"""Contracts for the first governed Power BI project; synthetic inputs only."""

from __future__ import annotations

import csv
import io
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from calico_publish.allowlist import load_allowlist
from tools.privacy_scan.policy import load_policy
from tools.privacy_scan.scanner import scan


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "powerbi"
DEFINITIONS = PROJECT / "Calico.SemanticModel" / "definition"
AUTHORITY = load_allowlist(ROOT / "contracts/publication-exports-v3.json")


def _required_columns(tmdl: str) -> tuple[str, ...]:
    match = re.search(r"Required = \{([^}]+)\}", tmdl)
    if match is None:
        raise ValueError("missing required source projection")
    return tuple(re.findall(r'"([a-z_]+)"', match.group(1)))


def _fixture_csv(payload: str, required: tuple[str, ...]) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(payload, newline=""))
    if tuple(reader.fieldnames or ()) != required:
        raise ValueError("source shape mismatch")
    return list(reader)


def _fixture_control(payload: str, required: tuple[str, ...]) -> dict[str, object]:
    record = json.loads(payload)
    if not isinstance(record, dict) or any(field not in record for field in required):
        raise ValueError("source shape mismatch")
    return {field: record[field] for field in required}


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


class GovernedTracerTests(unittest.TestCase):
    def test_native_project_retains_the_governed_tracer_sources_in_final_model(self):
        project = json.loads((PROJECT / "Calico.pbip").read_text(encoding="utf-8"))
        self.assertEqual(project["artifacts"][0]["report"]["path"], "Calico.Report")
        expressions = (DEFINITIONS / "expressions.tmdl").read_text(encoding="utf-8")
        self.assertIn("https://raw.githubusercontent.com/mrnouiouat/calico/published-data/", expressions)
        for table, relative_path in (
            ("mart_publication_status", "exports/mart_publication_status.csv"),
            ("capture_status", "capture-status.json"),
            ("dim_public_organizations", "exports/dim_public_organizations.csv"),
        ):
            with self.subTest(table=table):
                definition = (DEFINITIONS / "tables" / f"{table}.tmdl").read_text(encoding="utf-8")
                self.assertIn(f'RelativePath="{relative_path}"', definition)
                self.assertIn("mode: import", definition)
                self.assertIn("Web.Contents(PublishedBaseUrl", definition)
                self.assertNotIn("Table.FirstN", definition)
                self.assertNotIn("File.Contents", definition)
        self.assertEqual(len(list((DEFINITIONS / "tables").glob("*.tmdl"))), 13)
        self.assertTrue((DEFINITIONS / "relationships.tmdl").is_file())
        self.assertIn("annotation __PBI_TimeIntelligenceEnabled = 0",
                      (DEFINITIONS / "model.tmdl").read_text(encoding="utf-8"))

    def test_actual_source_projections_and_types_match_the_authority(self):
        for entry in (next(e for e in AUTHORITY.exports if e.export_name == name)
                      for name in ("mart_publication_status", "dim_public_organizations")):
            with self.subTest(table=entry.export_name):
                text = (DEFINITIONS / "tables" / f"{entry.export_name}.tmdl").read_text(encoding="utf-8")
                self.assertEqual(_required_columns(text), entry.columns)
                self.assertEqual(tuple(re.findall(r"^\tcolumn ([a-z_]+)$", text, re.M)), entry.columns)
                self.assertIn("Encoding=65001", text)
                self.assertIn("QuoteStyle=QuoteStyle.Csv", text)
                self.assertIn("Table.ColumnNames(Headers) <> Required", text)
                self.assertIn("MissingField.Error", text)
                for column in entry.columns:
                    self.assertIn(f"sourceColumn: {column}", text)
                    self.assertIn(f'{{"table_name":"{entry.export_name}","column_name":"{column}"}}', text)
        dimension = (DEFINITIONS / "tables" / "dim_public_organizations.tmdl").read_text(encoding="utf-8")
        self.assertRegex(dimension, r"column state_charity_registration_number\n\t\tdataType: string")
        self.assertIn('{"state_charity_registration_number", type text}', dimension)

    def test_fixture_source_shape_fails_on_missing_required_field(self):
        table = (DEFINITIONS / "tables" / "mart_publication_status.tmdl").read_text(encoding="utf-8")
        required = _required_columns(table)
        self.assertEqual(_fixture_csv("published_as_of_date,published_release_revision\n2032-01-05,2\n", required),
                         [{"published_as_of_date": "2032-01-05", "published_release_revision": "2"}])
        with self.assertRaisesRegex(ValueError, "source shape mismatch"):
            _fixture_csv("published_as_of_date\n2032-01-05\n", required)
        control = (DEFINITIONS / "tables" / "capture_status.tmdl").read_text(encoding="utf-8")
        approved = AUTHORITY.control_sources[0].columns
        self.assertEqual(_required_columns(control), approved)
        self.assertIn("Record.SelectFields(Shape, Required, MissingField.Error)", control)
        self.assertIn("Table.FromRecords({Selected}, Required)", control)
        self.assertNotIn("last_accepted_as_of_date", control)
        self.assertNotIn("last_accepted_release_revision", control)
        self.assertIn('{"ended_at_utc", type datetimezone}', control)
        self.assertIn('{"newer_attempt_not_accepted", type logical}', control)
        self.assertEqual(tuple(re.findall(r"^\tcolumn ([a-z_]+)$", control, re.M)), approved)
        synthetic = {field: "synthetic" for field in approved}
        synthetic["newer_attempt_not_accepted"] = True
        self.assertEqual(_fixture_control(json.dumps(synthetic), approved), synthetic)
        del synthetic["outcome"]
        with self.assertRaisesRegex(ValueError, "source shape mismatch"):
            _fixture_control(json.dumps(synthetic), approved)

    def test_policy_rejects_staged_cache_settings_and_binary_export(self):
        policy = load_policy(ROOT / "policies" / "publishable-tree.json")
        for relative in (
            "powerbi/Calico.SemanticModel/.pbi/cache.abf",
            "powerbi/Calico.Report/.pbi/localSettings.json",
            "powerbi/Calico.Report/.pbi/localsettings.json",
            "powerbi/Calico.pbix",
        ):
            with self.subTest(path=relative), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                _git(repo, "init", "-q")
                path = repo / relative
                path.parent.mkdir(parents=True)
                path.write_bytes(b"synthetic forbidden local artifact")
                _git(repo, "add", "--", relative)
                tree = _git(repo, "write-tree")
                findings = scan(treeish=tree, history_all=False, repo_dir=repo, policy=policy)
                self.assertEqual([finding.category for finding in findings], ["forbidden_path"])

    def test_first_real_banner_page_retains_both_source_groups(self):
        report = PROJECT / "Calico.Report" / "definition"
        report_definition = json.loads((report / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report_definition["themeCollection"], {})
        pages = json.loads((report / "pages" / "pages.json").read_text(encoding="utf-8"))
        self.assertEqual(pages["pageOrder"], [
            "published_registry_change", "cohort_persistence", "release_quality", "organization_lookup"
        ])
        self.assertTrue((report / "version.json").is_file())
        page = json.loads((report / "pages" / "published_registry_change" / "page.json").read_text(encoding="utf-8"))
        self.assertEqual(page["displayName"], "Published registry change")
        visuals = report / "pages" / "published_registry_change" / "visuals"
        for name in ("release_identity", "capture_outcome", "attempt_time", "newer_attempt_warning", "source_retirement"):
            with self.subTest(name=name):
                visual = json.loads((visuals / name / "visual.json").read_text(encoding="utf-8"))
                self.assertEqual(visual["name"], name)
                self.assertTrue(visual["visual"]["query"]["queryState"]["Data"]["projections"])
                self.assertEqual(visual["visual"]["visualType"], "cardVisual")
        release = json.loads((visuals / "release_identity" / "visual.json").read_text(encoding="utf-8"))
        self.assertEqual(len(release["visual"]["query"]["queryState"]["Data"]["projections"]), 2)


if __name__ == "__main__":
    unittest.main()
