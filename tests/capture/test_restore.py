"""Manifest-driven, path-safe archive restore matrix (06-03-PLAN.md Task 2).

Exercises `calico_capture.restore.restore_verified_transaction` against a
real admitted-store transaction synchronized into an in-memory
`tests.capture.fakes.FakeArchive` (mirroring
`tests.capture.test_archive._admit_baseline_into_fresh_store`), proving:
a complete transaction restores every manifest object and the promotion
snapshot to a fresh root and passes that exact root to an injected
real-build spy; malformed/unknown manifest, identity mismatch, missing
object, hash mismatch, absolute/traversal/symlink keys, duplicate
destination, and an incomplete transaction all expose no usable store; and
repeated restoration into an already-populated destination is a true
byte-identical no-op, while a genuinely conflicting pre-existing file
fails closed. No live source, private archive, or real dbt subprocess is
ever contacted.
"""

from __future__ import annotations

import json
import hashlib
from unittest.mock import patch
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

from calico_capture.archive import synchronize_verified_transaction
from calico_capture.restore import (
    RestoreError,
    restore_latest_known_transaction,
    restore_verified_transaction,
)
from calico_landing.admission import admit
from calico_landing.result import AdmissionResult
from tests.capture.fakes import FakeArchive
from tests.fixtures.landing.fixture_builder import mutated_candidate


def seed_policy_for_archive(archive, store_root):
    """Seed only identity-free policy bytes and return the trusted binding."""
    from calico_capture.private_policy import canonical_json, seed_private_policy_bundle
    from tests.capture.test_private_policy import publication_document, sidecar_document, COMMIT
    root = Path(store_root)
    (root / "public-eligibility-v1.json").write_bytes(canonical_json(sidecar_document()))
    with tempfile.TemporaryDirectory() as temporary:
        public = Path(temporary).resolve() / "publication.json"
        public.write_bytes(canonical_json(publication_document()))
        result = seed_private_policy_bundle(archive, root, published_manifest_path=public,
                                            published_data_commit=COMMIT)
    return (result["prior_publication_manifest_sha256"], COMMIT)


def archived_catalog_fixture(root):
    """Three admitted releases in a fake archive, never an owner store."""
    from calico_dbt.catalog import build_catalog_from_manifests
    from calico_landing.contracts import LOGICAL_LIST_ORDER
    from tests.fixtures.landing.fixture_builder import AS_OF_DATE_COLUMN
    root = Path(root)
    archive = FakeArchive()
    manifests = []
    for date in ("2020-01-15", "2020-02-05", "2020-02-19"):
        with mutated_candidate() as candidate:
            for name in LOGICAL_LIST_ORDER:
                rows = candidate.csv_path(name).read_bytes().splitlines()[1:]
                for index in range(len(rows)):
                    candidate.replace_field(name, index, AS_OF_DATE_COLUMN, date)
            result = admit(candidate.root, root)
        if result.status != "accepted":
            raise AssertionError("identity-free archive fixture was not admitted")
        synchronize_verified_transaction(archive, root, result)
        manifest = root / "releases" / date / f"rev-0001-{result.revision_fingerprint[:8]}" / "manifest.json"
        manifests.append((date, 1, result.revision_fingerprint, manifest.read_bytes()))
    catalog = build_catalog_from_manifests(manifests)
    return archive, catalog, seed_policy_for_archive(archive, root)


class CatalogPrivatePolicyRestoreTests(unittest.TestCase):
    def test_shared_restore_boundary_exists(self):
        from calico_capture import restore
        self.assertTrue(callable(getattr(restore, "restore_catalog_with_private_policy", None)),
                        "complete catalog and private policy need one production restore boundary")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.archive, self.catalog, self.binding = archived_catalog_fixture(self.source)
        self.destination = self.root / "destination"
        self.destination.mkdir()

    def restore(self, **kwargs):
        from calico_capture.restore import restore_catalog_with_private_policy
        return restore_catalog_with_private_policy(self.archive, self.destination,
                    catalog=self.catalog, binding_loader=lambda: self.binding, **kwargs)

    def test_three_releases_and_exact_policy_are_installed_idempotently(self):
        outcome = self.restore()
        self.assertEqual(outcome.restored_transaction_count, 3)
        self.assertEqual(outcome.release_manifest_sha256s,
                         tuple(anchor.revision_manifest_sha256 for anchor in self.catalog.releases))
        self.assertEqual(outcome.object_count, 12)
        self.assertTrue((self.destination / "public-eligibility-v1.json").read_bytes() ==
                        (self.source / "public-eligibility-v1.json").read_bytes())
        self.assertTrue((self.destination / "promoted-releases.json").read_bytes() ==
                        (self.source / "promoted-releases.json").read_bytes())
        self.assertEqual(self.restore(), outcome)
        document = outcome.private_policy.to_dict()
        self.assertEqual(set(document), {"expected_sha256", "actual_sha256", "classification_version",
                         "prior_publication_manifest_sha256", "prior_publication_commit", "readback_verified"})
        self.assertIs(document["readback_verified"], True)
        self.assertNotIn("synthetic-private-key", json.dumps(document))
        self.assertNotIn(str(self.destination), json.dumps(document))

    def test_policy_manifest_and_object_failures_leave_destination_empty(self):
        from calico_capture.private_policy import private_policy_manifest_key, private_policy_object_key, canonical_json
        manifest_key = private_policy_manifest_key(self.binding[0])
        original = json.loads(self.archive.get_object(manifest_key))
        object_key = original["objects"][0]
        original_object = self.archive.get_object(object_key)
        cases = [
            ("missing_manifest", "preflight.public_eligibility_missing"),
            ("missing_object", "preflight.public_eligibility_missing"),
            ("duplicate_manifest", "preflight.public_eligibility_invalid"),
            ("duplicate_object", "preflight.public_eligibility_invalid"),
            ("hidden", "preflight.public_eligibility_invalid"),
            ("unfinished", "preflight.public_eligibility_invalid"),
            ("transport", "preflight.public_eligibility_invalid"),
            ("sidecar_sha256", "preflight.public_eligibility_invalid"),
            ("sidecar_length_bytes", "preflight.public_eligibility_invalid"),
            ("classification_version", "preflight.public_eligibility_invalid"),
            ("prior_publication_commit", "preflight.public_eligibility_invalid"),
            ("prior_publication_manifest_sha256", "preflight.public_eligibility_invalid"),
            ("extra", "preflight.public_eligibility_invalid"),
            ("objects", "preflight.public_eligibility_invalid"),
        ]
        for kind, category in cases:
            with self.subTest(kind=kind):
                archive = FakeArchive()
                for key in self.archive.all_keys():
                    if key not in (manifest_key, object_key):
                        archive.put_object(key, self.archive.get_object(key))
                document = dict(original)
                if kind in ("sidecar_sha256", "prior_publication_manifest_sha256"):
                    document[kind] = "d" * 64
                    if kind == "sidecar_sha256":
                        document["objects"] = [private_policy_object_key(document[kind])]
                        archive.put_object(document["objects"][0], original_object)
                elif kind == "sidecar_length_bytes":
                    document[kind] += 1
                elif kind == "classification_version":
                    document[kind] = "synthetic-stale-version"
                elif kind == "prior_publication_commit":
                    document[kind] = "d" * 40
                elif kind == "extra":
                    document[kind] = "private-sentinel"
                elif kind == "objects":
                    document[kind] = ["archive/v1/out-of-scope/private-sentinel.json"]
                if kind != "missing_object":
                    archive.put_object(object_key, original_object)
                if kind != "missing_manifest":
                    archive.put_object(manifest_key, canonical_json(document))
                if kind.startswith("duplicate"):
                    key = manifest_key if kind.endswith("manifest") else object_key
                    archive.inject_colliding_version(key, archive.get_object(key))
                if kind in ("hidden", "unfinished"):
                    archive._versions[object_key][0].action = "hide" if kind == "hidden" else "start"
                if kind == "transport":
                    archive.set_read_override(object_key, b"private-sentinel")
                with patch.object(self, "archive", archive):
                    with self.assertRaises(RestoreError) as caught:
                        self.restore()
                self.assertEqual(str(caught.exception), category)
                self.assertEqual(list(self.destination.iterdir()), [])

    def test_stale_attested_version_is_rejected(self):
        with self.assertRaisesRegex(RestoreError, "^preflight.public_eligibility_invalid$"):
            self.restore(expected_classification_version="synthetic-prior-version")
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_catalog_anchor_tampering_fails_before_any_materialization(self):
        self.catalog = replace(self.catalog, releases=(replace(self.catalog.releases[0],
                    revision_manifest_sha256="d" * 64), *self.catalog.releases[1:]))
        with self.assertRaisesRegex(RestoreError, "^restore.manifest_verification_failed$"):
            self.restore()
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_conflicting_sidecar_is_preserved_without_partial_catalog(self):
        sidecar = self.destination / "public-eligibility-v1.json"
        sidecar.write_bytes(b"existing-private-sentinel")
        with self.assertRaisesRegex(RestoreError, "^preflight.public_eligibility_invalid$"):
            self.restore()
        self.assertTrue(sidecar.read_bytes() == b"existing-private-sentinel")
        self.assertEqual(len(list(self.destination.iterdir())), 1)

    def test_sidecar_link_and_root_alias_are_rejected(self):
        sidecar = self.destination / "public-eligibility-v1.json"
        sidecar.symlink_to(self.source / sidecar.name)
        with self.assertRaisesRegex(RestoreError, "^preflight.public_eligibility_invalid$"):
            self.restore()
        sidecar.unlink()
        alias = self.root / "alias"
        alias.symlink_to(self.destination, target_is_directory=True)
        with patch.object(self, "destination", alias):
            with self.assertRaisesRegex(RestoreError, "^preflight.public_eligibility_invalid$"):
                self.restore()
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_reparse_component_is_rejected_before_archive_reads_or_writes(self):
        from types import SimpleNamespace
        import os
        import stat
        original = os.lstat
        def reparse(path, *args, **kwargs):
            metadata = original(path, *args, **kwargs)
            if Path(path) == self.destination:
                return SimpleNamespace(st_mode=metadata.st_mode, st_file_attributes=0x400)
            return metadata
        with patch.object(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, create=True), \
             patch("calico_capture.restore.os.lstat", side_effect=reparse):
            with self.assertRaisesRegex(RestoreError, "^preflight.public_eligibility_invalid$"):
                self.restore()
        self.assertEqual(list(self.destination.iterdir()), [])


def _admit_baseline_into_fresh_store() -> "tuple[Path, AdmissionResult, tempfile.TemporaryDirectory]":
    """Admit the committed identity-free baseline candidate into a brand
    new external temporary store and return the resolved store root, the
    resulting `accepted` result, and the owning `TemporaryDirectory`
    (mirrors `tests.capture.test_archive`'s own helper exactly)."""

    store_tmp = tempfile.TemporaryDirectory(prefix="calico-restore-test-store-")
    store_root = Path(store_tmp.name).resolve()
    with mutated_candidate() as candidate:
        result = admit(candidate.root, store_root)
    assert result.status == "accepted", result.status
    return store_root, result, store_tmp


def _synchronized_archive_and_result() -> "tuple[FakeArchive, AdmissionResult, tempfile.TemporaryDirectory]":
    store_root, result, store_tmp = _admit_baseline_into_fresh_store()
    archive = FakeArchive()
    synchronize_verified_transaction(archive, store_root, result)
    return archive, result, store_tmp


class _BuildSpy:
    """Matches `calico_dbt.runner.build(mode="real", store=...)`'s call
    site exactly (mirrors `tests.capture.test_tracer._BuildSpy`)."""

    def __init__(self, *, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.calls: list[object] = []

    def __call__(self, store_root: object) -> "_BuildSpy":
        self.calls.append(store_root)
        return self

    @property
    def succeeded(self) -> bool:
        return self.succeeds


class CompleteRestoreTests(unittest.TestCase):
    def test_complete_transaction_restores_every_object_and_promotion_state(self) -> None:
        archive, result, store_tmp = _synchronized_archive_and_result()
        try:
            with tempfile.TemporaryDirectory(prefix="calico-restore-dest-") as dest_name:
                destination_root = Path(dest_name)
                build_spy = _BuildSpy(succeeds=True)

                outcome = restore_verified_transaction(
                    archive,
                    destination_root,
                    as_of_date=result.as_of_date,
                    release_revision=result.release_revision,
                    revision_fingerprint=result.revision_fingerprint,
                    build=build_spy,
                )

                self.assertEqual(outcome.as_of_date, result.as_of_date)
                self.assertEqual(outcome.release_revision, result.release_revision)
                self.assertGreater(len(outcome.object_keys), 0)

                revision_dir = (
                    destination_root
                    / "releases"
                    / result.as_of_date
                    / f"rev-{result.release_revision:04d}-{result.revision_fingerprint[:8]}"
                )
                self.assertTrue((revision_dir / "manifest.json").is_file())
                self.assertTrue((destination_root / "promoted-releases.json").is_file())

                # Every restored file is byte-identical to its original.
                store_root = Path(store_tmp.name).resolve()
                original_revision_dir = (
                    store_root
                    / "releases"
                    / result.as_of_date
                    / f"rev-{result.release_revision:04d}-{result.revision_fingerprint[:8]}"
                )
                for original_path in original_revision_dir.rglob("*"):
                    if not original_path.is_file():
                        continue
                    relative = original_path.relative_to(store_root)
                    restored_path = destination_root / relative
                    self.assertEqual(restored_path.read_bytes(), original_path.read_bytes())

                self.assertEqual(
                    (destination_root / "promoted-releases.json").read_bytes(),
                    (store_root / "promoted-releases.json").read_bytes(),
                )

                # The real-build boundary was invoked exactly once, with the
                # exact destination root -- never a raw dbt subprocess.
                self.assertEqual(build_spy.calls, [destination_root.resolve()])
        finally:
            store_tmp.cleanup()

    def test_repeated_restore_is_byte_identical_and_idempotent(self) -> None:
        archive, result, store_tmp = _synchronized_archive_and_result()
        try:
            with tempfile.TemporaryDirectory(prefix="calico-restore-dest-") as dest_name:
                destination_root = Path(dest_name)

                first = restore_verified_transaction(
                    archive,
                    destination_root,
                    as_of_date=result.as_of_date,
                    release_revision=result.release_revision,
                    revision_fingerprint=result.revision_fingerprint,
                    build=_BuildSpy(succeeds=True),
                )
                before = {
                    path: path.read_bytes()
                    for path in destination_root.rglob("*")
                    if path.is_file()
                }

                second = restore_verified_transaction(
                    archive,
                    destination_root,
                    as_of_date=result.as_of_date,
                    release_revision=result.release_revision,
                    revision_fingerprint=result.revision_fingerprint,
                    build=_BuildSpy(succeeds=True),
                )
                after = {
                    path: path.read_bytes()
                    for path in destination_root.rglob("*")
                    if path.is_file()
                }

                self.assertEqual(first.object_keys, second.object_keys)
                self.assertEqual(before, after)
        finally:
            store_tmp.cleanup()


class RestoreLatestKnownTransactionTests(unittest.TestCase):
    """CR-01 fix (2026-09-03 code review): the real production restore-
    before-capture boundary discovers and restores the single most
    recently archived transaction via the fixed discovery pointer, rather
    than requiring a caller to already know its identity."""

    def test_no_prior_transaction_returns_none_and_establishes_empty_layout(self) -> None:
        archive = FakeArchive()
        with tempfile.TemporaryDirectory(prefix="calico-restore-dest-") as dest_name:
            destination_root = Path(dest_name)

            outcome = restore_latest_known_transaction(archive, destination_root)

            self.assertIsNone(outcome)
            self.assertTrue((destination_root / "releases").is_dir())
            self.assertTrue((destination_root / ".staging").is_dir())
            self.assertFalse((destination_root / "promoted-releases.json").exists())

    def test_prior_transaction_is_discovered_and_restored(self) -> None:
        archive, result, store_tmp = _synchronized_archive_and_result()
        try:
            with tempfile.TemporaryDirectory(prefix="calico-restore-dest-") as dest_name:
                destination_root = Path(dest_name)
                build_spy = _BuildSpy(succeeds=True)

                outcome = restore_latest_known_transaction(
                    archive, destination_root, build=build_spy
                )

                self.assertIsNotNone(outcome)
                self.assertEqual(outcome.as_of_date, result.as_of_date)
                self.assertEqual(outcome.release_revision, result.release_revision)
                revision_dir = (
                    destination_root
                    / "releases"
                    / result.as_of_date
                    / f"rev-{result.release_revision:04d}-{result.revision_fingerprint[:8]}"
                )
                self.assertTrue((revision_dir / "manifest.json").is_file())
                self.assertTrue((destination_root / "promoted-releases.json").is_file())
                self.assertEqual(build_spy.calls, [destination_root.resolve()])
        finally:
            store_tmp.cleanup()

    def test_malformed_pointer_fails_closed_with_a_dedicated_category(self) -> None:
        archive = FakeArchive()
        archive.put_object(
            "archive/v1/latest-transaction-pointer.json", b'{"not": "a valid pointer"}'
        )
        with tempfile.TemporaryDirectory(prefix="calico-restore-dest-") as dest_name:
            with self.assertRaises(RestoreError) as ctx:
                restore_latest_known_transaction(archive, Path(dest_name))
            self.assertEqual(ctx.exception.category, "restore.latest_pointer_read_failed")


class MalformedAndUnknownManifestTests(unittest.TestCase):
    def test_unknown_transaction_fails_closed(self) -> None:
        archive = FakeArchive()
        with tempfile.TemporaryDirectory(prefix="calico-restore-dest-") as dest_name:
            with self.assertRaises(RestoreError) as ctx:
                restore_verified_transaction(
                    archive,
                    Path(dest_name),
                    as_of_date="2020-01-15",
                    release_revision=1,
                    revision_fingerprint="0" * 64,
                    build=_BuildSpy(),
                )
            self.assertEqual(ctx.exception.category, "restore.transaction_not_found")

    def test_malformed_manifest_json_fails_closed(self) -> None:
        archive, result, store_tmp = _synchronized_archive_and_result()
        try:
            manifest_key = (
                f"archive/v1/transactions/{result.as_of_date}-rev-"
                f"{result.release_revision:04d}-{result.revision_fingerprint[:8]}/"
                "archive-transaction.json"
            )
            archive._versions[manifest_key][0].data = b"not-json-at-all"
            with tempfile.TemporaryDirectory(prefix="calico-restore-dest-") as dest_name:
                with self.assertRaises(RestoreError) as ctx:
                    restore_verified_transaction(
                        archive,
                        Path(dest_name),
                        as_of_date=result.as_of_date,
                        release_revision=result.release_revision,
                        revision_fingerprint=result.revision_fingerprint,
                        build=_BuildSpy(),
                    )
                self.assertEqual(ctx.exception.category, "restore.malformed_transaction_manifest")
        finally:
            store_tmp.cleanup()

    def test_manifest_with_unknown_extra_key_fails_closed(self) -> None:
        archive, result, store_tmp = _synchronized_archive_and_result()
        try:
            manifest_key = (
                f"archive/v1/transactions/{result.as_of_date}-rev-"
                f"{result.release_revision:04d}-{result.revision_fingerprint[:8]}/"
                "archive-transaction.json"
            )
            document = json.loads(archive.get_object(manifest_key).decode("utf-8"))
            document["unexpected_extra_field"] = "unexpected"
            archive._versions[manifest_key][0].data = json.dumps(document).encode("utf-8")
            with tempfile.TemporaryDirectory(prefix="calico-restore-dest-") as dest_name:
                with self.assertRaises(RestoreError) as ctx:
                    restore_verified_transaction(
                        archive,
                        Path(dest_name),
                        as_of_date=result.as_of_date,
                        release_revision=result.release_revision,
                        revision_fingerprint=result.revision_fingerprint,
                        build=_BuildSpy(),
                    )
                self.assertEqual(ctx.exception.category, "restore.malformed_transaction_manifest")
        finally:
            store_tmp.cleanup()

    def test_identity_mismatch_fails_closed(self) -> None:
        archive, result, store_tmp = _synchronized_archive_and_result()
        try:
            manifest_key = (
                f"archive/v1/transactions/{result.as_of_date}-rev-"
                f"{result.release_revision:04d}-{result.revision_fingerprint[:8]}/"
                "archive-transaction.json"
            )
            document = json.loads(archive.get_object(manifest_key).decode("utf-8"))
            document["release_revision"] = document["release_revision"] + 1
            archive._versions[manifest_key][0].data = json.dumps(document).encode("utf-8")
            with tempfile.TemporaryDirectory(prefix="calico-restore-dest-") as dest_name:
                with self.assertRaises(RestoreError) as ctx:
                    restore_verified_transaction(
                        archive,
                        Path(dest_name),
                        as_of_date=result.as_of_date,
                        release_revision=result.release_revision,
                        revision_fingerprint=result.revision_fingerprint,
                        build=_BuildSpy(),
                    )
                self.assertEqual(ctx.exception.category, "restore.transaction_identity_mismatch")
        finally:
            store_tmp.cleanup()


class TamperingAndContainmentTests(unittest.TestCase):
    def test_missing_content_object_fails_closed_as_incomplete_transaction(self) -> None:
        archive, result, store_tmp = _synchronized_archive_and_result()
        try:
            manifest_key = (
                f"archive/v1/transactions/{result.as_of_date}-rev-"
                f"{result.release_revision:04d}-{result.revision_fingerprint[:8]}/"
                "archive-transaction.json"
            )
            document = json.loads(archive.get_object(manifest_key).decode("utf-8"))
            missing_key = document["object_keys"][0]
            archive.fail_read(missing_key)
            with tempfile.TemporaryDirectory(prefix="calico-restore-dest-") as dest_name:
                with self.assertRaises(RestoreError) as ctx:
                    restore_verified_transaction(
                        archive,
                        Path(dest_name),
                        as_of_date=result.as_of_date,
                        release_revision=result.release_revision,
                        revision_fingerprint=result.revision_fingerprint,
                        build=_BuildSpy(),
                    )
                self.assertEqual(ctx.exception.category, "restore.incomplete_transaction")
        finally:
            store_tmp.cleanup()

    def test_object_hash_mismatch_fails_closed(self) -> None:
        archive, result, store_tmp = _synchronized_archive_and_result()
        try:
            manifest_key = (
                f"archive/v1/transactions/{result.as_of_date}-rev-"
                f"{result.release_revision:04d}-{result.revision_fingerprint[:8]}/"
                "archive-transaction.json"
            )
            document = json.loads(archive.get_object(manifest_key).decode("utf-8"))
            tampered_key = document["object_keys"][0]
            archive.set_read_override(tampered_key, b"tampered-bytes-not-matching-hash")
            with tempfile.TemporaryDirectory(prefix="calico-restore-dest-") as dest_name:
                with self.assertRaises(RestoreError) as ctx:
                    restore_verified_transaction(
                        archive,
                        Path(dest_name),
                        as_of_date=result.as_of_date,
                        release_revision=result.release_revision,
                        revision_fingerprint=result.revision_fingerprint,
                        build=_BuildSpy(),
                    )
                self.assertEqual(ctx.exception.category, "restore.object_hash_mismatch")

                # No usable partial store was exposed: the destination
                # root has no restored release directory at all.
                self.assertFalse((Path(dest_name) / "releases" / result.as_of_date).exists())
        finally:
            store_tmp.cleanup()

    def test_promotion_snapshot_hash_mismatch_fails_closed(self) -> None:
        archive, result, store_tmp = _synchronized_archive_and_result()
        try:
            manifest_key = (
                f"archive/v1/transactions/{result.as_of_date}-rev-"
                f"{result.release_revision:04d}-{result.revision_fingerprint[:8]}/"
                "archive-transaction.json"
            )
            document = json.loads(archive.get_object(manifest_key).decode("utf-8"))
            promotion_key = document["promotion_snapshot_key"]
            archive.set_read_override(promotion_key, b"tampered-promotion-bytes")
            with tempfile.TemporaryDirectory(prefix="calico-restore-dest-") as dest_name:
                with self.assertRaises(RestoreError) as ctx:
                    restore_verified_transaction(
                        archive,
                        Path(dest_name),
                        as_of_date=result.as_of_date,
                        release_revision=result.release_revision,
                        revision_fingerprint=result.revision_fingerprint,
                        build=_BuildSpy(),
                    )
                self.assertEqual(ctx.exception.category, "restore.object_hash_mismatch")
        finally:
            store_tmp.cleanup()

    def test_traversal_key_fails_closed(self) -> None:
        archive, result, store_tmp = _synchronized_archive_and_result()
        try:
            manifest_key = (
                f"archive/v1/transactions/{result.as_of_date}-rev-"
                f"{result.release_revision:04d}-{result.revision_fingerprint[:8]}/"
                "archive-transaction.json"
            )
            document = json.loads(archive.get_object(manifest_key).decode("utf-8"))
            original_key = document["object_keys"][0]
            digest = document["object_sha256"].pop(original_key)
            malicious_key = "archive/v1/store/releases/../../etc/passwd"
            document["object_keys"][0] = malicious_key
            document["object_sha256"][malicious_key] = digest
            document["object_keys"] = sorted(document["object_keys"])
            archive._versions[manifest_key][0].data = json.dumps(document).encode("utf-8")
            with tempfile.TemporaryDirectory(prefix="calico-restore-dest-") as dest_name:
                with self.assertRaises(RestoreError) as ctx:
                    restore_verified_transaction(
                        archive,
                        Path(dest_name),
                        as_of_date=result.as_of_date,
                        release_revision=result.release_revision,
                        revision_fingerprint=result.revision_fingerprint,
                        build=_BuildSpy(),
                    )
                self.assertEqual(ctx.exception.category, "restore.invalid_object_key")
        finally:
            store_tmp.cleanup()

    def test_symlink_at_destination_component_fails_closed(self) -> None:
        archive, result, store_tmp = _synchronized_archive_and_result()
        try:
            with tempfile.TemporaryDirectory(prefix="calico-restore-dest-") as dest_name:
                destination_root = Path(dest_name)
                releases_link_target = destination_root / "real-releases-target"
                releases_link_target.mkdir()
                try:
                    (destination_root / "releases").symlink_to(
                        releases_link_target, target_is_directory=True
                    )
                except (OSError, NotImplementedError):
                    self.skipTest("symlink creation not permitted in this environment")

                with self.assertRaises(RestoreError) as ctx:
                    restore_verified_transaction(
                        archive,
                        destination_root,
                        as_of_date=result.as_of_date,
                        release_revision=result.release_revision,
                        revision_fingerprint=result.revision_fingerprint,
                        build=_BuildSpy(),
                    )
                self.assertEqual(ctx.exception.category, "restore.invalid_object_key")
        finally:
            store_tmp.cleanup()

    def test_duplicate_destination_fails_closed(self) -> None:
        archive, result, store_tmp = _synchronized_archive_and_result()
        try:
            manifest_key = (
                f"archive/v1/transactions/{result.as_of_date}-rev-"
                f"{result.release_revision:04d}-{result.revision_fingerprint[:8]}/"
                "archive-transaction.json"
            )
            document = json.loads(archive.get_object(manifest_key).decode("utf-8"))
            original_key = document["object_keys"][0]
            digest = document["object_sha256"][original_key]
            # Insert a redundant "." path segment just before the final
            # component -- still matches the closed object-key pattern
            # (archive/v1/store/(releases|attempts)/...) but normalizes to
            # the exact same destination path as `original_key`.
            key_parts = original_key.split("/")
            key_parts.insert(-1, ".")
            duplicate_key = "/".join(key_parts)
            document["object_keys"].append(duplicate_key)
            document["object_keys"] = sorted(document["object_keys"])
            document["object_sha256"][duplicate_key] = digest
            archive._versions[manifest_key][0].data = json.dumps(document).encode("utf-8")
            archive.put_object(duplicate_key, archive.get_object(original_key))
            with tempfile.TemporaryDirectory(prefix="calico-restore-dest-") as dest_name:
                with self.assertRaises(RestoreError) as ctx:
                    restore_verified_transaction(
                        archive,
                        Path(dest_name),
                        as_of_date=result.as_of_date,
                        release_revision=result.release_revision,
                        revision_fingerprint=result.revision_fingerprint,
                        build=_BuildSpy(),
                    )
                self.assertEqual(ctx.exception.category, "restore.duplicate_destination")
        finally:
            store_tmp.cleanup()

    def test_pre_existing_conflicting_file_fails_closed(self) -> None:
        archive, result, store_tmp = _synchronized_archive_and_result()
        try:
            with tempfile.TemporaryDirectory(prefix="calico-restore-dest-") as dest_name:
                destination_root = Path(dest_name)
                conflict_dir = (
                    destination_root
                    / "releases"
                    / result.as_of_date
                    / f"rev-{result.release_revision:04d}-{result.revision_fingerprint[:8]}"
                )
                conflict_dir.mkdir(parents=True)
                (conflict_dir / "manifest.json").write_bytes(b"pre-existing-different-bytes")

                with self.assertRaises(RestoreError) as ctx:
                    restore_verified_transaction(
                        archive,
                        destination_root,
                        as_of_date=result.as_of_date,
                        release_revision=result.release_revision,
                        revision_fingerprint=result.revision_fingerprint,
                        build=_BuildSpy(),
                    )
                self.assertEqual(ctx.exception.category, "restore.pre_existing_conflict")
        finally:
            store_tmp.cleanup()

    def test_build_failure_fails_closed(self) -> None:
        archive, result, store_tmp = _synchronized_archive_and_result()
        try:
            with tempfile.TemporaryDirectory(prefix="calico-restore-dest-") as dest_name:
                with self.assertRaises(RestoreError) as ctx:
                    restore_verified_transaction(
                        archive,
                        Path(dest_name),
                        as_of_date=result.as_of_date,
                        release_revision=result.release_revision,
                        revision_fingerprint=result.revision_fingerprint,
                        build=_BuildSpy(succeeds=False),
                    )
                self.assertEqual(ctx.exception.category, "restore.build_failed")
        finally:
            store_tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
