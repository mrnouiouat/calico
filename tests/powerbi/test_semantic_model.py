"""Complete governed semantic-model contract tests."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from calico_publish.allowlist import load_allowlist
from calico_publish.inventory import check_inventory, load_inventory_document
from calico_publish.tmdl_inventory import generate_inventory


ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "powerbi/Calico.SemanticModel"
AUTHORITY_PATH = ROOT / "contracts/publication-exports-v3.json"
AUTHORITY = load_allowlist(AUTHORITY_PATH)
AUTHORITY_JSON = json.loads(AUTHORITY_PATH.read_text(encoding="utf-8"))
EXPECTED = {
    item["export_name"]: tuple(item["columns"])
    for item in (*AUTHORITY_JSON["exports"], *AUTHORITY_JSON["control_sources"])
}


class SemanticModelContractTests(unittest.TestCase):
    def test_actual_metadata_has_exact_approved_tables_and_fields(self):
        inventory = generate_inventory(MODEL)
        self.assertEqual({table["table_name"] for table in inventory["tables"]}, set(EXPECTED))
        self.assertEqual(len(inventory["tables"]), 13)
        for table in inventory["tables"]:
            source_fields = tuple(
                field["field_name"] for field in table["fields"]
                if field["origin"] == "source_column"
            )
            self.assertEqual(source_fields, EXPECTED[table["table_name"]])
            for field in table["fields"]:
                self.assertTrue(field["lineage_complete"])
                self.assertTrue(field["source_columns"])
        fact = next(table for table in inventory["tables"] if table["table_name"] == "fct_public_status_observations")
        self.assertEqual(len(fact["fields"]), 5)

    def test_actual_metadata_has_only_the_named_single_direction_edge(self):
        inventory = generate_inventory(MODEL)
        self.assertEqual(inventory["relationships"], [{
            "from_table": "dim_public_organizations",
            "from_column": "state_charity_registration_number",
            "to_table": "fct_public_status_observations",
            "to_column": "state_charity_registration_number",
            "cardinality": "one_to_many",
            "cross_filter_direction": "single",
        }])
        self.assertEqual(check_inventory(inventory, AUTHORITY), ())

    def test_second_relationship_is_rejected_by_existing_checker(self):
        inventory = generate_inventory(MODEL)
        inventory["relationships"].append({
            "from_table": "dim_public_organizations", "from_column": "state_charity_registration_number",
            "to_table": "mart_release_snapshot_metrics", "to_column": "as_of_date",
            "cardinality": "one_to_many", "cross_filter_direction": "single",
        })
        categories = {finding.category for finding in check_inventory(inventory, AUTHORITY)}
        self.assertIn("inventory.cross_class_relationship", categories)

    def test_tracked_inventory_is_generated_from_actual_metadata(self):
        generated = generate_inventory(MODEL)
        tracked = load_inventory_document(ROOT / "powerbi/semantic-model-inventory-v1.json")
        self.assertEqual(tracked, generated)

    def test_each_csv_import_uses_exact_fixed_relative_path_and_utf8(self):
        for entry in AUTHORITY_JSON["exports"]:
            path = MODEL / "definition/tables" / f'{entry["export_name"]}.tmdl'
            text = path.read_text(encoding="utf-8")
            self.assertIn(f'RelativePath="exports/{entry["file_name"]}"', text)
            self.assertIn("Encoding=65001", text)
            self.assertIn("QuoteStyle=QuoteStyle.Csv", text)
            self.assertEqual(re.findall(r"Required = \{([^}]+)\}", text).__len__(), 1)

    def test_model_disables_auto_date_and_contains_no_calculated_table(self):
        model = (MODEL / "definition/model.tmdl").read_text(encoding="utf-8")
        self.assertIn("annotation __PBI_TimeIntelligenceEnabled = 0", model)
        for path in (MODEL / "definition/tables").glob("*.tmdl"):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"(?m)^table .+ =")


if __name__ == "__main__":
    unittest.main()
