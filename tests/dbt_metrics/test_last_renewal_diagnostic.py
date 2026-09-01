"""Closed contracts and fixture-DAG proof for the Last Renewal diagnostic."""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from calico_dbt import runner
from tests.fixtures.dbt_foundation.fixture_builder import gate_b_fixture_store_v2

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "dbt/models/marts/mart_last_renewal_diagnostic.sql"
ASSERTION = ROOT / "dbt/tests/assert_last_renewal_diagnostic.sql"
CONTRACT = ROOT / "contracts/metric-denominators-v1.json"
EVIDENCE = ROOT / "docs/evidence/gate-b/last-renewal-diagnostic-v1.json"


class LastRenewalDiagnosticTests(unittest.TestCase):
    def test_fixture_dag_executes_diagnostic_and_independent_assertion(self) -> None:
        outcome = runner.build(mode="fixture", fixture_store_factory=gate_b_fixture_store_v2)
        self.assertEqual(outcome.status, "success", outcome.category)
        self.assertIsNotNone(outcome.proof)
        self.assertEqual(outcome.proof.dbt_model_count, 30)

    def test_exact_three_closed_measures_and_role(self) -> None:
        text = MODEL.read_text(encoding="utf-8")
        for name in (
            "conditional_precision",
            "eligible_exit_sensitivity",
            "all_exit_sensitivity",
        ):
            self.assertEqual(text.count(f"'{name}'"), 1)
        self.assertIn("'release_quality_diagnostic' as diagnostic_role", text)
        for forbidden_ref in (
            "int_delinquency_spells",
            "mart_organization_history",
            "base_admitted_registry_records",
        ):
            self.assertNotIn(forbidden_ref, text)

    def test_presence_semantics_include_unparseable_nonblank_start(self) -> None:
        text = MODEL.read_text(encoding="utf-8")
        self.assertIn("start_source_reported_last_renewal_date_nonblank_unparseable", text)
        self.assertIn("end_source_reported_last_renewal_date_nonblank_unparseable", text)
        self.assertNotIn("date_diff", text.lower())
        self.assertNotIn("datediff", text.lower())

    def test_zero_denominators_are_visible_and_undefined(self) -> None:
        text = MODEL.read_text(encoding="utf-8")
        self.assertIn("when denominator_count = 0 then null", text)
        self.assertIn("numerator_count", text)
        self.assertIn("denominator_count", text)
        self.assertIn("wilson_95_lower", text)
        self.assertIn("wilson_95_upper", text)

    def test_independent_assertion_recomputes_from_transitions(self) -> None:
        text = ASSERTION.read_text(encoding="utf-8")
        self.assertIn("ref('int_entity_transitions')", text)
        self.assertIn("full outer join", text.lower())
        self.assertNotIn("state_charity_registration_number", text)

    def test_contract_ids_match_model_and_are_closed(self) -> None:
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(document["contract_version"], 1)
        definitions = document["denominator_definitions"]
        self.assertEqual(len(definitions), 3)
        ids = {item["id"] for item in definitions}
        self.assertEqual(ids, {
            "starting_delinquent_diagnostic_clears_v1",
            "observed_exits_with_populated_start_v1",
            "all_observed_exits_v1",
        })
        model = MODEL.read_text(encoding="utf-8")
        for denominator_id in ids:
            self.assertIn(denominator_id, model)

    def test_evidence_is_canonical_and_links_the_sql_result(self) -> None:
        raw = EVIDENCE.read_bytes()
        document = json.loads(raw)
        self.assertEqual(raw, (json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode())
        link = document["supersedes"]
        target = ROOT / link["path"]
        self.assertTrue(target.is_file())
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), link["sha256"])
        self.assertEqual(document["interpretation"], "stale_predecessor_observations_not_acceptance_tolerances")
        self.assertNotIn("tolerance", document)


if __name__ == "__main__":
    unittest.main()
