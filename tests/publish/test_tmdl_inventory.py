"""The committed TMDL must yield a complete, closed inventory."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from calico_publish.allowlist import load_allowlist
from calico_publish.inventory import check_inventory
from calico_publish.tmdl_inventory import TmdlInventoryError, generate_inventory


ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "powerbi" / "Calico.SemanticModel"
ALLOWLIST = load_allowlist(ROOT / "contracts" / "publication-exports-v3.json")


class TmdlInventoryTests(unittest.TestCase):
    def _copy_model(self, target):
        for source in (MODEL / "definition").rglob("*.tmdl"):
            destination = target / "definition" / source.relative_to(MODEL / "definition")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())

    def test_committed_model_is_complete_and_approved(self):
        record = generate_inventory(MODEL)
        self.assertEqual(record["inventory_source"], "machine_readable_metadata")
        self.assertEqual({table["table_name"] for table in record["tables"]},
                         {entry.export_name for entry in ALLOWLIST.exports}
                         | {entry.export_name for entry in ALLOWLIST.control_sources})
        self.assertEqual(record["relationships"], [{
            "from_table": "dim_public_organizations",
            "from_column": "state_charity_registration_number",
            "to_table": "fct_public_status_observations",
            "to_column": "state_charity_registration_number",
            "cardinality": "one_to_many",
            "cross_filter_direction": "single",
        }])
        self.assertEqual(check_inventory(record, ALLOWLIST), ())

    def test_added_objects_and_unknown_grammar_fail_closed(self):
        cases = [
            ("\n\tcolumn unapproved\n\t\tdataType: string\n", "tmdl.missing_lineage"),
            ("\n\tcalculationGroup Unknown\n", "tmdl.unsupported_syntax"),
            ("\n\tcolumn hidden_unknown\n\t\tisHidden\n\t\tdataType: string\n", "tmdl.missing_lineage"),
            ("\n\tmeasure 'Unknown' = 1\n\t\tannotation CalicoBaseLineage = nope\n", "tmdl.invalid_lineage"),
            ("\n\tmeasure 'Unknown' = 1\n", "tmdl.missing_lineage"),
            ("\n\tmeasure 'Unknown' = 1\n\t\tannotation CalicoBaseLineage = [{\"table_name\":\"bad\",\"column_name\":\"x\"}]\n", None),
            ("\n\tcolumn organization_name\n", "tmdl.duplicate_object"),
        ]
        original = (MODEL / "definition" / "tables" / "dim_public_organizations.tmdl").read_text(encoding="utf-8")
        for suffix, category in cases:
            with self.subTest(category=category, suffix=suffix), tempfile.TemporaryDirectory() as temp:
                target = Path(temp)
                self._copy_model(target)
                path = target / "definition" / "tables" / "dim_public_organizations.tmdl"
                path.write_text(original + suffix, encoding="utf-8")
                if category is None:
                    record = generate_inventory(target)
                    self.assertIn("inventory.unapproved_calculated_source",
                                  [finding.category for finding in check_inventory(record, ALLOWLIST)])
                else:
                    with self.assertRaises(TmdlInventoryError) as raised:
                        generate_inventory(target)
                    self.assertEqual(str(raised.exception), category)

    def test_hidden_unapproved_and_unapproved_table_are_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            self._copy_model(target)
            path = target / "definition" / "tables" / "dim_public_organizations.tmdl"
            path.write_text(path.read_text(encoding="utf-8") +
                "\n\tcolumn private_extra\n\t\tdataType: string\n\t\tisHidden\n"
                "\t\tsourceColumn: private_extra\n\t\tannotation CalicoBaseLineage = "
                '[{"table_name":"dim_public_organizations","column_name":"private_extra"}]\n',
                encoding="utf-8")
            findings = check_inventory(generate_inventory(target), ALLOWLIST)
            self.assertEqual([f.category for f in findings], ["inventory.unapproved_hidden_column"])
            unexpected = target / "definition" / "tables" / "unexpected_table.tmdl"
            unexpected.write_text("table unexpected_table\n\n\tcolumn id\n\t\tdataType: string\n"
                "\t\tsourceColumn: id\n\t\tannotation CalicoBaseLineage = "
                '[{"table_name":"unexpected_table","column_name":"id"}]\n'
                "\n\tpartition unexpected_table = m\n\t\tmode: import\n\t\tsource =\n\t\t\tlet\n\t\t\t\tx = 1\n\t\t\tin\n\t\t\t\tx\n",
                encoding="utf-8")
            findings = check_inventory(generate_inventory(target), ALLOWLIST)
            self.assertIn("inventory.unapproved_table", [finding.category for finding in findings])

    def test_cli_generates_the_checked_record(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "inventory.json"
            result = subprocess.run([sys.executable, "-m", "calico_publish", "generate-inventory",
                "--model", str(MODEL), "--output", str(output)], cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), generate_inventory(MODEL))
            check = subprocess.run([sys.executable, "-m", "calico_publish", "check-inventory",
                "--inventory", str(output)], cwd=ROOT, capture_output=True)
            self.assertEqual(check.returncode, 0, check.stderr.decode("utf-8"))

    def test_relationship_file_is_enumerated_and_class_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            self._copy_model(target)
            relationship = target / "definition" / "relationships.tmdl"
            relationship.write_text("relationship tested\n"
                "\tfromColumn: dim_public_organizations.organization_name\n"
                "\ttoColumn: mart_publication_status.published_as_of_date\n"
                "\tfromCardinality: many\n\ttoCardinality: one\n"
                "\tcrossFilteringBehavior: oneDirection\n", encoding="utf-8")
            record = generate_inventory(target)
            self.assertEqual(len(record["relationships"]), 1)
            self.assertEqual([f.category for f in check_inventory(record, ALLOWLIST)],
                             ["inventory.cross_class_relationship"])
            relationship.write_text(relationship.read_text(encoding="utf-8") +
                "\tsecurityFilteringBehavior: bothDirections\n", encoding="utf-8")
            with self.assertRaises(TmdlInventoryError):
                generate_inventory(target)

    def test_multiline_measure_is_an_expression_not_a_declaration(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            self._copy_model(target)
            path = target / "definition" / "tables" / "mart_publication_status.tmdl"
            original = path.read_text(encoding="utf-8")
            path.write_text(original + "\n\tmeasure 'Multiline' =\n"
                "\t\tVAR x = 1\n\t\tRETURN x\n"
                "\t\tannotation CalicoBaseLineage = "
                '[{"table_name":"mart_publication_status","column_name":"published_as_of_date"}]\n',
                encoding="utf-8")
            record = generate_inventory(target)
            self.assertIn("Multiline", [field["field_name"] for table in record["tables"]
                                         for field in table["fields"]])

    def test_auto_date_and_unexpected_file_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            (target / "definition" / "tables").mkdir(parents=True)
            (target / "definition" / "tables" / "LocalDateTable.tmdl").write_text("table LocalDateTable\n", encoding="utf-8")
            with self.assertRaises(TmdlInventoryError):
                generate_inventory(target)


if __name__ == "__main__":
    unittest.main()
