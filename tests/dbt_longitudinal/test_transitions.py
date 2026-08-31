"""Actual-dbt integration proof for keyed snapshots, unkeyed coverage, and
exact-key adjacent transitions (04-02-PLAN.md D-04/D-05/D-06).

Every build here drives the real `calico_dbt.runner.build()` service
against the actual product `../calico/dbt` project (no
`_dbt_project_dir_override`). Two fixture panels are exercised:

- `gate_b_fixture_store_v2` (Phase 4's five-date/six-revision longitudinal
  successor) proves the full DAG -- including this plan's four new models
  and four new singular tests -- succeeds over a panel deliberately
  engineered to exercise every D-19 longitudinal edge case (entry, still
  delinquent, observed exit, newly observed, disappearance,
  loss/reappearance, exit/re-entry, and same-date promotion). A successful
  `outcome.status == "success"` here is itself the row-level semantic
  proof: any endpoint-union, pair-locality, classification, or
  reconciliation defect fails the owned singular tests and therefore fails
  the whole `dbt build`.
- The default fixture factory (`gate_b_fixture_store`, v1) proves this
  plan's new models and tests do not regress the immutable Phase 3
  fixture's own full build.

This module never adds a new `runner.FixtureBuildInspection` facade method
or a new `SELECT_ALIASES` entry (Plan 06 owns the final closed
aliases/facade additions once every Phase 4 model exists, per
04-02-PLAN.md Task 2). Every assertion instead uses the existing full
fixture build's safe, non-echo `outcome.status`/`outcome.category` and the
closed-schema `SafeBuildProof` counts -- never a path, row, or excluded
value.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from calico_dbt import runner
from tests.fixtures.dbt_foundation.fixture_builder import gate_b_fixture_store_v2

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INTERMEDIATE_DIR = _REPO_ROOT / "dbt" / "models" / "intermediate"
_KEYED_SNAPSHOTS_SQL = _INTERMEDIATE_DIR / "int_keyed_snapshots.sql"
_UNKEYED_COVERAGE_SQL = _INTERMEDIATE_DIR / "int_unkeyed_coverage.sql"
_ENTITY_TRANSITIONS_SQL = _INTERMEDIATE_DIR / "int_entity_transitions.sql"
_TRANSITION_MATRIX_SQL = _INTERMEDIATE_DIR / "int_transition_matrix.sql"

#: The exact total dbt model count now that Plan 02's four models,
#: Plan 04's three models (04-04-PLAN.md: stg_capture_attempts,
#: int_capture_runs, int_release_flags), Plan 03's two models
#: (04-03-PLAN.md: int_entity_observation_sequence, int_delinquency_spells),
#: and Plan 05's four models (04-05-PLAN.md: int_public_organization_
#: eligibility, mart_registry_population_coverage, dim_public_organizations,
#: fct_public_status_observations) all exist alongside the ten already-
#: delivered Phase 3 models (Wave 3-5). An unselected `runner.build` always
#: builds the whole project regardless of which plan's test is driving it,
#: so this constant tracks total project model count, not only this file's
#: own Plan 02 models -- forward-fixed the same way Plan 04 forward-fixed
#: test_catalog.py's and test_repository_contract.py's own stale exact-count
#: assertions, and the same way this file's own constant was forward-fixed
#: from 14 to 17 to 19 to 23 as each later plan landed. A mismatch here
#: means either a model failed to build or an unexpected extra/missing
#: model exists in the project.
_EXPECTED_TOTAL_MODEL_COUNT = 23


class FullFixtureBuildWithLongitudinalPanelTests(unittest.TestCase):
    """D-04/D-05/D-06/D-19: the complete fixture DAG, including this
    plan's four new models and four new singular tests, succeeds over the
    Phase 4 longitudinal fixture panel engineered to exercise every named
    edge case.
    """

    def test_full_fixture_build_succeeds_with_v2_longitudinal_panel(self) -> None:
        outcome = runner.build(mode="fixture", fixture_store_factory=gate_b_fixture_store_v2)
        self.assertEqual(outcome.status, "success", outcome.category)
        self.assertIsNotNone(outcome.proof)
        self.assertEqual(outcome.proof.dbt_model_count, _EXPECTED_TOTAL_MODEL_COUNT)


class FullFixtureBuildRemainsGreenWithDefaultFixtureTests(unittest.TestCase):
    """Regression proof: Plan 02's new models/tests do not break the
    immutable Phase 3 default (v1) fixture's own full build.
    """

    def test_full_fixture_build_succeeds_with_default_v1_fixture(self) -> None:
        outcome = runner.build(mode="fixture")
        self.assertEqual(outcome.status, "success", outcome.category)
        self.assertIsNotNone(outcome.proof)
        self.assertEqual(outcome.proof.dbt_model_count, _EXPECTED_TOTAL_MODEL_COUNT)


class KeyedSnapshotSqlShapeTests(unittest.TestCase):
    """D-04: `int_keyed_snapshots` selects the exact disposition predicate."""

    def test_keyed_snapshots_uses_exact_eligible_predicate(self) -> None:
        content = _KEYED_SNAPSHOTS_SQL.read_text(encoding="utf-8")
        self.assertIn("disposition = 'eligible_for_keyed_path'", content)
        self.assertIn("ref('int_registry_record_dispositions')", content)

    def test_unkeyed_coverage_groups_the_row_level_relation(self) -> None:
        content = _UNKEYED_COVERAGE_SQL.read_text(encoding="utf-8")
        self.assertIn("ref('int_keyless_registry_coverage')", content)
        self.assertIn("group by", content.lower())


class EntityTransitionsSqlShapeTests(unittest.TestCase):
    """D-05/D-06/REQ-sql-techniques: the transition model visibly uses the
    required exact-key join, anti-join, and union techniques.
    """

    def test_entity_transitions_joins_pairs_and_keyed_snapshots(self) -> None:
        content = _ENTITY_TRANSITIONS_SQL.read_text(encoding="utf-8")
        self.assertIn("ref('int_adjacent_release_pairs')", content)
        self.assertIn("ref('int_keyed_snapshots')", content)

    def test_entity_transitions_uses_anti_join_and_three_way_union(self) -> None:
        # Counted over the whole file (comments included), so this asserts
        # a floor, not an exact count: the model's two anti-join branches
        # and explanatory prose both use the exact phrase.
        content = _ENTITY_TRANSITIONS_SQL.read_text(encoding="utf-8").lower()
        self.assertGreaterEqual(content.count("anti join"), 2)
        self.assertGreaterEqual(content.count("union all"), 2)

    def test_entity_transitions_never_coalesces_missing_status(self) -> None:
        content = _ENTITY_TRANSITIONS_SQL.read_text(encoding="utf-8").lower()
        self.assertNotIn("coalesce(start_status", content)
        self.assertNotIn("coalesce(end_status", content)


class TransitionMatrixSqlShapeTests(unittest.TestCase):
    """REQ-sql-techniques: `int_transition_matrix` is a grouped internal
    helper, never a headline mart.
    """

    def test_transition_matrix_groups_over_entity_transitions(self) -> None:
        content = _TRANSITION_MATRIX_SQL.read_text(encoding="utf-8")
        self.assertIn("ref('int_entity_transitions')", content)
        self.assertIn("group by", content.lower())
        self.assertIn("count(*)", content.lower())


if __name__ == "__main__":
    unittest.main()
