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
import tempfile
import unittest
from pathlib import Path

from calico_capture.archive import synchronize_verified_transaction
from calico_capture.restore import RestoreError, restore_verified_transaction
from calico_landing.admission import admit
from calico_landing.result import AdmissionResult
from tests.capture.fakes import FakeArchive
from tests.fixtures.landing.fixture_builder import mutated_candidate


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
            archive.set_read_override(manifest_key, b"not-json-at-all")
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
            archive.set_read_override(manifest_key, json.dumps(document).encode("utf-8"))
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
            archive.set_read_override(manifest_key, json.dumps(document).encode("utf-8"))
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
            archive.set_read_override(manifest_key, json.dumps(document).encode("utf-8"))
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
            archive.set_read_override(manifest_key, json.dumps(document).encode("utf-8"))
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
