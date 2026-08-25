"""Tests for tools.privacy_scan.policy.

Uses only reserved synthetic values. Never imports fixtures from the private
Calico-build workspace.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.privacy_scan.policy import (
    ALLOWED_CATEGORIES,
    PathRule,
    Policy,
    PolicyError,
    load_policy,
)

SYNTHETIC_MARKER = "synthetic-marker-zzqx-9138"

VALID_POLICY = {
    "policy_version": 1,
    "max_blob_bytes": 1048576,
    "forbidden_paths": [
        {"kind": "prefix", "value": "data/raw/", "category": "raw_source_data"},
        {"kind": "suffix", "value": ".duckdb", "category": "database_file"},
        {"kind": "exact", "value": "data/mitos.db", "category": "private_database"},
    ],
}


class PolicyTempFile:
    """Writes a policy dict to a temporary JSON file for the duration of a `with` block."""

    def __init__(self, payload: object) -> None:
        self._payload = payload
        self._dir: tempfile.TemporaryDirectory[str] | None = None
        self.path: Path | None = None

    def __enter__(self) -> Path:
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "publishable-tree.json"
        if isinstance(self._payload, str):
            self.path.write_text(self._payload, encoding="utf-8")
        else:
            self.path.write_text(json.dumps(self._payload), encoding="utf-8")
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._dir is not None:
            self._dir.cleanup()


class TestLoadPolicyValid(unittest.TestCase):
    def test_valid_policy_loads(self) -> None:
        with PolicyTempFile(VALID_POLICY) as path:
            policy = load_policy(path)
        self.assertIsInstance(policy, Policy)
        self.assertEqual(policy.policy_version, 1)
        self.assertEqual(policy.max_blob_bytes, 1048576)
        self.assertEqual(len(policy.forbidden_paths), 3)
        self.assertTrue(all(isinstance(rule, PathRule) for rule in policy.forbidden_paths))

    def test_forbidden_path_rule_fields(self) -> None:
        with PolicyTempFile(VALID_POLICY) as path:
            policy = load_policy(path)
        kinds = {rule.kind for rule in policy.forbidden_paths}
        self.assertEqual(kinds, {"exact", "prefix", "suffix"})
        for rule in policy.forbidden_paths:
            self.assertIn(rule.category, ALLOWED_CATEGORIES)

    def test_committed_seed_policy_loads(self) -> None:
        seed_path = Path(__file__).resolve().parents[3] / "policies" / "publishable-tree.json"
        policy = load_policy(seed_path)
        self.assertEqual(policy.policy_version, 1)
        self.assertGreater(policy.max_blob_bytes, 0)
        self.assertGreater(len(policy.forbidden_paths), 0)
        for rule in policy.forbidden_paths:
            self.assertIn(rule.kind, {"exact", "prefix", "suffix"})
            self.assertIn(rule.category, ALLOWED_CATEGORIES)


class TestLoadPolicyFailsClosed(unittest.TestCase):
    def test_missing_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist.json"
            with self.assertRaises(PolicyError) as ctx:
                load_policy(missing)
            self.assertEqual(ctx.exception.category, "policy_not_found")

    def test_invalid_json_fails_closed(self) -> None:
        with PolicyTempFile("{not valid json") as path:
            with self.assertRaises(PolicyError) as ctx:
                load_policy(path)
            self.assertEqual(ctx.exception.category, "invalid_policy_json")

    def test_non_object_root_fails_closed(self) -> None:
        with PolicyTempFile([1, 2, 3]) as path:
            with self.assertRaises(PolicyError) as ctx:
                load_policy(path)
            self.assertEqual(ctx.exception.category, "invalid_policy_schema")

    def test_unknown_top_level_field_fails_closed(self) -> None:
        payload = dict(VALID_POLICY)
        payload["unexpected_field"] = SYNTHETIC_MARKER
        with PolicyTempFile(payload) as path:
            with self.assertRaises(PolicyError) as ctx:
                load_policy(path)
            self.assertEqual(ctx.exception.category, "invalid_policy_schema")

    def test_missing_required_field_fails_closed(self) -> None:
        payload = {"policy_version": 1, "forbidden_paths": []}
        with PolicyTempFile(payload) as path:
            with self.assertRaises(PolicyError) as ctx:
                load_policy(path)
            self.assertEqual(ctx.exception.category, "invalid_policy_schema")

    def test_wrong_policy_version_fails_closed(self) -> None:
        payload = dict(VALID_POLICY)
        payload["policy_version"] = 2
        with PolicyTempFile(payload) as path:
            with self.assertRaises(PolicyError) as ctx:
                load_policy(path)
            self.assertEqual(ctx.exception.category, "invalid_policy_version")

    def test_non_positive_max_blob_bytes_fails_closed(self) -> None:
        payload = dict(VALID_POLICY)
        payload["max_blob_bytes"] = 0
        with PolicyTempFile(payload) as path:
            with self.assertRaises(PolicyError) as ctx:
                load_policy(path)
            self.assertEqual(ctx.exception.category, "invalid_max_blob_bytes")

    def test_negative_max_blob_bytes_fails_closed(self) -> None:
        payload = dict(VALID_POLICY)
        payload["max_blob_bytes"] = -5
        with PolicyTempFile(payload) as path:
            with self.assertRaises(PolicyError) as ctx:
                load_policy(path)
            self.assertEqual(ctx.exception.category, "invalid_max_blob_bytes")

    def test_unknown_rule_kind_fails_closed(self) -> None:
        payload = dict(VALID_POLICY)
        payload["forbidden_paths"] = [
            {"kind": "regex", "value": "data/raw/", "category": "raw_source_data"}
        ]
        with PolicyTempFile(payload) as path:
            with self.assertRaises(PolicyError) as ctx:
                load_policy(path)
            self.assertEqual(ctx.exception.category, "invalid_rule_kind")

    def test_unknown_rule_category_fails_closed(self) -> None:
        payload = dict(VALID_POLICY)
        payload["forbidden_paths"] = [
            {"kind": "prefix", "value": "data/raw/", "category": SYNTHETIC_MARKER}
        ]
        with PolicyTempFile(payload) as path:
            with self.assertRaises(PolicyError) as ctx:
                load_policy(path)
            self.assertEqual(ctx.exception.category, "invalid_rule_category")

    def test_rule_missing_field_fails_closed(self) -> None:
        payload = dict(VALID_POLICY)
        payload["forbidden_paths"] = [{"kind": "prefix", "value": "data/raw/"}]
        with PolicyTempFile(payload) as path:
            with self.assertRaises(PolicyError) as ctx:
                load_policy(path)
            self.assertEqual(ctx.exception.category, "invalid_rule_schema")

    def test_non_posix_backslash_path_fails_closed(self) -> None:
        payload = dict(VALID_POLICY)
        payload["forbidden_paths"] = [
            {"kind": "prefix", "value": "data\\raw\\", "category": "raw_source_data"}
        ]
        with PolicyTempFile(payload) as path:
            with self.assertRaises(PolicyError) as ctx:
                load_policy(path)
            self.assertEqual(ctx.exception.category, "non_posix_path")

    def test_leading_slash_path_fails_closed(self) -> None:
        payload = dict(VALID_POLICY)
        payload["forbidden_paths"] = [
            {"kind": "prefix", "value": "/data/raw/", "category": "raw_source_data"}
        ]
        with PolicyTempFile(payload) as path:
            with self.assertRaises(PolicyError) as ctx:
                load_policy(path)
            self.assertEqual(ctx.exception.category, "non_posix_path")

    def test_empty_path_value_fails_closed(self) -> None:
        payload = dict(VALID_POLICY)
        payload["forbidden_paths"] = [{"kind": "prefix", "value": "", "category": "raw_source_data"}]
        with PolicyTempFile(payload) as path:
            with self.assertRaises(PolicyError) as ctx:
                load_policy(path)
            self.assertEqual(ctx.exception.category, "non_posix_path")

    def test_errors_never_reflect_input(self) -> None:
        payload = dict(VALID_POLICY)
        payload["forbidden_paths"] = [
            {"kind": "prefix", "value": "data\\raw\\" + SYNTHETIC_MARKER, "category": "raw_source_data"}
        ]
        with PolicyTempFile(payload) as path:
            with self.assertRaises(PolicyError) as ctx:
                load_policy(path)
            self.assertNotIn(SYNTHETIC_MARKER, str(ctx.exception))
            self.assertNotIn(SYNTHETIC_MARKER, ctx.exception.category)


if __name__ == "__main__":
    unittest.main()
