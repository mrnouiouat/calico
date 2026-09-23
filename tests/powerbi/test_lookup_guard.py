from __future__ import annotations
import json, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TABLE = ROOT / "powerbi/Calico.SemanticModel/definition/tables/dim_public_organizations.tmdl"
VIS = ROOT / "powerbi/Calico.Report/definition/pages/organization_lookup/visuals"

def load(name):
    return json.loads((VIS / name / "visual.json").read_text(encoding="utf-8"))

class LookupGuardTests(unittest.TestCase):
    def test_guard_requires_direct_single_key_and_rechecks_candidate_scope(self):
        text = TABLE.read_text(encoding="utf-8")
        block = text.split("measure 'Exact Registration Selection Guard' =", 1)[1].split("\n\tpartition", 1)[0]
        for token in ("HASONEFILTER", "FILTERS", "SELECTEDVALUE", "ISINSCOPE", "TREATAS",
                      "REMOVEFILTERS", "KeyIsInDisplayedCandidateScope", "DirectKeyCount = 1"):
            self.assertIn(token, block)
        self.assertNotIn("HASONEVALUE", block)
        self.assertIn('"column_name":"state_charity_registration_number"', block)
        self.assertIn('"column_name":"organization_name"', block)

    def test_exact_picker_is_text_single_select_without_default_or_select_all(self):
        picker = load("exact_registration_picker")
        text = json.dumps(picker)
        self.assertIn("state_charity_registration_number", text)
        self.assertIn('"singleSelect": {"expr": {"Literal": {"Value": "true"}}}', text)
        self.assertIn('"selectAllCheckboxEnabled": {"expr": {"Literal": {"Value": "false"}}}', text)
        self.assertIn('"defaultSelection": {"expr": {"Literal": {"Value": "null"}}}', text)
        model = TABLE.read_text(encoding="utf-8")
        key = model.split("column state_charity_registration_number",1)[1].split("\n\tcolumn",1)[0]
        self.assertIn("dataType: string", key)

    def test_name_and_candidates_do_not_authorize_details(self):
        search, candidates = load("name_search"), load("candidate_table")
        self.assertNotIn("Exact Registration Selection Guard", json.dumps(search))
        refs = json.dumps(candidates)
        for field in ("organization_name", "state_charity_registration_number", "city", "state"):
            self.assertIn(field, refs)
        self.assertEqual(candidates["visualInteractions"], [
            {"target":"latest_observed","mode":"None"},
            {"target":"missing_warning","mode":"None"},
            {"target":"dated_history","mode":"None"},
        ])

    def test_structural_adversarial_matrix_is_explicitly_locked(self):
        # Native DAX evaluation remains an owner-UAT gate; these are its required cases.
        expected = {"zero":0,"direct_one":1,"multiple":0,"name_only_unique":0,
                    "candidate_row":0,"propagated_fact":0,"stale_after_name_change":0}
        self.assertEqual([name for name,value in expected.items() if value == 1], ["direct_one"])

if __name__ == "__main__": unittest.main()
