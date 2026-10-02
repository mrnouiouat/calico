"""Immutable private policy identity, ordering and non-echo boundaries."""
from __future__ import annotations

import copy
import dataclasses
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from calico_capture import private_policy as policy
from calico_publish.allowlist import load_allowlist
from calico_publish.manifest import compute_revision_fingerprint
from calico_landing.contracts import LOGICAL_LIST_ORDER
from tests.capture.fakes import FakeArchive

COMMIT = "a" * 40


def publication_document():
    authority = load_allowlist(Path(__file__).resolve().parents[2] / "contracts/publication-exports-v3.json")
    return {
        "schema_version": 1, "allowlist_version": authority.allowlist_version,
        "parser_contract_version": "calico-registry-csv-v1",
        "toolchain": {name: "1.0.0" for name in ("python", "dbt_core", "dbt_duckdb", "duckdb")},
        "accepted_releases": [{"as_of_date": "2026-08-19", "release_revision": 1,
                               "revision_fingerprint": compute_revision_fingerprint({name: "b" * 64 for name in LOGICAL_LIST_ORDER}),
                               "source_objects": [{"source_list": name, "sha256": "b" * 64,
                                                   "byte_size": 1, "row_count": 1}
                                                  for name in sorted(LOGICAL_LIST_ORDER)]}],
        "eligible_key_count": 0,
        "exports": [{"export_name": entry.export_name, "file_name": entry.file_name,
                     "sha256": "c" * 64, "row_count": 0, "grain": list(entry.grain)}
                    for entry in sorted(authority.exports, key=lambda entry: entry.export_name)],
    }


def sidecar_document():
    return {"schema_version": 1, "classification_version": "synthetic-policy-v1",
            "classifications": [{"registration_number": "synthetic-private-key",
                                 "classification": "unclassified"}]}


class PrivatePolicyBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="calico-private-policy-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = self.root / "store"
        self.store.mkdir()
        self.sidecar = self.store / "public-eligibility-v1.json"
        self.sidecar.write_bytes((json.dumps(sidecar_document()) + "\n").encode())
        self.publication = self.root / "publication.json"
        self.publication.write_bytes(policy.canonical_json(publication_document()))
        self.archive = FakeArchive()

    def seed(self, archive=None, commit=COMMIT):
        return policy.seed_private_policy_bundle(
            self.archive if archive is None else archive, self.store,
            published_manifest_path=self.publication, published_data_commit=commit)

    def binding_hash(self):
        return hashlib.sha256(self.publication.read_bytes()).hexdigest()

    def test_exact_bytes_round_trip_with_closed_publication_binding(self):
        result = self.seed()
        self.assertEqual(set(result), {"category", "sidecar_sha256", "classification_version",
                                      "prior_publication_manifest_sha256", "prior_publication_commit",
                                      "readback_verified"})
        self.assertEqual(result["category"], "seed_policy.completed")
        self.assertIs(result["readback_verified"], True)
        manifest = policy.load_private_policy_manifest(
            self.archive, prior_publication_manifest_sha256=self.binding_hash(),
            prior_publication_commit=COMMIT, expected_classification_version="synthetic-policy-v1")
        self.assertEqual(set(manifest.to_dict()), {"schema_version", "classification_version", "sidecar_sha256",
                         "sidecar_length_bytes", "prior_publication_manifest_sha256", "prior_publication_commit", "objects"})
        key = policy.private_policy_object_key(result["sidecar_sha256"])
        self.assertTrue(self.archive.get_object(key) == self.sidecar.read_bytes())
        self.assertNotIn("synthetic-private-key", json.dumps(result))
        self.assertNotIn(str(self.store), json.dumps(result))

    def test_upload_readback_order_is_sidecar_then_manifest(self):
        operations = []
        class OrderedArchive(FakeArchive):
            def put_object(inner, key, data):
                operations.append(("put", key))
                return super().put_object(key, data)
            def get_object(inner, key, *, version_id=None):
                operations.append(("get", key))
                self.assertIsNotNone(version_id)
                return super().get_object(key, version_id=version_id)
        archive = OrderedArchive()
        self.seed(archive)
        object_key = policy.private_policy_object_key(hashlib.sha256(self.sidecar.read_bytes()).hexdigest())
        manifest_key = policy.private_policy_manifest_key(self.binding_hash())
        self.assertEqual(operations, [("put", object_key), ("get", object_key),
                                      ("put", manifest_key), ("get", manifest_key)])

    def test_identical_retry_verifies_bytes_without_creating_versions(self):
        first = self.seed()
        self.assertEqual(self.seed(), first)
        self.assertTrue(all(self.archive.version_count(key) == 1 for key in self.archive.all_keys()))
        key = policy.private_policy_object_key(first["sidecar_sha256"])
        self.archive.set_read_override(key, b"corrupted")
        with self.assertRaisesRegex(policy.PrivatePolicyError, "^private_policy.readback_mismatch$"):
            self.seed()

    def test_interruption_before_manifest_leaves_no_authority_and_retry_recovers(self):
        key = policy.private_policy_manifest_key(self.binding_hash())
        self.archive.fail_write(key)
        with self.assertRaises(policy.PrivatePolicyError):
            self.seed()
        self.assertEqual(self.archive.version_count(key), 0)
        self.archive.clear_write_failure(key)
        self.seed()
        self.assertTrue(all(self.archive.version_count(key) == 1 for key in self.archive.all_keys()))

    def test_collision_hidden_unfinished_and_duplicate_versions_stop_before_manifest(self):
        key = policy.private_policy_object_key(hashlib.sha256(self.sidecar.read_bytes()).hexdigest())
        manifest_key = policy.private_policy_manifest_key(self.binding_hash())
        for action, duplicate in (("upload", False), ("hide", False), ("start", False), ("upload", True)):
            archive = FakeArchive()
            archive.inject_colliding_version(key, b"different", action=action)
            if duplicate:
                archive.inject_colliding_version(key, b"other")
            with self.subTest(action=action, duplicate=duplicate), self.assertRaises(policy.PrivatePolicyError):
                self.seed(archive)
            self.assertEqual(archive.version_count(manifest_key), 0)

    def test_missing_invalid_unknown_and_duplicate_sidecar_fields_make_no_archive_writes(self):
        raw = self.sidecar.read_bytes()
        self.sidecar.unlink()
        with self.assertRaisesRegex(policy.PrivatePolicyError, "^private_policy.sidecar_missing$"):
            self.seed()
        invalid = sidecar_document()
        invalid["extra"] = "private-sentinel"
        for payload in (policy.canonical_json(invalid), b'{"schema_version":1,"schema_version":1}', b"bad"):
            self.sidecar.write_bytes(payload)
            with self.assertRaises(policy.PrivatePolicyError):
                self.seed()
            self.assertEqual(self.archive.all_keys(), ())
        self.sidecar.write_bytes(raw)

    def test_links_and_noncanonical_publication_and_bad_commit_make_no_writes(self):
        original = self.sidecar.read_bytes()
        target = self.root / "outside.json"
        target.write_bytes(original)
        self.sidecar.unlink()
        try:
            self.sidecar.symlink_to(target)
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(policy.PrivatePolicyError):
            self.seed()
        self.sidecar.unlink()
        self.sidecar.write_bytes(original)
        with self.assertRaises(policy.PrivatePolicyError):
            self.seed(commit="../invalid")
        self.publication.write_text(json.dumps(publication_document(), indent=2), encoding="utf-8")
        with self.assertRaises(policy.PrivatePolicyError):
            self.seed()
        self.assertEqual(self.archive.all_keys(), ())

    def test_closed_manifest_rejects_unknown_fields_bad_identity_lengths_and_scope(self):
        result = self.seed()
        key = policy.private_policy_manifest_key(self.binding_hash())
        original = json.loads(self.archive.get_object(key))
        mutations = (("extra", True), ("schema_version", True), ("schema_version", 2),
                     ("sidecar_sha256", "z" * 64), ("sidecar_length_bytes", True),
                     ("sidecar_length_bytes", -1), ("classification_version", ""),
                     ("prior_publication_commit", "short"), ("objects", ["archive/v1/../outside"]),
                     ("objects", ["outside"]), ("objects", []))
        for field, value in mutations:
            document = copy.deepcopy(original)
            document[field] = value
            with self.subTest(field=field), self.assertRaises(policy.PrivatePolicyError):
                policy.validate_private_policy_manifest(document)
        self.assertEqual(result["sidecar_sha256"], original["sidecar_sha256"])

    def test_missing_bundle_wrong_binding_and_version_fail_closed(self):
        with self.assertRaises(policy.PrivatePolicyError):
            policy.load_private_policy_manifest(self.archive, prior_publication_manifest_sha256=self.binding_hash())
        self.seed()
        for keywords in ({"prior_publication_commit": "b" * 40},
                         {"expected_classification_version": "other-version"}):
            with self.assertRaises(policy.PrivatePolicyError):
                policy.load_private_policy_manifest(self.archive, prior_publication_manifest_sha256=self.binding_hash(), **keywords)

    def test_provider_details_are_never_in_errors_or_results(self):
        for failure in ("fail_all_lists", "fail_all_reads", "fail_all_writes"):
            archive = FakeArchive()
            getattr(archive, failure)(detail="private-provider-sentinel")
            with self.assertRaises(policy.PrivatePolicyError) as caught:
                self.seed(archive)
            self.assertNotIn("private-provider-sentinel", str(caught.exception))

    def test_tampered_manifest_hash_length_version_binding_and_schema_fail(self):
        self.seed()
        manifest_key = policy.private_policy_manifest_key(self.binding_hash())
        original = json.loads(self.archive.get_object(manifest_key))
        for field, value, category in (
                ("sidecar_length_bytes", original["sidecar_length_bytes"] + 1, "private_policy.sidecar_length_mismatch"),
                ("classification_version", "different-version", "private_policy.classification_version_mismatch"),
                ("prior_publication_manifest_sha256", "d" * 64, "private_policy.publication_binding_mismatch"),
                ("extra", "private-sentinel", "private_policy.invalid_manifest")):
            archive = FakeArchive()
            document = copy.deepcopy(original)
            document[field] = value
            archive.put_object(manifest_key, policy.canonical_json(document))
            archive.put_object(original["objects"][0], self.sidecar.read_bytes())
            with self.subTest(field=field), self.assertRaisesRegex(policy.PrivatePolicyError, "^" + category + "$"):
                policy.load_private_policy_manifest(archive, prior_publication_manifest_sha256=self.binding_hash())
        document = copy.deepcopy(original)
        document["sidecar_sha256"] = "d" * 64
        document["objects"] = [policy.private_policy_object_key("d" * 64)]
        archive = FakeArchive()
        archive.put_object(manifest_key, policy.canonical_json(document))
        archive.put_object(document["objects"][0], self.sidecar.read_bytes())
        with self.assertRaisesRegex(policy.PrivatePolicyError, "^private_policy.sidecar_hash_mismatch$"):
            policy.load_private_policy_manifest(archive, prior_publication_manifest_sha256=self.binding_hash())

    def test_corrupt_readback_and_false_provider_metadata_prevent_manifest_write(self):
        object_key = policy.private_policy_object_key(hashlib.sha256(self.sidecar.read_bytes()).hexdigest())
        manifest_key = policy.private_policy_manifest_key(self.binding_hash())
        archive = FakeArchive()
        archive.set_read_override(object_key, b"provider-private-sentinel")
        with self.assertRaisesRegex(policy.PrivatePolicyError, "^private_policy.readback_mismatch$"):
            self.seed(archive)
        self.assertEqual(archive.version_count(manifest_key), 0)
        class WrongMetadata(FakeArchive):
            def list_versions(inner, key):
                versions = super().list_versions(key)
                return tuple(dataclasses.replace(version, content_length=version.content_length + 1)
                             for version in versions)
        archive = WrongMetadata()
        with self.assertRaisesRegex(policy.PrivatePolicyError, "^private_policy.readback_mismatch$"):
            self.seed(archive)
        self.assertEqual(archive.version_count(manifest_key), 0)

    def test_linked_parent_and_reparse_components_stop_before_archive_write(self):
        alias = self.root / "linked-store"
        try:
            alias.symlink_to(self.store, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaisesRegex(policy.PrivatePolicyError, "^private_policy.link_rejected$"):
            policy.seed_private_policy_bundle(self.archive, alias,
                                             published_manifest_path=self.publication, published_data_commit=COMMIT)
        original_lstat = policy.os.lstat
        def reparse(path, *args, **kwargs):
            metadata = original_lstat(path, *args, **kwargs)
            if Path(path) == self.store:
                return SimpleNamespace(st_mode=metadata.st_mode, st_file_attributes=1024)
            return metadata
        with patch.object(policy.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024, create=True), \
                patch.object(policy.os, "lstat", side_effect=reparse):
            with self.assertRaisesRegex(policy.PrivatePolicyError, "^private_policy.link_rejected$"):
                self.seed()
        self.assertEqual(self.archive.all_keys(), ())

    def test_publication_schema_and_version_output_are_non_echo_checked_before_writes(self):
        document = publication_document()
        document["extra"] = "private-publication-sentinel"
        self.publication.write_bytes(policy.canonical_json(document))
        with self.assertRaisesRegex(policy.PrivatePolicyError, "^private_policy.invalid_publication_manifest$"):
            self.seed()
        self.publication.write_bytes(policy.canonical_json(publication_document()))
        document = sidecar_document()
        document["classification_version"] = "/" + "Users/private-version-sentinel"
        self.sidecar.write_bytes(policy.canonical_json(document))
        with self.assertRaisesRegex(policy.PrivatePolicyError, "^private_policy.invalid_classification_version$") as caught:
            self.seed()
        self.assertNotIn("private-version-sentinel", str(caught.exception))
        self.assertEqual(self.archive.all_keys(), ())

    def test_conflicting_same_publication_bundle_preserves_prior_manifest(self):
        self.seed()
        key = policy.private_policy_manifest_key(self.binding_hash())
        before = self.archive.get_object(key)
        document = sidecar_document()
        document["classification_version"] = "synthetic-policy-v2"
        self.sidecar.write_bytes(policy.canonical_json(document))
        with self.assertRaisesRegex(policy.PrivatePolicyError, "^private_policy.collision$"):
            self.seed()
        self.assertTrue(self.archive.get_object(key) == before)
        self.assertEqual(self.archive.version_count(key), 1)

    def test_missing_or_ambiguous_archived_policy_never_becomes_usable(self):
        self.seed()
        key = policy.private_policy_manifest_key(self.binding_hash())
        archive = FakeArchive()
        archive.put_object(key, self.archive.get_object(key))
        with self.assertRaisesRegex(policy.PrivatePolicyError, "^private_policy.object_missing$"):
            policy.load_private_policy_manifest(archive, prior_publication_manifest_sha256=self.binding_hash())
        self.archive.inject_colliding_version(key, self.archive.get_object(key))
        with self.assertRaisesRegex(policy.PrivatePolicyError, "^private_policy.version_ambiguous$"):
            policy.load_private_policy_manifest(self.archive, prior_publication_manifest_sha256=self.binding_hash())

    def test_distinct_publication_bundles_remain_additive(self):
        first = self.seed()
        old_keys = self.archive.all_keys()
        document = publication_document()
        document["toolchain"]["python"] = "1.0.1"
        self.publication.write_bytes(policy.canonical_json(document))
        sidecar = sidecar_document()
        sidecar["classification_version"] = "synthetic-policy-v2"
        self.sidecar.write_bytes(policy.canonical_json(sidecar))
        second = self.seed(commit="b" * 40)
        self.assertNotEqual(first["sidecar_sha256"], second["sidecar_sha256"])
        self.assertTrue(set(old_keys).issubset(self.archive.all_keys()))
        self.assertTrue(all(self.archive.version_count(key) == 1 for key in self.archive.all_keys()))


if __name__ == "__main__":
    unittest.main()
