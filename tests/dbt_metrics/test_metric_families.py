"""Contract and fixture-DAG tests for the separate v1 metric families."""
from __future__ import annotations

import unittest
from pathlib import Path

from calico_dbt import runner
from tests.fixtures.dbt_foundation.fixture_builder import gate_b_fixture_store_v2

ROOT = Path(__file__).resolve().parents[2]
MARTS = ROOT / "dbt" / "models" / "marts"


class MetricFamilyTests(unittest.TestCase):
    def test_fixture_builds_all_metric_families(self) -> None:
        outcome = runner.build(mode="fixture", fixture_store_factory=gate_b_fixture_store_v2)
        self.assertEqual(outcome.status, "success", outcome.category)
        self.assertIsNotNone(outcome.proof)
        self.assertEqual(outcome.proof.dbt_model_count, 30)

    def test_snapshot_keeps_locked_categories_and_denominators(self) -> None:
        text = (MARTS / "mart_release_snapshot_metrics.sql").read_text(encoding="utf-8")
        for value in (
            "Delinquent", "Delinquent - Late Fees Due", "keyed_record_count",
            "unkeyed_record_count", "raw_total_record_count",
            "all_promoted_release_records_v1", "wilson_interval",
        ):
            self.assertIn(value, text)
        self.assertIn("keyed_record_count, 0) as keyed_record_count", text)
        self.assertIn("unkeyed_record_count, 0) as unkeyed_record_count", text)

    def test_time_semantics_are_honest(self) -> None:
        cohort = (MARTS / "mart_starting_cohort_persistence.sql").read_text(encoding="utf-8")
        age = (MARTS / "mart_source_reported_status_age.sql").read_text(encoding="utf-8")
        for column in ("from_as_of_date", "to_as_of_date", "gap_days", "not_observed_count"):
            self.assertIn(column, cohort)
        self.assertIn("source_reported_status_age_days", age)
        self.assertIn("invalid_nonblank", age)
        self.assertNotIn("onset_date", age.lower())

    def test_spell_and_release_quality_vocabularies_are_closed(self) -> None:
        spell = (MARTS / "mart_spell_censoring_summary.sql").read_text(encoding="utf-8")
        quality = (MARTS / "mart_release_quality.sql").read_text(encoding="utf-8")
        self.assertIn("ref('int_delinquency_spells')", spell)
        for value in ("capture_attempted_count", "capture_succeeded_count", "capture_failed_count",
                      "capture_unavailable_count", "schema_added_column_count", "schema_removed_column_count",
                      "schema_type_changed_column_count", "schema_contract_version", "parser_contract_version"):
            self.assertIn(value, quality)
        for prohibited in ("risk_score", "quality_score", "organization_rank", "recommendation"):
            self.assertNotIn(prohibited, quality.lower())

    def test_metric_models_project_no_identity(self) -> None:
        text = "\n".join(path.read_text(encoding="utf-8") for path in MARTS.glob("mart_*metrics.sql"))
        self.assertNotIn("state_charity_registration_number", text)


if __name__ == "__main__":
    unittest.main()
