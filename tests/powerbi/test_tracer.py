"""Contracts for the first governed Power BI project; synthetic inputs only."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "powerbi"


class GovernedTracerTests(unittest.TestCase):
    def test_native_project_has_three_governed_imports(self):
        project = json.loads((PROJECT / "Calico.pbip").read_text(encoding="utf-8"))
        self.assertEqual(project["artifacts"][0]["report"]["path"], "Calico.Report")
        definitions = PROJECT / "Calico.SemanticModel" / "definition"
        expressions = (definitions / "expressions.tmdl").read_text(encoding="utf-8")
        self.assertIn("https://raw.githubusercontent.com/mrnouiouat/calico/published-data/", expressions)
        for table, relative_path in (
            ("mart_publication_status", "exports/mart_publication_status.csv"),
            ("capture_status", "capture-status.json"),
            ("dim_public_organizations", "exports/dim_public_organizations.csv"),
        ):
            with self.subTest(table=table):
                definition = (definitions / "tables" / f"{table}.tmdl").read_text(encoding="utf-8")
                self.assertIn(f'RelativePath="{relative_path}"', definition)
                self.assertIn("mode: import", definition)

    def test_one_real_banner_page_has_both_source_groups(self):
        report = PROJECT / "Calico.Report" / "definition"
        pages = json.loads((report / "pages" / "pages.json").read_text(encoding="utf-8"))
        self.assertEqual(pages["pageOrder"], ["published_registry_change"])
        visuals = report / "pages" / "published_registry_change" / "visuals"
        for name in ("release_identity", "attempt_status"):
            with self.subTest(name=name):
                visual = json.loads((visuals / name / "visual.json").read_text(encoding="utf-8"))
                self.assertEqual(visual["name"], name)
                self.assertTrue(visual["visual"]["query"]["queryState"]["Data"]["projections"])


if __name__ == "__main__":
    unittest.main()
