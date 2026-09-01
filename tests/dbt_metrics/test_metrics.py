"""Contract and actual-dbt tests for the adjacent-pair metric tracer.

Python verifies repository shape and SQL-produced dbt outcomes only.  It
does not calculate a business metric.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from calico_dbt import runner
from tests.fixtures.dbt_foundation.fixture_builder import gate_b_fixture_store_v2


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MACRO = _REPO_ROOT / "dbt" / "macros" / "wilson_interval.sql"
_MODEL = _REPO_ROOT / "dbt" / "models" / "marts" / "mart_adjacent_pair_metrics.sql"
_METADATA = _REPO_ROOT / "dbt" / "models" / "marts" / "metrics.yml"
_ARITHMETIC = _REPO_ROOT / "dbt" / "tests" / "assert_metric_arithmetic.sql"


class AdjacentPairMetricDbtTests(unittest.TestCase):
    def test_longitudinal_fixture_builds_the_production_metric_dag(self) -> None:
        outcome = runner.build(
            mode="fixture", fixture_store_factory=gate_b_fixture_store_v2
        )
        self.assertEqual(outcome.status, "success", outcome.category)
        self.assertIsNotNone(outcome.proof)
        self.assertEqual(outcome.proof.dbt_model_count, 31)


class AdjacentPairMetricContractTests(unittest.TestCase):
    def test_model_preserves_exact_pair_identity_and_gap(self) -> None:
        content = _MODEL.read_text(encoding="utf-8")
        for column in (
            "from_as_of_date",
            "from_release_revision",
            "from_revision_fingerprint",
            "to_as_of_date",
            "to_release_revision",
            "to_revision_fingerprint",
            "gap_days",
        ):
            self.assertIn(column, content)
        self.assertIn("ref('int_entity_transitions')", content)
        self.assertIn("ref('int_transition_matrix')", content)

    def test_zero_denominators_leave_proportions_and_wilson_bounds_null(self) -> None:
        model = _MODEL.read_text(encoding="utf-8").lower()
        macro = _MACRO.read_text(encoding="utf-8").lower()
        self.assertIn("when starting_delinquent_count = 0 then null", model)
        self.assertIn("when {{ denominator_count }} = 0 then null", macro)
        self.assertNotRegex(model, r"coalesce\s*\([^)]*(proportion|wilson)")

    def test_entry_populations_and_closed_denominators_are_distinct(self) -> None:
        content = _MODEL.read_text(encoding="utf-8")
        for token in (
            "matched_observed_entry_count",
            "newly_observed_delinquent_count",
            "total_entrant_count",
            "starting_published_delinquent_cohort_v1",
            "matched_observed_entry_population_v1",
            "all_entrant_population_v1",
        ):
            self.assertIn(token, content)

    def test_net_movement_and_largest_transition_are_sql_owned(self) -> None:
        model = _MODEL.read_text(encoding="utf-8")
        arithmetic = _ARITHMETIC.read_text(encoding="utf-8")
        self.assertIn(
            "ending_published_delinquent_count - starting_delinquent_count",
            model,
        )
        self.assertIn("row_number() over", model.lower())
        self.assertIn("transition_count desc", model.lower())
        self.assertIn("normalized_from_status asc", model.lower())
        self.assertIn("normalized_to_status asc", model.lower())
        self.assertIn("__NULL_STATUS__", model)
        self.assertIn("expected_largest_transition", arithmetic)

    def test_metadata_is_grain_first_and_documents_null_sort_token(self) -> None:
        content = _METADATA.read_text(encoding="utf-8")
        self.assertRegex(
            content,
            r"(?s)name: mart_adjacent_pair_metrics\s+description: >\s+Grain:",
        )
        self.assertIn("__NULL_STATUS__", content)

    def test_metric_surface_has_no_unsupported_rate_or_judgment_fields(self) -> None:
        content = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (_MODEL, _METADATA)
        ).lower()
        prohibited_field_fragments = (
            "annualized_",
            "standardized_30_day",
            "constant_hazard",
            "survival_estimate",
            "organization_score",
            "organization_rank",
            "risk_score",
            "quality_score",
            "threshold_value",
        )
        for fragment in prohibited_field_fragments:
            self.assertNotIn(fragment, content)
        self.assertIsNone(re.search(r"state_charity_registration_number|\bfein\b|\bein\b", content))

    def test_arithmetic_failure_projection_is_identity_free(self) -> None:
        content = _ARITHMETIC.read_text(encoding="utf-8").lower()
        self.assertNotIn("state_charity_registration_number", content)
        self.assertNotRegex(content, r"\bfein\b|\bein\b")
        self.assertIn("failure_reason", content)


if __name__ == "__main__":
    unittest.main()
