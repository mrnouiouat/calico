"""Contracts for exact-support visuals on Published registry change."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VISUALS = ROOT / "powerbi/Calico.Report/definition/pages/published_registry_change/visuals"


def load(name: str) -> dict:
    return json.loads((VISUALS / name / "visual.json").read_text(encoding="utf-8"))


def query_refs(payload: dict) -> set[str]:
    found: set[str] = set()
    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get("queryRef"), str):
                found.add(value["queryRef"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(payload)
    return found


class RegistryChangePageTests(unittest.TestCase):
    def test_population_chart_uses_only_governed_snapshot_fields(self):
        payload = load("population_chart")
        self.assertEqual(payload["position"], {"x": 24, "y": 408, "z": 310, "height": 224, "width": 768, "tabOrder": 310})
        self.assertEqual(query_refs(payload), {
            "mart_release_snapshot_metrics.as_of_date",
            "mart_release_snapshot_metrics.published_delinquent_category",
            "mart_release_snapshot_metrics.published_delinquent_category_count",
        })
        general = payload["visual"]["visualContainerObjects"]["general"]
        self.assertIn("altText", general[0]["properties"])
        self.assertNotIn("altText", payload["visual"]["visualContainerObjects"])

    def test_movement_keeps_entrant_populations_distinct_and_complete(self):
        expected = {f"mart_adjacent_pair_metrics.{name}" for name in (
            "from_as_of_date from_release_revision to_as_of_date to_release_revision gap_days "
            "total_entrant_count matched_observed_entry_count newly_observed_delinquent_count "
            "observed_exit_count still_delinquent_count not_observed_count net_delinquent_movement "
            "largest_transition_from_status largest_transition_to_status largest_transition_count"
        ).split()}
        self.assertEqual(query_refs(load("movement_support")), expected)

    def test_interval_proportion_has_dates_revisions_denominator_and_supplied_bounds(self):
        expected = {f"mart_adjacent_pair_metrics.{name}" for name in (
            "from_as_of_date from_release_revision to_as_of_date to_release_revision gap_days "
            "observed_exit_count starting_delinquent_count observed_exit_proportion "
            "observed_exit_wilson_95_lower observed_exit_wilson_95_upper"
        ).split()}
        payload = load("interval_context")
        self.assertEqual(query_refs(payload), expected)
        self.assertIn("not available", json.dumps(payload))

    def test_approved_wording_is_verbatim_and_support_is_visible(self):
        contract = json.loads((ROOT / "contracts/claim-support-v1.json").read_text(encoding="utf-8"))
        payload = load("approved_finding")
        self.assertIn(contract["approved_wording"], json.dumps(payload, ensure_ascii=False))
        required = {f"mart_claim_support.{name}" for name in (
            "support_count support_denominator_count from_as_of_date to_as_of_date gap_days "
            "total_matched_entry_count total_entrant_count dominant_source_reported_status_date "
            "dominant_status_date_count dominant_status_date_denominator_count dominant_status_date_share"
        ).split()}
        self.assertEqual(query_refs(payload), required)

    def test_no_named_record_binding_or_unsupported_calculation(self):
        text = " ".join(json.dumps(load(name)).lower() for name in (
            "population_chart", "movement_support", "interval_context", "approved_finding"
        ))
        for prohibited in ("dim_public_organizations", "fct_public_status_observations", "measure", "annualized", "topn"):
            self.assertNotIn(prohibited, text)


if __name__ == "__main__":
    unittest.main()
