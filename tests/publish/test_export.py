"""Fixed CSV dialect, complete fixture exports, and fail-closed read-back proofs."""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import inspect
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from calico_dbt import runner
from calico_publish.allowlist import Allowlist, ExportEntry, load_allowlist
from calico_publish import export as module
from calico_publish.gate import verify
from tests.publish.test_tracer import _ALLOWLIST_PATH, _manifest


class ExportBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="calico-export-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "fixture.duckdb"
        self.entry = ExportEntry("fixture_export", "aggregate", "fixture_export",
                                 ("sequence", "label"), ("sequence",), ("label",),
                                 "fixture_export.csv", "calico-csv-v1")
        self.allowlist = Allowlist(1, "publication-exports-v1", (self.entry,))
        with duckdb.connect(str(self.database)) as connection:
            connection.execute("CREATE TABLE fixture_export(sequence INTEGER, label VARCHAR)")

    def test_quoted_newlines_and_control_separators_count_csv_records(self):
        with duckdb.connect(str(self.database)) as connection:
            connection.executemany("INSERT INTO fixture_export VALUES (?, ?)", [
                (2, 'quoted "label", with comma'), (1, "line\nbreak"),
                (3, "control\x1eseparator"), (4, "café"), (5, ""), (6, None),
            ])
        records = module.export_all(self.database, self.allowlist, self.root / "first")
        repeated = module.export_all(self.database, self.allowlist, self.root / "second")
        payload = (self.root / "first" / records[0].relative_path).read_bytes()
        self.assertEqual(records[0].row_count, 6)
        self.assertEqual(records, repeated)
        self.assertTrue(payload == (self.root / "second" / repeated[0].relative_path).read_bytes())
        self.assertIn(b'5,""\n6,\n', payload)
        manifest = _manifest(self.allowlist, records)
        manifest_path = self.root / "first" / "manifest.json"
        manifest_path.write_text(manifest.to_json(), encoding="ascii")
        result = verify(self.root / "first", self.allowlist, manifest_path)
        self.assertTrue(result.passed, [v.category for v in result.violations])

    def test_readback_rejects_bad_encoding_header_and_row_width(self):
        path = self.root / "candidate.csv"
        for payload, category in (
            (b"\xff", "invalid_encoding"),
            (b"\xef\xbb\xbfsequence,label\n", "byte_order_mark_present"),
            (b"sequence,label\r\n", "carriage_return_present"),
            (b"label,sequence\n", "header_mismatch"),
            (b"sequence,label\n1\n", "row_arity_mismatch"),
            (b"sequence,label\n1,x,extra\n", "row_arity_mismatch"),
            (b'sequence,label\n1,"unterminated\n', "readback_failed"),
        ):
            with self.subTest(category=category):
                path.write_bytes(payload)
                with self.assertRaises(module.ExportError) as caught:
                    module._readback(path, self.entry)
                self.assertEqual(str(caught.exception), "export." + category)

    def test_nonempty_exports_are_rejected_but_empty_exports_are_usable(self):
        staging = self.root / "staging"
        (staging / "exports").mkdir(parents=True)
        records = module.export_all(self.database, self.allowlist, staging)
        self.assertEqual(records[0].row_count, 0)
        with self.assertRaises(module.ExportError) as caught:
            module.export_all(self.database, self.allowlist, staging)
        self.assertEqual(str(caught.exception), "export.staging_not_empty")

    def test_missing_late_projection_fails_before_any_file_is_written(self):
        missing = dataclasses.replace(self.entry, export_name="late_export", file_name="late_export.csv",
                                      columns=("sequence", "absent"))
        allowlist = dataclasses.replace(self.allowlist, exports=(self.entry, missing))
        staging = self.root / "staging"
        with self.assertRaises(module.ExportError) as caught:
            module.export_all(self.database, allowlist, staging)
        self.assertEqual(str(caught.exception), "export.header_mismatch")
        self.assertFalse(list(staging.rglob("*.csv")))

    def test_failed_readback_removes_only_this_attempts_outputs(self):
        staging = self.root / "staging"
        with patch.object(module, "_readback", side_effect=module.ExportError("export.readback_failed")):
            with self.assertRaises(module.ExportError):
                module.export_all(self.database, self.allowlist, staging)
        self.assertFalse(list(staging.rglob("*.csv")))

    def test_copy_options_are_exercised_on_pinned_binary_without_python_rendering(self):
        self.assertEqual(duckdb.__version__, "1.5.5")
        self.assertEqual(set(inspect.signature(module.export_all).parameters),
                         {"duckdb_path", "allowlist", "staging_dir"})
        with duckdb.connect(str(self.database)) as connection:
            connection.execute("CREATE TABLE typed_export(sequence INTEGER, amount DECIMAL(18,6), observed DATE, captured TIMESTAMP)")
            connection.execute("INSERT INTO typed_export VALUES (1, 123.456789, DATE '2026-01-02', TIMESTAMP '2026-01-02 03:04:05')")
        entry = dataclasses.replace(self.entry, source_relation="typed_export",
                                    columns=("sequence", "amount", "observed", "captured"),
                                    measures=("amount", "observed", "captured"))
        records = module.export_all(self.database, dataclasses.replace(self.allowlist, exports=(entry,)), self.root / "typed")
        payload = (self.root / "typed" / records[0].relative_path).read_bytes()
        self.assertTrue(payload.endswith(b"1,123.456789,2026-01-02,2026-01-02T03:04:05\n"))

    def test_named_observation_grain_retains_same_date_revisions(self):
        authority = load_allowlist(_ALLOWLIST_PATH)
        entry = next(e for e in authority.exports if e.export_name == "fct_public_status_observations")
        with duckdb.connect(str(self.database)) as connection:
            connection.execute("CREATE TABLE fct_public_status_observations ("
                               "state_charity_registration_number VARCHAR, as_of_date DATE, "
                               "release_revision INTEGER, observation_state VARCHAR, "
                               "organization_name VARCHAR, city VARCHAR, state VARCHAR, "
                               "source_reported_status VARCHAR)")
            connection.execute("INSERT INTO fct_public_status_observations VALUES "
                               "('fixture-key', DATE '2026-01-01', 2, 'observed', 'Fixture Organization', NULL, NULL, 'Current'), "
                               "('fixture-key', DATE '2026-01-01', 1, 'observed', 'Fixture Organization', NULL, NULL, 'Current')")
        subset = dataclasses.replace(authority, exports=(entry,))
        staging = self.root / "named"
        records = module.export_all(self.database, subset, staging)
        self.assertEqual(entry.grain, ("state_charity_registration_number", "as_of_date", "release_revision"))
        self.assertEqual(records[0].row_count, 2)
        rows = list(csv.reader(io.StringIO((staging / records[0].relative_path).read_text(encoding="utf-8"))))
        self.assertEqual([r[2] for r in rows[1:]], ["1", "2"])
        self.assertTrue(verify(staging, subset).passed)


class FullExportTests(unittest.TestCase):
    def test_fixture_build_exports_all_eleven_twice_and_empty_named_history(self):
        allowlist = load_allowlist(_ALLOWLIST_PATH)
        with tempfile.TemporaryDirectory(prefix="calico-full-exports-") as temp:
            root = Path(temp)
            captured = {}

            def export(database):
                captured["first"] = module.export_all(database, allowlist, root / "first")
                captured["second"] = module.export_all(database, allowlist, root / "second")
                with duckdb.connect(str(database)) as connection:
                    captured["columns"] = {}
                    captured["keys"] = {}
                    for entry in allowlist.exports:
                        captured["columns"][entry.export_name] = tuple(
                            row[0] for row in connection.execute('DESCRIBE "' + entry.source_relation + '"').fetchall()
                        )
                        projection = ", ".join('CAST("' + c + '" AS VARCHAR)' for c in entry.grain)
                        ordering = ", ".join('"' + c + '" ASC NULLS LAST' for c in entry.grain)
                        captured["keys"][entry.export_name] = connection.execute(
                            'SELECT ' + projection + ' FROM "' + entry.source_relation + '" ORDER BY ' + ordering
                        ).fetchall()
                    # Empty eligibility is an intentional valid state. Reapply the
                    # existing named views to an empty classification input; the
                    # observation fact is materialized, so retain its schema empty.
                    connection.execute("DELETE FROM runtime_input.public_eligibility_classifications")
                    connection.execute("DELETE FROM fct_public_status_observations")
                captured["empty"] = module.export_all(database, allowlist, root / "empty")

            outcome = runner.build(mode="fixture", export=export)
            self.assertEqual(outcome.status, "success", outcome.category)
            self.assertEqual(captured["first"], captured["second"])
            self.assertEqual(len(captured["first"]), 11)
            self.assertEqual(len(list((root / "first" / "exports").iterdir())), 11)
            for entry, record in zip(allowlist.exports, captured["first"], strict=True):
                payload = (root / "first" / record.relative_path).read_bytes()
                self.assertTrue(payload == (root / "second" / record.relative_path).read_bytes(), entry.export_name)
                self.assertEqual(hashlib.sha256(payload).hexdigest(), record.sha256)
                self.assertNotEqual(payload[0], 0xEF)
                self.assertNotIn(b"\r", payload)
                rows = list(csv.reader(io.StringIO(payload.decode("utf-8"), newline="")))
                self.assertEqual(tuple(rows[0]), entry.columns)
                self.assertEqual(len(rows) - 1, record.row_count)
                self.assertTrue(all(len(row) == len(entry.columns) for row in rows))
                if entry.export_class == "aggregate":
                    self.assertEqual(entry.columns, captured["columns"][entry.export_name])
                else:
                    self.assertEqual(entry.columns, tuple(c for c in captured["columns"][entry.export_name] if c in entry.columns))
                positions = [entry.columns.index(c) for c in entry.grain]
                keys = [tuple(row[p] for p in positions) for row in rows[1:]]
                expected_keys = [tuple("" if v is None else v for v in row) for row in captured["keys"][entry.export_name]]
                self.assertTrue(keys == expected_keys, entry.export_name)
                self.assertEqual(len(keys), len(set(keys)), entry.export_name)
            for entry, record in zip(allowlist.exports, captured["empty"], strict=True):
                if entry.export_class == "named_history":
                    self.assertEqual(record.row_count, 0)
                    self.assertTrue((root / "empty" / record.relative_path).read_bytes() == (",".join(entry.columns) + "\n").encode("ascii"))


if __name__ == "__main__":
    unittest.main()
