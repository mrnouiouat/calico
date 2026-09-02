"""Closed immutable archive schema and collision/interruption matrix
(06-01-PLAN.md Task 2).

Exercises `calico_capture.archive.synchronize_verified_transaction` against
the real admitted-store layout `calico_landing.admission.admit()` produces
(via the committed identity-free fixture) and the in-memory
`tests.capture.fakes.FakeArchive` double -- proving upload ordering,
byte-idempotent replay, deterministic collision/ambiguity refusal, stable
key ordering, and manifest-last completeness without ever contacting a
live source or private archive.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from calico_capture import archive as archive_module
from calico_capture.archive import ArchiveError, synchronize_verified_transaction
from calico_landing.admission import admit
from calico_landing.result import AdmissionResult
from tests.capture.fakes import FakeArchive
from tests.fixtures.landing.fixture_builder import mutated_candidate


def _admit_baseline_into_fresh_store() -> tuple[Path, AdmissionResult, tempfile.TemporaryDirectory]:
    """Admit the committed identity-free baseline candidate into a brand
    new external temporary store (outside any Git worktree) and return the
    resolved store root, the resulting `accepted` result, and the owning
    `TemporaryDirectory` (kept alive by the caller so the store is not
    removed before assertions run).

    This module tests the archive layer in isolation, so it admits without
    opting into the closed status-vocabulary contract
    (`calico_capture.orchestrator.capture()` is what opts in, per
    `tests.capture.test_tracer`) -- the committed baseline fixture predates
    that vocabulary and is otherwise structurally valid.
    """

    store_tmp = tempfile.TemporaryDirectory(prefix="calico-archive-test-store-")
    store_root = Path(store_tmp.name).resolve()
    with mutated_candidate() as candidate:
        result = admit(candidate.root, store_root)
    assert result.status == "accepted", result.status
    return store_root, result, store_tmp


class SynchronizeTransactionTests(unittest.TestCase):
    def test_absent_keys_upload_in_stable_lexical_order(self) -> None:
        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            archive = FakeArchive()
            transaction = synchronize_verified_transaction(archive, store_root, result)

            self.assertEqual(list(transaction.object_keys), sorted(transaction.object_keys))
            self.assertTrue(all(key.startswith("archive/v1/store/releases/") for key in transaction.object_keys))
            # Every content key resolved to exactly one uploaded version.
            for key in transaction.object_keys:
                self.assertEqual(archive.version_count(key), 1)
        finally:
            store_tmp.cleanup()

    def test_promotion_snapshot_follows_content_and_manifest_is_last(self) -> None:
        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            archive = FakeArchive()
            transaction = synchronize_verified_transaction(archive, store_root, result)

            # FakeArchive assigns strictly increasing version ids in write
            # order across every key -- reading them back proves the
            # required content-then-promotion-then-manifest sequence
            # without needing a separate call-order spy.
            content_write_orders = [
                int(archive.list_versions(key)[0].version_id[1:]) for key in transaction.object_keys
            ]
            promotion_key = next(
                key
                for key in archive.all_keys()
                if key.startswith("archive/v1/transactions/") and key.endswith("promoted-releases.json")
            )
            manifest_write_order = int(archive.list_versions(transaction.manifest_key)[0].version_id[1:])
            promotion_write_order = int(archive.list_versions(promotion_key)[0].version_id[1:])

            self.assertTrue(max(content_write_orders) < promotion_write_order < manifest_write_order)
        finally:
            store_tmp.cleanup()

    def test_transaction_manifest_is_read_back_and_schema_verified(self) -> None:
        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            archive = FakeArchive()
            transaction = synchronize_verified_transaction(archive, store_root, result)

            manifest_bytes = archive.get_object(transaction.manifest_key)
            document = json.loads(manifest_bytes.decode("utf-8"))
            self.assertEqual(document["transaction_id"], transaction.transaction_id)
            self.assertEqual(document["object_keys"], sorted(document["object_keys"]))
            self.assertEqual(set(document["object_sha256"].keys()), set(document["object_keys"]))
        finally:
            store_tmp.cleanup()

    def test_byte_identical_replay_is_an_idempotent_no_op(self) -> None:
        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            archive = FakeArchive()
            first = synchronize_verified_transaction(archive, store_root, result)
            second = synchronize_verified_transaction(archive, store_root, result)

            self.assertEqual(first, second)
            for key in first.object_keys + (first.manifest_key,):
                self.assertEqual(archive.version_count(key), 1)
        finally:
            store_tmp.cleanup()

    def test_different_bytes_at_existing_key_is_a_deterministic_collision(self) -> None:
        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            archive = FakeArchive()
            manifest_path = (
                store_root
                / "releases"
                / result.as_of_date
                / f"rev-{result.release_revision:04d}-{result.revision_fingerprint[:8]}"
                / "manifest.json"
            )
            relative = manifest_path.relative_to(store_root).as_posix()
            colliding_key = f"archive/v1/store/{relative}"
            archive.inject_colliding_version(colliding_key, b"different-bytes-than-the-real-manifest")

            with self.assertRaises(ArchiveError) as ctx:
                synchronize_verified_transaction(archive, store_root, result)
            self.assertEqual(ctx.exception.category, "archive.collision")
        finally:
            store_tmp.cleanup()

    def test_duplicate_versions_at_existing_key_is_a_closed_ambiguity_error(self) -> None:
        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            archive = FakeArchive()
            manifest_path = (
                store_root
                / "releases"
                / result.as_of_date
                / f"rev-{result.release_revision:04d}-{result.revision_fingerprint[:8]}"
                / "manifest.json"
            )
            relative = manifest_path.relative_to(store_root).as_posix()
            key = f"archive/v1/store/{relative}"
            archive.inject_colliding_version(key, b"version-one")
            archive.inject_colliding_version(key, b"version-two")

            with self.assertRaises(ArchiveError) as ctx:
                synchronize_verified_transaction(archive, store_root, result)
            self.assertEqual(ctx.exception.category, "archive.version_ambiguous")
        finally:
            store_tmp.cleanup()

    def test_hide_marker_at_existing_key_is_a_closed_ambiguity_error(self) -> None:
        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            archive = FakeArchive()
            manifest_path = (
                store_root
                / "releases"
                / result.as_of_date
                / f"rev-{result.release_revision:04d}-{result.revision_fingerprint[:8]}"
                / "manifest.json"
            )
            relative = manifest_path.relative_to(store_root).as_posix()
            key = f"archive/v1/store/{relative}"
            archive.inject_colliding_version(key, b"hidden", action="hide")

            with self.assertRaises(ArchiveError) as ctx:
                synchronize_verified_transaction(archive, store_root, result)
            self.assertEqual(ctx.exception.category, "archive.version_ambiguous")
        finally:
            store_tmp.cleanup()

    def test_unfinished_upload_at_existing_key_is_a_closed_ambiguity_error(self) -> None:
        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            archive = FakeArchive()
            manifest_path = (
                store_root
                / "releases"
                / result.as_of_date
                / f"rev-{result.release_revision:04d}-{result.revision_fingerprint[:8]}"
                / "manifest.json"
            )
            relative = manifest_path.relative_to(store_root).as_posix()
            key = f"archive/v1/store/{relative}"
            archive.inject_colliding_version(key, b"partial", action="start")

            with self.assertRaises(ArchiveError) as ctx:
                synchronize_verified_transaction(archive, store_root, result)
            self.assertEqual(ctx.exception.category, "archive.version_ambiguous")
        finally:
            store_tmp.cleanup()

    def test_list_failure_is_a_closed_category(self) -> None:
        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            archive = FakeArchive()
            manifest_path = (
                store_root
                / "releases"
                / result.as_of_date
                / f"rev-{result.release_revision:04d}-{result.revision_fingerprint[:8]}"
                / "manifest.json"
            )
            relative = manifest_path.relative_to(store_root).as_posix()
            key = f"archive/v1/store/{relative}"
            archive.fail_list(key)

            with self.assertRaises(ArchiveError) as ctx:
                synchronize_verified_transaction(archive, store_root, result)
            self.assertEqual(ctx.exception.category, "archive.list_failed")
        finally:
            store_tmp.cleanup()

    def test_exhausted_bounded_write_transport_is_a_closed_category(self) -> None:
        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            archive = FakeArchive()
            archive.fail_all_writes()

            with self.assertRaises(ArchiveError) as ctx:
                synchronize_verified_transaction(archive, store_root, result)
            self.assertEqual(ctx.exception.category, "archive.write_failed")
        finally:
            store_tmp.cleanup()

    def test_content_readback_mismatch_is_a_closed_category(self) -> None:
        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            archive = FakeArchive()
            manifest_path = (
                store_root
                / "releases"
                / result.as_of_date
                / f"rev-{result.release_revision:04d}-{result.revision_fingerprint[:8]}"
                / "manifest.json"
            )
            relative = manifest_path.relative_to(store_root).as_posix()
            key = f"archive/v1/store/{relative}"
            archive.set_read_override(key, b"corrupted-in-transit")

            with self.assertRaises(ArchiveError) as ctx:
                synchronize_verified_transaction(archive, store_root, result)
            self.assertEqual(ctx.exception.category, "archive.readback_mismatch")
        finally:
            store_tmp.cleanup()

    def test_interrupted_transaction_leaves_no_authoritative_partial_transaction(self) -> None:
        """A failure between the promotion snapshot and the transaction
        manifest leaves content and the promotion snapshot uploaded, but no
        manifest key -- proving must_haves truth 3 (no authoritative
        partial transaction is ever exposed). Clearing the injected
        failure and resynchronizing then completes the transaction without
        re-uploading any already-verified content object.
        """

        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            archive = FakeArchive()
            expected_prefix = (
                f"archive/v1/transactions/{result.as_of_date}"
                f"-rev-{result.release_revision:04d}-{result.revision_fingerprint[:8]}"
            )
            manifest_key = f"{expected_prefix}/archive-transaction.json"
            archive.fail_write(manifest_key)

            with self.assertRaises(ArchiveError) as ctx:
                synchronize_verified_transaction(archive, store_root, result)
            self.assertEqual(ctx.exception.category, "archive.write_failed")

            keys_after_interruption = archive.all_keys()
            self.assertTrue(any(key.startswith("archive/v1/store/releases/") for key in keys_after_interruption))
            self.assertTrue(
                any(key.endswith("promoted-releases.json") for key in keys_after_interruption)
            )
            self.assertNotIn(manifest_key, keys_after_interruption)

            content_version_counts_before = {
                key: archive.version_count(key)
                for key in keys_after_interruption
            }

            archive.clear_write_failure(manifest_key)
            transaction = synchronize_verified_transaction(archive, store_root, result)

            self.assertEqual(transaction.manifest_key, manifest_key)
            self.assertIn(manifest_key, archive.all_keys())
            for key, count_before in content_version_counts_before.items():
                self.assertEqual(archive.version_count(key), count_before)
        finally:
            store_tmp.cleanup()

    def test_missing_or_empty_object_set_rejects_before_any_upload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="calico-archive-test-empty-") as tmp_name:
            store_root = Path(tmp_name).resolve()
            as_of_date = "2020-01-15"
            fingerprint = "a" * 64
            release_revision = 1
            revision_dir = store_root / "releases" / as_of_date / f"rev-{release_revision:04d}-{fingerprint[:8]}"
            revision_dir.mkdir(parents=True)  # deliberately empty: no manifest, no content

            result = AdmissionResult.accepted(as_of_date, release_revision, fingerprint)
            archive = FakeArchive()

            with self.assertRaises(ArchiveError) as ctx:
                synchronize_verified_transaction(archive, store_root, result)
            self.assertEqual(ctx.exception.category, "archive.empty_object_set")
            self.assertEqual(archive.all_keys(), ())

    def test_rejected_or_operational_error_result_is_never_synchronized(self) -> None:
        archive = FakeArchive()
        with tempfile.TemporaryDirectory(prefix="calico-archive-test-rejected-") as tmp_name:
            store_root = Path(tmp_name).resolve()
            for bad_result in (
                AdmissionResult.rejected(()),
                AdmissionResult.operational_error(()),
            ):
                with self.assertRaises(ArchiveError) as ctx:
                    synchronize_verified_transaction(archive, store_root, bad_result)
                self.assertEqual(ctx.exception.category, "archive.invalid_result_status")
        self.assertEqual(archive.all_keys(), ())


class TransactionManifestValidationTests(unittest.TestCase):
    """Direct closed-schema validation coverage for
    `archive._validate_transaction_manifest_document` -- the guard that
    protects every future reader of an archived transaction manifest
    (mirrors `contracts/private-archive-v1.schema.json`).
    """

    def _valid_document(self) -> dict:
        return {
            "schema_version": 1,
            "transaction_id": "2020-01-15-rev-0001-aabbccdd",
            "as_of_date": "2020-01-15",
            "release_revision": 1,
            "revision_fingerprint": "a" * 64,
            "object_keys": ["archive/v1/store/releases/2020-01-15/rev-0001-aabbccdd/manifest.json"],
            "object_sha256": {
                "archive/v1/store/releases/2020-01-15/rev-0001-aabbccdd/manifest.json": "b" * 64
            },
            "promotion_snapshot_key": (
                "archive/v1/transactions/2020-01-15-rev-0001-aabbccdd/promoted-releases.json"
            ),
            "promotion_snapshot_sha256": "c" * 64,
        }

    def test_valid_document_passes(self) -> None:
        archive_module._validate_transaction_manifest_document(self._valid_document())

    def test_unknown_top_level_key_is_rejected(self) -> None:
        document = self._valid_document()
        document["unexpected_extra_field"] = "value"
        with self.assertRaises(ArchiveError) as ctx:
            archive_module._validate_transaction_manifest_document(document)
        self.assertEqual(ctx.exception.category, "archive.malformed_transaction_manifest")

    def test_missing_required_key_is_rejected(self) -> None:
        document = self._valid_document()
        del document["promotion_snapshot_sha256"]
        with self.assertRaises(ArchiveError) as ctx:
            archive_module._validate_transaction_manifest_document(document)
        self.assertEqual(ctx.exception.category, "archive.malformed_transaction_manifest")

    def test_unsorted_object_keys_is_rejected(self) -> None:
        document = self._valid_document()
        document["object_keys"] = ["b", "a"]
        document["object_sha256"] = {"b": "b" * 64, "a": "a" * 64}
        with self.assertRaises(ArchiveError) as ctx:
            archive_module._validate_transaction_manifest_document(document)
        self.assertEqual(ctx.exception.category, "archive.malformed_transaction_manifest")

    def test_object_sha256_key_set_mismatch_is_rejected(self) -> None:
        document = self._valid_document()
        document["object_sha256"] = {"archive/v1/store/releases/unknown-key.json": "d" * 64}
        with self.assertRaises(ArchiveError) as ctx:
            archive_module._validate_transaction_manifest_document(document)
        self.assertEqual(ctx.exception.category, "archive.malformed_transaction_manifest")

    def test_wrong_schema_version_is_rejected(self) -> None:
        document = self._valid_document()
        document["schema_version"] = 2
        with self.assertRaises(ArchiveError) as ctx:
            archive_module._validate_transaction_manifest_document(document)
        self.assertEqual(ctx.exception.category, "archive.malformed_transaction_manifest")

    def test_non_dict_document_is_rejected(self) -> None:
        with self.assertRaises(ArchiveError) as ctx:
            archive_module._validate_transaction_manifest_document(["not", "a", "dict"])
        self.assertEqual(ctx.exception.category, "archive.malformed_transaction_manifest")


if __name__ == "__main__":
    unittest.main()
