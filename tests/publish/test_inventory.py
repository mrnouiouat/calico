"""Synthetic visible/hidden field, relationship, and structured lineage proofs."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from calico_publish import inventory as module
from calico_publish.allowlist import load_allowlist

ROOT = Path(__file__).resolve().parents[2]
TABLE = "dim_public_organizations"
COLUMN = "organization_name"


def field(name=COLUMN, visibility="visible", origin="source_column", source=None):
    return dict(field_name=name, visibility=visibility, origin=origin, lineage_complete=True,
                source_columns=source if source is not None else [dict(table_name=TABLE, column_name=name)])


def document():
    return dict(schema_version=1, model_name="registry_monitor",
                inventory_source="manual_inspection_record",
                tables=[dict(table_name=TABLE, fields=[field()])], relationships=[])


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.allowlist = load_allowlist(ROOT / "contracts/publication-exports-v1.json")

    def check(self, data):
        return module.check_inventory(data, self.allowlist)

    def test_valid_visible_hidden_calculated_measure_and_relationship(self):
        data = document()
        data["tables"][0]["fields"].extend([
            field("city", "hidden"),
            field("display_label", origin="calculated_column", source=[dict(table_name=TABLE, column_name=COLUMN)]),
            field("observed_count", origin="measure", source=[dict(table_name=TABLE, column_name=COLUMN)]),
        ])
        data["relationships"] = [dict(from_table=TABLE, from_column=COLUMN, to_table=TABLE,
                                      to_column="city", cardinality="one_to_many", cross_filter_direction="single")]
        self.assertEqual(self.check(data), ())
        data["inventory_source"] = "machine_readable_metadata"
        self.assertEqual(self.check(data), ())

    def test_unapproved_hidden_column_visible_only_would_miss(self):
        data = document()
        data["tables"][0]["fields"].append(field("unapproved", "hidden"))
        findings = self.check(data)
        self.assertEqual([f.category for f in findings], ["inventory.unapproved_hidden_column"])
        data["tables"][0]["fields"] = [f for f in data["tables"][0]["fields"] if f["visibility"] == "visible"]
        self.assertEqual(self.check(data), ())

    def test_unapproved_visible_column(self):
        data = document()
        data["tables"][0]["fields"].append(field("unapproved"))
        self.assertEqual([f.category for f in self.check(data)], ["inventory.unapproved_column"])

    def test_unapproved_table(self):
        data = document()
        data["tables"][0]["table_name"] = "unapproved_table"
        data["tables"][0]["fields"][0]["source_columns"][0]["table_name"] = "unapproved_table"
        self.assertIn("inventory.unapproved_table", [f.category for f in self.check(data)])

    def test_unapproved_relationship_each_endpoint(self):
        relationship = dict(from_table=TABLE, from_column=COLUMN, to_table=TABLE,
                            to_column=COLUMN, cardinality="one_to_one", cross_filter_direction="both")
        for key in ("from_table", "from_column", "to_table", "to_column"):
            data = document()
            data["relationships"] = [dict(relationship, **{key: "unapproved"})]
            self.assertEqual([f.category for f in self.check(data)], ["inventory.unapproved_relationship"])

    def test_unapproved_calculated_source_and_measure_source(self):
        for origin in ("calculated_column", "measure"):
            data = document()
            data["tables"][0]["fields"].append(field("derived", origin=origin,
                source=[dict(table_name=TABLE, column_name="unapproved"),
                        dict(table_name="unapproved_table", column_name=COLUMN)]))
            self.assertEqual([f.category for f in self.check(data)], ["inventory.unapproved_calculated_source"] * 2)

    def test_findings_are_sorted_and_value_free(self):
        data = document()
        data["tables"][0]["fields"].extend([field("z_hidden", "hidden"), field("a_visible")])
        findings = self.check(data)
        self.assertEqual(findings, tuple(sorted(findings, key=lambda f: (f.table_name, f.field_name, f.category))))
        for finding in findings:
            self.assertEqual(set(vars(finding)), {"table_name", "field_name", "category"})
            self.assertEqual(finding.render(), f"{finding.table_name}:{finding.field_name}: {finding.category}")

    def test_labels_cannot_echo_numeric_value_tokens(self):
        unsafe_labels = ["Field " + "123" + "456" + "789",
                         "Field " + "123" + " Example Street", "Field  v2",
                         " Field", "Field ", "Field\nName"]
        for label in unsafe_labels:
            data = document()
            data["tables"][0]["fields"].append(field(label, origin="measure",
                source=[dict(table_name=TABLE, column_name="unapproved")]))
            with self.assertRaises(module.InventoryError) as raised:
                self.check(data)
            self.assertEqual(str(raised.exception), "inventory.invalid_document_schema")
            for table_name, field_name in ((TABLE, label), (label, COLUMN)):
                with self.assertRaises(module.InventoryError) as raised:
                    module.InventoryFinding(table_name, field_name, "inventory.unapproved_column")
                self.assertEqual(str(raised.exception), "inventory.invalid_document_schema")
        data = document()
        data["model_name"] = "Registry Monitor v2"
        data["tables"][0]["fields"].append(field("Observed Count", origin="measure",
            source=[dict(table_name=TABLE, column_name=COLUMN)]))
        self.assertEqual(self.check(data), ())

    def test_incomplete_empty_duplicate_and_wrong_source_lineage_fail_closed(self):
        mutations = [lambda f: f.pop("lineage_complete"), lambda f: f.update(lineage_complete=False),
                     lambda f: f.update(lineage_complete=1), lambda f: f.update(source_columns=[]),
                     lambda f: f["source_columns"].append(copy.deepcopy(f["source_columns"][0])),
                     lambda f: f["source_columns"][0].update(column_name="city"),
                     lambda f: f.update(source_expression="expression body must never be echoed")]
        for mutate in mutations:
            data = document()
            mutate(data["tables"][0]["fields"][0])
            with self.assertRaises(module.InventoryError) as raised:
                self.check(data)
            self.assertEqual(str(raised.exception), "inventory.invalid_document_schema")

    def test_unknown_keys_every_level_and_wrong_types(self):
        for path in ((), ("tables", 0), ("tables", 0, "fields", 0),
                     ("tables", 0, "fields", 0, "source_columns", 0)):
            data = document()
            target = data
            for key in path:
                target = target[key]
            target["private_detail"] = "never echoed"
            with self.assertRaises(module.InventoryError):
                self.check(data)
        for key in ("schema_version", "model_name", "inventory_source", "tables", "relationships"):
            for value in (None, {}, True, 1.0):
                with self.assertRaises(module.InventoryError):
                    self.check(dict(document(), **{key: value}))
        for key in ("visibility", "origin", "field_name"):
            for value in ({}, [], "bad\nname"):
                data = document()
                data["tables"][0]["fields"][0][key] = value
                with self.assertRaises(module.InventoryError):
                    self.check(data)

    def test_loader_failure_categories_and_valid_read(self):
        with tempfile.TemporaryDirectory(prefix="calico-inventory-") as temp:
            path = Path(temp) / "inventory.json"
            seen = set()
            def reject(payload, category):
                if payload is not None:
                    path.write_bytes(payload)
                with self.assertRaises(module.InventoryError) as raised:
                    module.load_inventory_document(path)
                self.assertEqual(str(raised.exception), category)
                seen.add(category)
            reject(None, "inventory.not_found")
            reject(bytes([255]), "inventory.invalid_encoding")
            reject(b"{", "inventory.invalid_json")
            reject(b"{}", "inventory.invalid_document_schema")
            data = document()
            data["tables"].append(copy.deepcopy(data["tables"][0]))
            reject(json.dumps(data).encode(), "inventory.duplicate_table")
            data = document()
            data["tables"][0]["fields"].append(field())
            reject(json.dumps(data).encode(), "inventory.duplicate_field")
            self.assertEqual(seen, module.INVENTORY_ERROR_CATEGORIES)
            path.write_text(json.dumps(document()), encoding="utf-8")
            self.assertEqual(module.load_inventory_document(path), document())

    def test_schema_closed_keys_and_enums_match(self):
        schema = json.loads((ROOT / "contracts/semantic-model-inventory-v1.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(schema["required"]), set(document()))
        for node in (schema, *schema["$defs"].values()):
            self.assertIs(node["additionalProperties"], False)
            self.assertEqual(set(node["required"]), set(node["properties"]))
            self.assertTrue(all("description" in p for p in node["properties"].values()))
        fields = schema["$defs"]["field"]["properties"]
        self.assertEqual(fields["field_name"]["pattern"], module._LABEL.pattern)
        self.assertEqual(schema["properties"]["model_name"]["pattern"], module._LABEL.pattern)
        self.assertEqual(set(fields["visibility"]["enum"]), module.FIELD_VISIBILITIES)
        self.assertEqual(set(fields["origin"]["enum"]), module.FIELD_ORIGINS)
        self.assertIs(fields["lineage_complete"]["const"], True)
        self.assertNotIn("source_expression", fields)

    def test_relationship_structure_is_closed_and_type_safe(self):
        relation = dict(from_table=TABLE, from_column=COLUMN, to_table=TABLE,
                        to_column=COLUMN, cardinality="one_to_one", cross_filter_direction="both")
        for key in relation:
            for value in (None, {}, [], "unapproved value"):
                data = document()
                data["relationships"] = [dict(relation, **{key: value})]
                with self.assertRaises(module.InventoryError) as raised:
                    self.check(data)
                self.assertEqual(str(raised.exception), "inventory.invalid_document_schema")
        data = document()
        data["relationships"] = [dict(relation, private_detail="never echoed")]
        with self.assertRaises(module.InventoryError):
            self.check(data)

    def test_duplicate_json_keys_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="calico-inventory-") as temp:
            path = Path(temp) / "inventory.json"
            payload = json.dumps(document()).replace('"schema_version": 1', '"schema_version": 2, "schema_version": 1')
            path.write_text(payload, encoding="utf-8")
            with self.assertRaises(module.InventoryError) as raised:
                module.load_inventory_document(path)
            self.assertEqual(str(raised.exception), "inventory.invalid_document_schema")


if __name__ == "__main__":
    unittest.main()
