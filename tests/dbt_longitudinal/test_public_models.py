"""Actual-dbt integration proof for the governed public-history slice
(04-05-PLAN.md D-15/D-16/D-17/D-18).

Three layers of proof, mirroring Plan 02/04's own `test_transitions.py` /
`test_capture_runs.py` precedent of relying on dbt's own singular tests plus
a successful full build rather than a new `runner.FixtureBuildInspection`
facade method (deferred to Plan 06, per this plan's own Task 2 action text):

1. `PreflightPublicEligibilityBindingTests` drives
   `calico_dbt.preflight.prepare_runtime_input` directly (no dbt subprocess)
   to prove the fixed nullable
   `runtime_input.public_eligibility_classifications` schema is identical
   whether the store has zero, several, or a malformed sidecar, and that
   containment/vocabulary/duplicate-key violations fail preflight closed.
2. `PublicModelsFixtureBuildTests` builds a custom store -- the real
   `gate_b_fixture_store_v2` longitudinal panel plus a hand-written private
   eligibility sidecar exercising all three closed states, including one
   key deliberately left out of the sidecar entirely (proving the missing-
   match-normalizes-to-unclassified default) -- and proves the complete
   fixture DAG, including this plan's three new singular tests, builds
   successfully over that panel. A successful `outcome.status == "success"`
   here is itself the row-level semantic proof: any eligibility, aggregate,
   or public-projection defect fails one of
   `assert_population_coverage_reconciliation.sql`,
   `assert_public_organization_eligibility.sql`, or
   `assert_public_status_observation_reconciliation.sql` and therefore
   fails the whole `dbt build`.
3. `FullFixtureBuildRemainsGreenWithDefaultFixtureTests` and the SQL-shape
   classes prove the immutable default (v1) fixture still builds with no
   sidecar at all (empty eligibility relation, zero public rows, no build
   failure) and that the required allowed-identity/excluded-category
   boundary is visibly present in the committed SQL.

No real organization identity or excluded value is used anywhere -- only
invented synthetic values and the project's own existing safe fixtures.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from calico_dbt import catalog as cat
from calico_dbt import preflight, runner
from calico_dbt.eligibility import EligibilityError, load_eligibility_classifications
from calico_landing.admission import admit
from calico_landing.store import ensure_store_layout
from tests.fixtures.dbt_foundation.fixture_builder import gate_b_fixture_store_v2

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INTERMEDIATE_DIR = _REPO_ROOT / "dbt" / "models" / "intermediate"
_MARTS_DIR = _REPO_ROOT / "dbt" / "models" / "marts"
_ELIGIBILITY_SQL = _INTERMEDIATE_DIR / "int_public_organization_eligibility.sql"
_COVERAGE_MART_SQL = _MARTS_DIR / "mart_registry_population_coverage.sql"
_DIM_ORGANIZATIONS_SQL = _MARTS_DIR / "dim_public_organizations.sql"
_FCT_OBSERVATIONS_SQL = _MARTS_DIR / "fct_public_status_observations.sql"
_PREFLIGHT_PY = _REPO_ROOT / "calico_dbt" / "preflight.py"
_ELIGIBILITY_PY = _REPO_ROOT / "calico_dbt" / "eligibility.py"

_BASELINE_CANDIDATE_ROOT = _REPO_ROOT / "tests" / "fixtures" / "landing" / "valid"

#: The exact total dbt model count now that this plan's four models
#: (int_public_organization_eligibility, mart_registry_population_coverage,
#: dim_public_organizations, fct_public_status_observations) exist
#: alongside the nineteen already-delivered Phase 3/Plan 02/Plan 03/Plan 04
#: models. An unselected `runner.build` always builds the whole project
#: regardless of which plan's test is driving it, so this constant tracks
#: total project model count, not only this file's own plan's models --
#: mirrors the identical forward-fix this plan applied to
#: test_transitions.py, test_spells.py, and test_capture_runs.py. A
#: mismatch here means either a model failed to build or an unexpected
#: extra/missing model exists in the project.
#:
#: 23 -> 31 at Phase 5 closure (05-05-PLAN.md Task 3): Plans 01-04
#: cumulatively added eight new marts (mart_release_snapshot_metrics,
#: mart_adjacent_pair_metrics, mart_starting_cohort_persistence,
#: mart_source_reported_status_age, mart_spell_censoring_summary,
#: mart_release_quality, mart_last_renewal_diagnostic, mart_claim_support).
_EXPECTED_TOTAL_MODEL_COUNT = 31

#: Synthetic fixture v2 keys used to prove all three closed eligibility
#: states plus the missing-match default (D-18). Chosen from the real
#: `gate-b-fixture-v2.json` longitudinal panel so the eligibility left join
#: exercises genuinely multi-release keys, never invented identities.
_ELIGIBLE_ENTRY_KEY = "9210001"  # observed at d0 (non-delinquent) then d1 (delinquent)
_ELIGIBLE_REAPPEARANCE_KEY = "CT910020"  # observed at d1 and d3 only (loss/reappearance)
_AMBIGUOUS_KEY = "9210002"  # observed at d1 and d2 (still delinquent)
_EXPLICIT_UNCLASSIFIED_KEY = "CT910030"  # observed at d2, d3, d4 (exit/re-entry)
#: Deliberately never listed in the sidecar at all -- proves the missing-
#: match-normalizes-to-'unclassified' default (T-04-05B), not an explicit
#: reviewed state.
_UNLISTED_KEY = "9210003"


def _write_eligibility_sidecar(store_root: Path) -> None:
    """Write one hand-shaped private eligibility sidecar directly into a
    fixture store, bypassing every production writer -- legitimate only as
    test setup, mirroring how `test_capture_runs.py`'s
    `_write_raw_attempt_json` places hand-written attempt shapes no
    production code path can itself produce today.
    """

    document = {
        "schema_version": 1,
        "classification_version": "public-models-fixture-v1",
        "classifications": [
            {"registration_number": _ELIGIBLE_ENTRY_KEY, "classification": "eligible"},
            {"registration_number": _ELIGIBLE_REAPPEARANCE_KEY, "classification": "eligible"},
            {"registration_number": _AMBIGUOUS_KEY, "classification": "ambiguous_natural_person"},
            {"registration_number": _EXPLICIT_UNCLASSIFIED_KEY, "classification": "unclassified"},
        ],
    }
    (store_root / "public-eligibility-v1.json").write_text(
        json.dumps(document, sort_keys=True), encoding="utf-8"
    )


@contextmanager
def _public_models_fixture_store() -> Iterator[object]:
    """The real v2 longitudinal panel plus this plan's own hand-written
    private eligibility sidecar, exercising all three closed states and the
    missing-match default (D-18). `gate_b_fixture_store_v2` itself is
    completely unmodified; this is a thin additive wrapper, mirroring the
    same pattern `test_capture_runs.py`'s `_capture_fixture_store` uses.
    """

    with gate_b_fixture_store_v2() as store:
        _write_eligibility_sidecar(store.store_root)
        yield store


class PreflightPublicEligibilityBindingTests(unittest.TestCase):
    """D-16/D-18/D-20: `prepare_runtime_input` always creates the fixed
    nullable `runtime_input.public_eligibility_classifications` schema -- as
    an empty relation when the store has no sidecar, one row per entry when
    it does, and a fail-closed `PreflightError` when the sidecar is
    malformed, carries a duplicate key, or is a symlink/reparse alias
    (T-04-05A). Every check here is a direct Python-level assertion against
    `information_schema`/row content, never a raw path or document echo.
    """

    _EXPECTED_COLUMNS = ("registration_number", "classification", "classification_version")

    def _columns(self, duckdb_path: Path) -> list[str]:
        connection = duckdb.connect(str(duckdb_path), read_only=True)
        try:
            rows = connection.execute(
                "select column_name from information_schema.columns "
                "where table_schema = 'runtime_input' "
                "and table_name = 'public_eligibility_classifications' "
                "order by ordinal_position"
            ).fetchall()
        finally:
            connection.close()
        return [row[0] for row in rows]

    def test_empty_store_binds_an_empty_but_correctly_shaped_relation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            ensure_store_layout(store_root)
            temp_root = Path(tmp) / "runtime"
            temp_root.mkdir()

            binding = preflight.prepare_runtime_input(
                store_root=store_root,
                catalog=cat.InputCatalog(contract_version=1, releases=()),
                temp_root=temp_root,
            )
            self.assertEqual(binding.verified_eligibility_classification_count, 0)
            self.assertEqual(list(self._columns(binding.duckdb_path)), list(self._EXPECTED_COLUMNS))

            connection = duckdb.connect(str(binding.duckdb_path), read_only=True)
            try:
                row_count = connection.execute(
                    "select count(*) from runtime_input.public_eligibility_classifications"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(row_count, 0)

    def test_admitted_store_with_sidecar_binds_rows_with_correct_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            _write_eligibility_sidecar(layout.store_root)

            temp_root = Path(tmp) / "runtime"
            temp_root.mkdir()

            binding = preflight.prepare_runtime_input(
                store_root=store_root,
                catalog=cat.InputCatalog(contract_version=1, releases=()),
                temp_root=temp_root,
            )
            self.assertEqual(binding.verified_eligibility_classification_count, 4)
            self.assertEqual(list(self._columns(binding.duckdb_path)), list(self._EXPECTED_COLUMNS))

            connection = duckdb.connect(str(binding.duckdb_path), read_only=True)
            try:
                rows = connection.execute(
                    "select registration_number, classification, classification_version "
                    "from runtime_input.public_eligibility_classifications "
                    "order by registration_number"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(
                rows,
                sorted(
                    [
                        (_ELIGIBLE_ENTRY_KEY, "eligible", "public-models-fixture-v1"),
                        (_ELIGIBLE_REAPPEARANCE_KEY, "eligible", "public-models-fixture-v1"),
                        (_AMBIGUOUS_KEY, "ambiguous_natural_person", "public-models-fixture-v1"),
                        (_EXPLICIT_UNCLASSIFIED_KEY, "unclassified", "public-models-fixture-v1"),
                    ]
                ),
            )

    def test_absent_sidecar_after_real_admission_binds_empty_relation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            result = admit(_BASELINE_CANDIDATE_ROOT, store_root)
            self.assertEqual(result.status, "accepted")

            manifest_path = (
                store_root
                / "releases"
                / result.as_of_date
                / f"rev-{result.release_revision:04d}-{result.revision_fingerprint[:8]}"
                / "manifest.json"
            )
            catalog = cat.build_catalog_from_manifests(
                [
                    (
                        result.as_of_date,
                        result.release_revision,
                        result.revision_fingerprint,
                        manifest_path.read_bytes(),
                    )
                ]
            )

            temp_root = Path(tmp) / "runtime"
            temp_root.mkdir()
            binding = preflight.prepare_runtime_input(
                store_root=store_root, catalog=catalog, temp_root=temp_root
            )
            self.assertEqual(binding.verified_eligibility_classification_count, 0)

    def test_malformed_sidecar_fails_preflight_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            (layout.store_root / "public-eligibility-v1.json").write_text(
                "{not-json", encoding="utf-8"
            )

            temp_root = Path(tmp) / "runtime"
            temp_root.mkdir()

            with self.assertRaises(preflight.PreflightError) as ctx:
                preflight.prepare_runtime_input(
                    store_root=store_root,
                    catalog=cat.InputCatalog(contract_version=1, releases=()),
                    temp_root=temp_root,
                )
            self.assertEqual(ctx.exception.category, "preflight.public_eligibility_invalid")

    def test_duplicate_registration_key_fails_preflight_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            document = {
                "schema_version": 1,
                "classification_version": "duplicate-key-test-v1",
                "classifications": [
                    {"registration_number": "9210001", "classification": "eligible"},
                    {"registration_number": "9210001", "classification": "unclassified"},
                ],
            }
            (layout.store_root / "public-eligibility-v1.json").write_text(
                json.dumps(document), encoding="utf-8"
            )

            temp_root = Path(tmp) / "runtime"
            temp_root.mkdir()

            with self.assertRaises(preflight.PreflightError) as ctx:
                preflight.prepare_runtime_input(
                    store_root=store_root,
                    catalog=cat.InputCatalog(contract_version=1, releases=()),
                    temp_root=temp_root,
                )
            self.assertEqual(ctx.exception.category, "preflight.public_eligibility_invalid")

    def test_unknown_classification_value_fails_preflight_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            document = {
                "schema_version": 1,
                "classification_version": "unknown-value-test-v1",
                "classifications": [
                    {"registration_number": "9210001", "classification": "trusted_partner"},
                ],
            }
            (layout.store_root / "public-eligibility-v1.json").write_text(
                json.dumps(document), encoding="utf-8"
            )

            temp_root = Path(tmp) / "runtime"
            temp_root.mkdir()

            with self.assertRaises(preflight.PreflightError) as ctx:
                preflight.prepare_runtime_input(
                    store_root=store_root,
                    catalog=cat.InputCatalog(contract_version=1, releases=()),
                    temp_root=temp_root,
                )
            self.assertEqual(ctx.exception.category, "preflight.public_eligibility_invalid")

    @unittest.skipUnless(sys.platform != "win32", "symlink creation needs elevated privilege on Windows")
    def test_symlinked_sidecar_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)

            real_path = Path(tmp) / "real-eligibility.json"
            _write_eligibility_sidecar(Path(tmp))
            (Path(tmp) / "public-eligibility-v1.json").rename(real_path)
            alias_path = layout.store_root / "public-eligibility-v1.json"
            alias_path.symlink_to(real_path)

            with self.assertRaises(EligibilityError) as ctx:
                load_eligibility_classifications(layout.store_root)
            self.assertEqual(ctx.exception.category, "eligibility.link_rejected")


class PublicModelsFixtureBuildTests(unittest.TestCase):
    """The complete fixture DAG, including this plan's four new models and
    three new singular tests, succeeds over the real v2 longitudinal panel
    plus a private eligibility sidecar exercising every closed state and the
    missing-match default (D-15/D-16/D-18).
    """

    def test_full_fixture_build_succeeds_with_mixed_eligibility_panel(self) -> None:
        outcome = runner.build(mode="fixture", fixture_store_factory=_public_models_fixture_store)
        self.assertEqual(outcome.status, "success", outcome.category)
        self.assertIsNotNone(outcome.proof)
        self.assertEqual(outcome.proof.dbt_model_count, _EXPECTED_TOTAL_MODEL_COUNT)


class FullFixtureBuildRemainsGreenWithDefaultFixtureTests(unittest.TestCase):
    """Regression proof: this plan's new models/tests do not break the
    immutable Phase 3 default (v1) fixture's own full build, which carries
    no eligibility sidecar at all -- proving real mode's "absent sidecar
    binds an empty relation and never fails the build" contract end to end
    through the actual dbt DAG, not only at the Python preflight layer.
    """

    def test_full_fixture_build_succeeds_with_default_v1_fixture(self) -> None:
        outcome = runner.build(mode="fixture")
        self.assertEqual(outcome.status, "success", outcome.category)
        self.assertIsNotNone(outcome.proof)
        self.assertEqual(outcome.proof.dbt_model_count, _EXPECTED_TOTAL_MODEL_COUNT)


class EligibilitySqlShapeTests(unittest.TestCase):
    """D-18/T-04-05B: `int_public_organization_eligibility` left joins the
    private source and normalizes a missing match to 'unclassified', never
    a fuzzy/name heuristic or score (T-04-05F).
    """

    def test_eligibility_model_left_joins_the_private_source(self) -> None:
        content = _ELIGIBILITY_SQL.read_text(encoding="utf-8")
        self.assertIn("left join", content.lower())
        self.assertIn("source('runtime_input', 'public_eligibility_classifications')", content)

    def test_eligibility_model_normalizes_missing_match_to_unclassified(self) -> None:
        content = _ELIGIBILITY_SQL.read_text(encoding="utf-8")
        self.assertIn("coalesce(classifications.classification, 'unclassified')", content)

    def test_eligibility_boundary_has_no_fuzzy_or_scoring_language(self) -> None:
        for path in (_ELIGIBILITY_SQL, _ELIGIBILITY_PY):
            content = path.read_text(encoding="utf-8").lower()
            for forbidden in ("similarity(", "levenshtein", "jaro", "risk_score", "trust_score"):
                self.assertNotIn(forbidden, content)


class PublicMartsSqlShapeTests(unittest.TestCase):
    """D-007/D-15/D-17: the aggregate mart carries no organization-level
    drillthrough field, while the two named relations deliberately carry
    the allowed identity fields and never `select *` or an excluded column
    (T-04-05C/T-04-05D/T-04-05E).
    """

    _EXCLUDED_COLUMN_TOKENS = (
        "federal_employer_identification_number",
        "sos_or_ftb_number",
        "source_reported_issue_date",
    )

    def test_aggregate_mart_has_no_organization_level_column(self) -> None:
        content = _COVERAGE_MART_SQL.read_text(encoding="utf-8").lower()
        for forbidden in (
            "state_charity_registration_number",
            "source_reported_organization_name",
            "source_reported_city",
        ):
            self.assertNotIn(forbidden, content)

    def test_aggregate_mart_groups_only_low_cardinality_dimensions(self) -> None:
        content = _COVERAGE_MART_SQL.read_text(encoding="utf-8").lower()
        self.assertIn("group by", content)
        self.assertIn("coverage_class", content)

    def test_named_relations_deliberately_carry_allowed_identity_fields(self) -> None:
        # D-007/T-04-05E: a scanner or test that default-denies organization
        # name or the exact registration number would break the project's
        # own required named lookup. These two allowed fields must be
        # visibly present in both named public relations.
        for path in (_DIM_ORGANIZATIONS_SQL, _FCT_OBSERVATIONS_SQL):
            content = path.read_text(encoding="utf-8")
            self.assertIn("state_charity_registration_number", content)
        dim_content = _DIM_ORGANIZATIONS_SQL.read_text(encoding="utf-8")
        self.assertIn("source_reported_organization_name", dim_content)
        fct_content = _FCT_OBSERVATIONS_SQL.read_text(encoding="utf-8")
        self.assertIn("source_reported_organization_name", fct_content)

    def test_named_relations_never_select_star_or_excluded_column(self) -> None:
        for path in (_DIM_ORGANIZATIONS_SQL, _FCT_OBSERVATIONS_SQL, _COVERAGE_MART_SQL):
            content = path.read_text(encoding="utf-8").lower()
            self.assertNotRegex(content, r"select\s+\*")
            for forbidden in self._EXCLUDED_COLUMN_TOKENS:
                self.assertNotIn(forbidden, content)

    def test_dim_public_organizations_uses_deterministic_latest_row_ranking(self) -> None:
        content = _DIM_ORGANIZATIONS_SQL.read_text(encoding="utf-8").lower()
        self.assertIn("row_number()", content)
        self.assertIn("partition by", content)
        self.assertIn("desc", content)

    def test_fct_public_status_observations_makes_missing_explicit(self) -> None:
        content = _FCT_OBSERVATIONS_SQL.read_text(encoding="utf-8").lower()
        self.assertIn("cross join", content)
        self.assertIn("left join", content)
        self.assertIn("not_observed", content)


class PreflightSqlBindingShapeTests(unittest.TestCase):
    """T-04-05A: every sidecar field crosses into SQL only as a bound
    parameter, never interpolated into SQL text.
    """

    def test_eligibility_insert_uses_parameter_placeholders(self) -> None:
        content = _PREFLIGHT_PY.read_text(encoding="utf-8")
        self.assertIn(
            "INSERT INTO {RUNTIME_SCHEMA}.public_eligibility_classifications VALUES", content
        )
        self.assertIn("(?, ?, ?)", content)


if __name__ == "__main__":
    unittest.main()
