"""Contracts for exported persistence, age and censoring visuals."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VISUALS = ROOT / "powerbi/Calico.Report/definition/pages/cohort_persistence/visuals"


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


class CohortPageTests(unittest.TestCase):
    def test_persistence_keeps_three_states_and_complete_support(self):
        expected = {f"mart_starting_cohort_persistence.{name}" for name in (
            "from_as_of_date from_release_revision to_as_of_date to_release_revision gap_days "
            "starting_delinquent_count still_delinquent_count observed_exit_count not_observed_count "
            "persistence_proportion"
        ).split()}
        payload = load("persistence_support")
        self.assertEqual(query_refs(payload), expected)
        self.assertIn("not available", json.dumps(payload))
        self.assertNotIn("wilson", json.dumps(payload).lower())

    def test_age_is_complete_prevalent_snapshot_without_bins_or_top_n(self):
        payload = load("status_age")
        self.assertEqual(query_refs(payload), {f"mart_source_reported_status_age.{name}" for name in (
            "as_of_date release_revision source_reported_status_age_state source_reported_status_age_days record_count"
        ).split()})
        text = json.dumps(payload)
        title = payload["visual"]["visualContainerObjects"]["title"][0]["properties"]["text"]["expr"]["Literal"]["Value"]
        self.assertEqual(title, "'Source-reported status age — prevalent-snapshot description'")
        self.assertNotIn("TopN", text)
        self.assertNotIn("bin", text.lower())

    def test_censoring_is_separate_from_observed_exit(self):
        payload = load("spell_censoring")
        self.assertEqual(query_refs(payload), {
            "mart_spell_censoring_summary.terminal_state",
            "mart_spell_censoring_summary.spell_count",
        })
        text = json.dumps(payload).lower()
        self.assertIn("disappearance is separate from an observed exit", text)
        self.assertIn("do not establish an exact onset or exit date", text)

    def test_layout_and_tab_order_are_explicit(self):
        positions = [load(name)["position"] for name in ("persistence_support", "status_age", "spell_censoring")]
        self.assertEqual([(p["y"], p["height"]) for p in positions], [(312, 304), (632, 224), (872, 208)])
        self.assertEqual(len({p["tabOrder"] for p in positions}), 3)

    def test_no_named_record_binding_or_derived_duration(self):
        payloads = [load(name) for name in ("persistence_support", "status_age", "spell_censoring")]
        text = " ".join(json.dumps(payload).lower() for payload in payloads)
        for prohibited in ("dim_public_organizations", "fct_public_status_observations", "measure", "survival"):
            self.assertNotIn(prohibited, text)
        self.assertTrue(all("duration" not in ref for payload in payloads for ref in query_refs(payload)))


if __name__ == "__main__":
    unittest.main()
