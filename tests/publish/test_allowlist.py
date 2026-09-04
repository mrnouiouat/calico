"""Prove the publication authority is closed and agrees with dbt documentation."""

from __future__ import annotations

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

from calico_publish import allowlist as module

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts/publication-exports-v1.json"
NAMED_OMISSIONS = {
    "dim_public_organizations": {"latest_is_delinquent", "latest_observed_revision_fingerprint"},
    "fct_public_status_observations": {"is_delinquent", "revision_fingerprint"},
}


def documented_columns() -> dict[str, set[str]]:
    # These two checked-in files have a fixed indentation contract. Like the
    # foundation contract tests, inspect that structure without a new parser.
    models: dict[str, set[str]] = {}
    for filename in ("marts.yml", "metrics.yml"):
        current = None
        for line in (ROOT / "dbt/models/marts" / filename).read_text(encoding="utf-8").splitlines():
            model = re.fullmatch(r"  - name: ([a-z][a-z0-9_]*)", line)
            column = re.fullmatch(r"      - name: ([a-z][a-z0-9_]*)", line)
            if model:
                current = model[1]
                models[current] = set()
            elif column and current:
                models[current].add(column[1])
    return models


def assert_documentation(test: unittest.TestCase, document: dict) -> None:
    models = documented_columns()
    for entry in document["exports"]:
        name = entry["source_relation"]
        test.assertIn(name, models)
        test.assertEqual(set(entry["columns"]), models[name] - NAMED_OMISSIONS.get(name, set()), name)


class AllowlistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.temp = tempfile.TemporaryDirectory(prefix="calico-allowlist-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "authority.json"

    def load(self, document=None):
        self.path.write_text(json.dumps(self.document if document is None else document), encoding="utf-8")
        return module.load_allowlist(self.path)

    def rejects(self, category, document=None):
        with self.assertRaises(module.AllowlistError) as caught:
            self.load(document)
        self.assertEqual(caught.exception.category, category)
        self.assertEqual(str(caught.exception), category)

    def test_complete_surface_and_structural_boundary(self):
        authority = self.load()
        self.assertEqual(len(authority.exports), 11)
        self.assertEqual(sum(e.export_class == "aggregate" for e in authority.exports), 9)
        self.assertEqual(sum(e.export_class == "named_history" for e in authority.exports), 2)
        names = [e.export_name for e in authority.exports]
        self.assertEqual(names, sorted(names))
        self.assertEqual([e["export_name"] for e in self.document["exports"]], names)
        self.assertEqual(module.AGGREGATE_PROHIBITED_COLUMNS, {
            "state_charity_registration_number", "organization_name", "city", "state"
        })
        for entry in authority.exports:
            self.assertEqual(entry.export_name, entry.source_relation)
            self.assertEqual(entry.file_name, entry.export_name + ".csv")
            self.assertEqual(entry.dialect, "calico-csv-v1")
            self.assertEqual(len(entry.columns), len(set(entry.columns)))
            self.assertLessEqual(set(entry.grain), set(entry.columns))
            if entry.export_class == "aggregate":
                self.assertEqual(set(entry.columns), set(entry.grain) | set(entry.measures))
                self.assertFalse(set(entry.columns) & module.AGGREGATE_PROHIBITED_COLUMNS)
            else:
                self.assertFalse(entry.measures)
                self.assertFalse(set(entry.columns) & set.union(*NAMED_OMISSIONS.values()))
        self.assertLess(len(CONTRACT.read_text(encoding="utf-8").splitlines()), 250)

    def test_documented_columns_agree_in_both_directions(self):
        assert_documentation(self, self.document)
        changed = copy.deepcopy(self.document)
        changed["exports"][0]["columns"][0] = "undocumented_column"
        with self.assertRaises(AssertionError):
            assert_documentation(self, changed)

    def test_loader_sorts_entries(self):
        self.document["exports"].reverse()
        names = [e.export_name for e in self.load().exports]
        self.assertEqual(names, sorted(names))

    def test_closed_category_vocabulary(self):
        self.assertEqual(module.ALLOWLIST_ERROR_CATEGORIES, frozenset({
            "allowlist.not_found", "allowlist.invalid_encoding", "allowlist.invalid_json",
            "allowlist.invalid_schema", "allowlist.duplicate_export_name",
            "allowlist.duplicate_file_name", "allowlist.duplicate_source_relation",
            "allowlist.grain_not_in_columns", "allowlist.aggregate_identity_column",
            "allowlist.aggregate_column_not_grain_or_measure", "allowlist.measures_not_allowed_for_class",
        }))

    def test_missing_unreadable_json_and_encoding(self):
        with self.assertRaises(module.AllowlistError) as caught:
            module.load_allowlist(self.path)
        self.assertEqual(str(caught.exception), "allowlist.not_found")
        for payload, category in ((b"\xff", "allowlist.invalid_encoding"), (b"{", "allowlist.invalid_json")):
            self.path.write_bytes(payload)
            with self.assertRaises(module.AllowlistError) as caught:
                module.load_allowlist(self.path)
            self.assertEqual(str(caught.exception), category)

    def test_unknown_keys_wrong_types_versions_and_dialect(self):
        for scope, key, value in (
            ("top", "unexpected", True), ("entry", "unexpected", True),
            ("top", "schema_version", 2), ("top", "schema_version", True),
            ("top", "schema_version", 1.0), ("top", "exports", []),
            ("top", "allowlist_version", "unknown"), ("entry", "dialect", "unknown"),
            ("entry", "export_class", []), ("entry", "columns", []),
            ("entry", "grain", []), ("entry", "measures", "count"),
            ("entry", "source_relation", "../outside"), ("entry", "file_name", "../outside.csv"),
        ):
            with self.subTest(scope=scope, key=key):
                changed = copy.deepcopy(self.document)
                target = changed if scope == "top" else changed["exports"][0]
                target[key] = value
                self.rejects("allowlist.invalid_schema", changed)
        changed = copy.deepcopy(self.document)
        changed["exports"][0]["columns"].append(changed["exports"][0]["columns"][0])
        self.rejects("allowlist.invalid_schema", changed)

    def test_duplicate_names_files_and_relations_fail_closed(self):
        for field in ("export_name", "file_name", "source_relation"):
            changed = copy.deepcopy(self.document)
            added = copy.deepcopy(changed["exports"][0])
            added.update(export_name="another_export", file_name="another_export.csv", source_relation="another_relation")
            added[field] = changed["exports"][0][field]
            changed["exports"].append(added)
            self.rejects("allowlist.duplicate_" + field, changed)

    def test_extra_missing_and_identity_columns_fail_closed(self):
        self.load()  # exact grain plus measures is admitted
        for mode, category in (
            ("extra", "aggregate_column_not_grain_or_measure"),
            ("missing", "grain_not_in_columns"), ("identity", "aggregate_identity_column"),
        ):
            changed = copy.deepcopy(self.document)
            entry = next(e for e in changed["exports"] if e["export_class"] == "aggregate")
            if mode == "missing":
                entry["columns"].remove(entry["grain"][0])
            else:
                entry["columns"].append("organization_name" if mode == "identity" else "extra_field")
            self.rejects("allowlist." + category, changed)

    def test_named_history_cannot_declare_measures(self):
        self.document["exports"][0]["measures"] = ["organization_name"]
        self.rejects("allowlist.measures_not_allowed_for_class")


if __name__ == "__main__":
    unittest.main()
