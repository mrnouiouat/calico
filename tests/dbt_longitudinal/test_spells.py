"""Actual-dbt integration proof for censoring-aware delinquency spells
(04-03-PLAN.md D-08 through D-11).

Every build here drives the real `calico_dbt.runner.build()` service
against the actual product `../calico/dbt` project (no
`_dbt_project_dir_override`), reusing the same closed
`runner.build(mode="fixture")` harness Plan 02's `test_transitions.py`
established -- no new `runner.FixtureBuildInspection` facade method or
`SELECT_ALIASES` entry is added here (Plan 06 owns the final closed
aliases/facade additions once every Phase 4 model exists, per
04-03-PLAN.md Task 2). Row-level semantic proof of every onset/exit bound,
censoring flag, and loss/reappearance/exit-re-entry boundary comes from the
owned singular test `assert_delinquency_spell_invariants.sql`, which always
runs as part of the same `dbt build` this module drives; this module's own
assertions are limited to the build's safe, non-echo `outcome.status`/
`outcome.category` and closed-schema `SafeBuildProof` counts, plus static
SQL-shape checks confirming the required window/gaps-and-islands techniques
are visibly present in the committed models.

Two fixture panels are exercised, mirroring `test_transitions.py`:

- `gate_b_fixture_store_v2` (Phase 4's five-date/six-revision longitudinal
  successor) is engineered to exercise every D-19 spell edge case: observed
  entry, first-seen (left-censored) onset, observed exit, still delinquent,
  loss/reappearance, and exit/re-entry. A successful
  `outcome.status == "success"` here means the owned singular test's full
  independent recomputation passed over every one of those cases -- any
  bound, censoring, or continuity defect fails that test and therefore
  fails the whole `dbt build`.
- The default fixture factory (`gate_b_fixture_store`, v1) proves this
  plan's two new models and one new singular test do not regress the
  immutable Phase 3 fixture's own full build.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from calico_dbt import runner
from tests.fixtures.dbt_foundation.fixture_builder import gate_b_fixture_store_v2

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INTERMEDIATE_DIR = _REPO_ROOT / "dbt" / "models" / "intermediate"
_OBSERVATION_SEQUENCE_SQL = _INTERMEDIATE_DIR / "int_entity_observation_sequence.sql"
_DELINQUENCY_SPELLS_SQL = _INTERMEDIATE_DIR / "int_delinquency_spells.sql"
_SPELLS_YML = _INTERMEDIATE_DIR / "intermediate_spells.yml"

#: The exact total dbt model count now that this plan's two models
#: (int_entity_observation_sequence, int_delinquency_spells) and
#: 04-05-PLAN.md's four models (int_public_organization_eligibility,
#: mart_registry_population_coverage, dim_public_organizations,
#: fct_public_status_observations) exist alongside the seventeen already-
#: delivered Phase 3/Plan 02/Plan 04 models. An unselected `runner.build`
#: always builds the whole project regardless of which plan's test is
#: driving it, so this constant tracks total project model count, not only
#: this file's own plan's models -- forward-fixed the same way 04-04-
#: PLAN.md forward-fixed this file's own stale count when its three models
#: landed. A mismatch here means either a model failed to build or an
#: unexpected extra/missing model exists in the project.
_EXPECTED_TOTAL_MODEL_COUNT = 23


class FullFixtureBuildWithLongitudinalPanelTests(unittest.TestCase):
    """D-08/D-09/D-10/D-11/D-19: the complete fixture DAG, including this
    plan's two new models and one new singular test, succeeds over the
    Phase 4 longitudinal fixture panel engineered to exercise every named
    spell edge case.
    """

    def test_full_fixture_build_succeeds_with_v2_longitudinal_panel(self) -> None:
        outcome = runner.build(mode="fixture", fixture_store_factory=gate_b_fixture_store_v2)
        self.assertEqual(outcome.status, "success", outcome.category)
        self.assertIsNotNone(outcome.proof)
        self.assertEqual(outcome.proof.dbt_model_count, _EXPECTED_TOTAL_MODEL_COUNT)


class FullFixtureBuildRemainsGreenWithDefaultFixtureTests(unittest.TestCase):
    """Regression proof: this plan's new models/test do not break the
    immutable Phase 3 default (v1) fixture's own full build.
    """

    def test_full_fixture_build_succeeds_with_default_v1_fixture(self) -> None:
        outcome = runner.build(mode="fixture")
        self.assertEqual(outcome.status, "success", outcome.category)
        self.assertIsNotNone(outcome.proof)
        self.assertEqual(outcome.proof.dbt_model_count, _EXPECTED_TOTAL_MODEL_COUNT)


class ObservationSequenceSqlShapeTests(unittest.TestCase):
    """D-08: `int_entity_observation_sequence` visibly computes a global
    ordinal and per-key `lag`/`lead` over the complete keyed sequence
    before any delinquency filtering.
    """

    def test_observation_sequence_refs_keyed_snapshots_and_date_spine(self) -> None:
        content = _OBSERVATION_SEQUENCE_SQL.read_text(encoding="utf-8")
        self.assertIn("ref('int_keyed_snapshots')", content)
        self.assertIn("ref('int_promoted_date_spine')", content)

    def test_observation_sequence_uses_lag_and_lead_over_a_named_window(self) -> None:
        content = _OBSERVATION_SEQUENCE_SQL.read_text(encoding="utf-8").lower()
        self.assertIn("lag(", content)
        self.assertIn("lead(", content)
        self.assertIn("window key_window as", content)
        self.assertIn("row_number() over (order by as_of_date)", content)

    def test_observation_sequence_never_filters_before_computing_neighbors(self) -> None:
        # The neighbor-window CTE must read from the complete keyed
        # observation CTE, never from a delinquency-filtered subset -- the
        # word "where" (a delinquency filter) must not appear before the
        # window computation in the file's own CTE ordering.
        content = _OBSERVATION_SEQUENCE_SQL.read_text(encoding="utf-8").lower()
        window_index = content.index("window key_window as")
        # No `where is_delinquent` filter precedes the neighbor window --
        # that would mean filtering happened before lag/lead, exactly the
        # pitfall D-09/D-10 forbid.
        self.assertNotIn("where is_delinquent", content[:window_index])


class DelinquencySpellsSqlShapeTests(unittest.TestCase):
    """D-08 through D-11, D-21, REQ-sql-techniques: `int_delinquency_spells`
    visibly uses a cumulative window (gaps-and-islands) and never coalesces
    a missing bound into an invented value.
    """

    def test_delinquency_spells_refs_observation_sequence(self) -> None:
        content = _DELINQUENCY_SPELLS_SQL.read_text(encoding="utf-8")
        self.assertIn("ref('int_entity_observation_sequence')", content)

    def test_delinquency_spells_uses_cumulative_window_for_island_numbering(self) -> None:
        content = _DELINQUENCY_SPELLS_SQL.read_text(encoding="utf-8").lower()
        self.assertIn("sum(", content)
        self.assertIn("rows between unbounded preceding and current row", content)

    def test_delinquency_spells_has_closed_mutually_exclusive_terminal_states(self) -> None:
        content = _DELINQUENCY_SPELLS_SQL.read_text(encoding="utf-8")
        for terminal_state in ("observed_exit", "lost_to_observation", "right_censored"):
            self.assertIn(terminal_state, content)

    def test_delinquency_spells_never_coalesces_a_missing_bound(self) -> None:
        content = _DELINQUENCY_SPELLS_SQL.read_text(encoding="utf-8").lower()
        self.assertNotIn("coalesce(onset_left", content)
        self.assertNotIn("coalesce(exit_right", content)

    def test_delinquency_spells_is_materialized_as_a_table(self) -> None:
        content = _DELINQUENCY_SPELLS_SQL.read_text(encoding="utf-8")
        self.assertIn("materialized='table'", content)

    def test_spells_yml_uses_the_exact_published_delinquent_population_phrase(self) -> None:
        content = _SPELLS_YML.read_text(encoding="utf-8")
        self.assertIn("published delinquent population", content)


if __name__ == "__main__":
    unittest.main()
