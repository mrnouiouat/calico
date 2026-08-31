"""Actual-dbt integration proof for capture runs and release flags
(04-04-PLAN.md D-02/D-12/D-13/D-14).

Three layers of proof, mirroring Plan 02's `test_transitions.py` precedent
of relying on dbt's own singular tests plus a successful full build rather
than a new `runner.FixtureBuildInspection` facade method (deferred to
Plan 06, per this plan's own Task 2 action text):

1. `PreflightCaptureAttemptsBindingTests` drives
   `calico_dbt.preflight.prepare_runtime_input` directly (no dbt
   subprocess) to prove the fixed nullable `runtime_input.capture_attempts`
   schema is identical whether the store has zero or several attempts, and
   that a malformed attempt document fails preflight closed.
2. `CaptureFixtureBuildTests` builds a custom throwaway store -- through
   the real `calico_landing.admission.admit()` boundary for the genuine
   accepted/no_new_release/rejected outcomes, plus hand-written legacy v1
   and edge-case v2 documents for shapes `admit()` cannot itself produce
   (a historical admission-level/store-level v1 record, an orphaned
   `recovered` outcome, and unordered v2 UTC bounds) -- and proves the
   complete fixture DAG, including this plan's two new singular tests,
   builds successfully over that rich, mixed-shape attempt panel. A
   successful `outcome.status == "success"` here is itself the row-level
   semantic proof: any normalization, timing, or grain defect fails
   `assert_capture_run_normalization.sql` / `assert_release_flag_grain.sql`
   and therefore fails the whole `dbt build`.
3. `FullFixtureBuildRemainsGreenWithDefaultFixtureTests` and the SQL-shape
   classes prove the immutable default (v1) fixture still builds and that
   the required techniques (exhaustive `case`, parameterized binding,
   union branches) are visibly present in the committed SQL.

No real organization identity or excluded value is used anywhere -- only
invented synthetic values and the project's own existing safe fixtures.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import duckdb

from calico_dbt import catalog as cat
from calico_dbt import preflight, runner
from calico_landing.admission import admit
from calico_landing.attempts import write_v2_attempt
from calico_landing.result import AdmissionResult
from calico_landing.store import ensure_store_layout
from tests.fixtures.landing import fixture_builder as landing_fb

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INTERMEDIATE_DIR = _REPO_ROOT / "dbt" / "models" / "intermediate"
_STG_CAPTURE_ATTEMPTS_SQL = _INTERMEDIATE_DIR / "stg_capture_attempts.sql"
_CAPTURE_RUNS_SQL = _INTERMEDIATE_DIR / "int_capture_runs.sql"
_RELEASE_FLAGS_SQL = _INTERMEDIATE_DIR / "int_release_flags.sql"
_PREFLIGHT_PY = _REPO_ROOT / "calico_dbt" / "preflight.py"

_BASELINE_CANDIDATE_ROOT = _REPO_ROOT / "tests" / "fixtures" / "landing" / "valid"

#: The exact total dbt model count now that Plan 03's two models
#: (04-03-PLAN.md: int_entity_observation_sequence, int_delinquency_spells)
#: and Plan 05's four models (04-05-PLAN.md: int_public_organization_
#: eligibility, mart_registry_population_coverage, dim_public_organizations,
#: fct_public_status_observations) also exist alongside this plan's own
#: three models and the fourteen already-delivered Phase 3 + Plan 02
#: models. An unselected `runner.build` always builds the whole project
#: regardless of which plan's test is driving it, so this constant tracks
#: total project model count, not only this file's own plan's models --
#: forward-fixed the same way this file's own constant was forward-fixed
#: from 14 to 17 to 19 to 23 as each later plan landed. A mismatch here
#: means either a new model failed to build or an unexpected extra/missing
#: model exists in the project.
_EXPECTED_TOTAL_MODEL_COUNT = 23

_FABRICATED_RECOVERED_FINGERPRINT = "b" * 64


def _write_raw_attempt_json(attempts_dir: Path, document: dict) -> None:
    """Write one already-shaped attempt document directly, bypassing every
    production writer. Legitimate only as test setup: it lets this suite
    place a historical v1-shaped document (or a deliberately unordered v2
    document) into a store without a production code path that could ever
    itself produce it today.
    """

    path = attempts_dir / f"{document['attempt_id']}.json"
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")


@dataclass(frozen=True)
class _SingleAdmission:
    """Minimal shape `calico_dbt.runner._build_fixture_catalog` needs: a
    `.result` attribute carrying one accepted `AdmissionResult`.
    """

    result: AdmissionResult


@dataclass(frozen=True)
class _CaptureFixtureStore:
    store_root: Path
    admissions: tuple[_SingleAdmission, ...]


@contextmanager
def _capture_fixture_store() -> Iterator[_CaptureFixtureStore]:
    """Build one throwaway store exercising every capture-run/release-flag
    case this plan's SQL must handle: real accepted/no_new_release/rejected
    v2 attempts from the actual `admit()` boundary, an orphaned synthetic
    `recovered` v2 attempt, both legacy v1 shapes, and a v2 attempt with
    deliberately unordered UTC bounds.
    """

    with tempfile.TemporaryDirectory(prefix="calico-capture-fixture-store-") as store_dir:
        store_root = Path(store_dir).resolve()

        accepted = admit(_BASELINE_CANDIDATE_ROOT, store_root)
        if accepted.status != "accepted":
            raise AssertionError("baseline candidate must admit cleanly for this fixture to be valid")

        no_new = admit(_BASELINE_CANDIDATE_ROOT, store_root)
        if no_new.status != "no_new_release":
            raise AssertionError("identical rerun must return no_new_release")

        with landing_fb.truncated_payload() as bad_candidate:
            rejected = admit(bad_candidate.root, store_root)
        if rejected.status != "rejected":
            raise AssertionError("truncated payload must be rejected")

        attempts_dir = store_root / "attempts"

        # Delete the one real "accepted" v2 attempt for the promoted
        # release, leaving only its "no_new_release" sibling behind. This
        # gives `capture_outcome_available_v1` a genuine zero-linked-
        # accepted-or-recovered promoted release to classify as 'review',
        # without needing a second distinct as_of_date.
        accepted_attempt_files = [
            path
            for path in attempts_dir.glob("*.json")
            if json.loads(path.read_text(encoding="utf-8"))["status"] == "accepted"
        ]
        if len(accepted_attempt_files) != 1:
            raise AssertionError("expected exactly one accepted v2 attempt before deletion")
        accepted_attempt_files[0].unlink()

        # An orphaned synthetic "recovered" v2 attempt against a fabricated
        # release identity that matches no real promoted release -- proves
        # normalized_outcome == 'recovered' without disturbing the
        # deliberately zero-linked review case above.
        write_v2_attempt(
            store_root,
            attempt_id="synthetic-recovered-attempt",
            started_at_utc="2020-06-01T00:00:00.000Z",
            ended_at_utc="2020-06-01T00:00:01.000Z",
            status="recovered",
            as_of_date="2020-06-01",
            release_revision=99,
            revision_fingerprint=_FABRICATED_RECOVERED_FINGERPRINT,
            reason_count=None,
        )

        # Legacy admission-level v1 shape -- never rewritten or produced by
        # any current production writer, but must still normalize/reconcile
        # correctly with permanently unavailable timing.
        _write_raw_attempt_json(
            attempts_dir,
            {
                "schema_version": 1,
                "attempt_id": "synthetic-admission-v1-attempt",
                "status": "rejected",
                "as_of_date": "2020-01-15",
                "reason_count": 1,
            },
        )

        # Legacy store-level v1 shapes, one non-recovered and one recovered
        # accepted record plus a no_new_release record, all against the
        # real promoted release identity.
        _write_raw_attempt_json(
            attempts_dir,
            {
                "schema_version": 1,
                "attempt_id": "synthetic-store-v1-accepted",
                "as_of_date": accepted.as_of_date,
                "revision_fingerprint": accepted.revision_fingerprint,
                "status": "accepted",
                "release_revision": accepted.release_revision,
                "recovered": False,
            },
        )
        _write_raw_attempt_json(
            attempts_dir,
            {
                "schema_version": 1,
                "attempt_id": "synthetic-store-v1-recovered",
                "as_of_date": accepted.as_of_date,
                "revision_fingerprint": accepted.revision_fingerprint,
                "status": "accepted",
                "release_revision": accepted.release_revision,
                "recovered": True,
            },
        )
        _write_raw_attempt_json(
            attempts_dir,
            {
                "schema_version": 1,
                "attempt_id": "synthetic-store-v1-no-new-release",
                "as_of_date": accepted.as_of_date,
                "revision_fingerprint": accepted.revision_fingerprint,
                "status": "no_new_release",
                "release_revision": accepted.release_revision,
                "recovered": False,
            },
        )

        # A v2 attempt with both UTC bounds present but deliberately
        # unordered -- duration_seconds must stay null, never negative.
        write_v2_attempt(
            store_root,
            attempt_id="synthetic-unordered-bounds",
            started_at_utc="2020-01-15T12:00:05.000Z",
            ended_at_utc="2020-01-15T12:00:00.000Z",
            status="rejected",
            as_of_date=None,
            release_revision=None,
            revision_fingerprint=None,
            reason_count=1,
        )

        yield _CaptureFixtureStore(
            store_root=store_root,
            admissions=(_SingleAdmission(result=accepted),),
        )


class PreflightCaptureAttemptsBindingTests(unittest.TestCase):
    """D-12/D-20: `prepare_runtime_input` always creates the fixed nullable
    `runtime_input.capture_attempts` schema -- as an empty relation when the
    store has no attempts, one row per attempt when it does, and a fail-
    closed `PreflightError` when a document is malformed. Every check here
    is a direct Python-level assertion against `information_schema`/row
    content, never a raw path or document echo.
    """

    _EXPECTED_COLUMNS = (
        "schema_version",
        "attempt_shape",
        "attempt_id",
        "raw_status",
        "as_of_date",
        "release_revision",
        "revision_fingerprint",
        "reason_count",
        "recovered",
        "started_at_utc",
        "ended_at_utc",
    )

    def _columns(self, duckdb_path: Path) -> list[str]:
        connection = duckdb.connect(str(duckdb_path), read_only=True)
        try:
            rows = connection.execute(
                "select column_name from information_schema.columns "
                "where table_schema = 'runtime_input' and table_name = 'capture_attempts' "
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
            self.assertEqual(binding.verified_capture_attempt_count, 0)
            self.assertEqual(list(self._columns(binding.duckdb_path)), list(self._EXPECTED_COLUMNS))

            connection = duckdb.connect(str(binding.duckdb_path), read_only=True)
            try:
                row_count = connection.execute(
                    "select count(*) from runtime_input.capture_attempts"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(row_count, 0)

    def test_admitted_store_binds_one_row_per_attempt_with_correct_values(self) -> None:
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
            self.assertEqual(binding.verified_capture_attempt_count, 1)
            self.assertEqual(list(self._columns(binding.duckdb_path)), list(self._EXPECTED_COLUMNS))

            connection = duckdb.connect(str(binding.duckdb_path), read_only=True)
            try:
                rows = connection.execute(
                    "select attempt_shape, raw_status, release_revision, revision_fingerprint "
                    "from runtime_input.capture_attempts"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(
                rows,
                [("v2", "accepted", result.release_revision, result.revision_fingerprint)],
            )

    def test_malformed_attempt_file_fails_preflight_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            (layout.store_root / "attempts" / "bad.json").write_text("{not-json", encoding="utf-8")

            temp_root = Path(tmp) / "runtime"
            temp_root.mkdir()

            with self.assertRaises(preflight.PreflightError) as ctx:
                preflight.prepare_runtime_input(
                    store_root=store_root,
                    catalog=cat.InputCatalog(contract_version=1, releases=()),
                    temp_root=temp_root,
                )
            self.assertEqual(ctx.exception.category, "preflight.capture_attempt_invalid")


class CaptureFixtureBuildTests(unittest.TestCase):
    """The complete fixture DAG, including this plan's three new models and
    two new singular tests, succeeds over a store deliberately engineered
    to mix every closed attempt shape and every closed capture/flag
    outcome (D-12/D-13/D-14).
    """

    def test_full_fixture_build_succeeds_with_mixed_attempt_panel(self) -> None:
        outcome = runner.build(mode="fixture", fixture_store_factory=_capture_fixture_store)
        self.assertEqual(outcome.status, "success", outcome.category)
        self.assertIsNotNone(outcome.proof)
        self.assertEqual(outcome.proof.dbt_model_count, _EXPECTED_TOTAL_MODEL_COUNT)


class FullFixtureBuildRemainsGreenWithDefaultFixtureTests(unittest.TestCase):
    """Regression proof: this plan's new models/tests do not break the
    immutable Phase 3 default (v1) fixture's own full build.
    """

    def test_full_fixture_build_succeeds_with_default_v1_fixture(self) -> None:
        outcome = runner.build(mode="fixture")
        self.assertEqual(outcome.status, "success", outcome.category)
        self.assertIsNotNone(outcome.proof)
        self.assertEqual(outcome.proof.dbt_model_count, _EXPECTED_TOTAL_MODEL_COUNT)


class CaptureRunsSqlShapeTests(unittest.TestCase):
    """D-02/D-12/D-13: `int_capture_runs` visibly normalizes every closed
    shape/status pair and never fabricates v1 timing.
    """

    def test_capture_runs_reads_only_the_staging_relation(self) -> None:
        content = _CAPTURE_RUNS_SQL.read_text(encoding="utf-8")
        self.assertIn("ref('stg_capture_attempts')", content)

    def test_capture_runs_covers_every_closed_shape_status_pair(self) -> None:
        content = _CAPTURE_RUNS_SQL.read_text(encoding="utf-8")
        for shape, status in (
            ("admission_v1", "rejected"),
            ("store_v1", "accepted"),
            ("store_v1", "no_new_release"),
            ("v2", "accepted"),
            ("v2", "no_new_release"),
            ("v2", "rejected"),
            ("v2", "recovered"),
        ):
            with self.subTest(shape=shape, status=status):
                self.assertIn(f"attempt_shape = '{shape}'", content)
                self.assertIn(f"raw_status = '{status}'", content)

    def test_capture_runs_never_invents_v1_timing(self) -> None:
        content = _CAPTURE_RUNS_SQL.read_text(encoding="utf-8")
        self.assertIn("timing_unavailable", content)
        self.assertIn("timing_available", content)
        # Duration is gated on the ordered-bounds comparison, never a bare
        # subtraction that could go negative.
        self.assertIn("ended_at_utc >= started_at_utc", content.replace("\n", " ").replace("  ", " "))


class ReleaseFlagsSqlShapeTests(unittest.TestCase):
    """D-14/T-04-04E: `int_release_flags` is a tall union of deterministic
    and heuristic branches, never a score.
    """

    def test_release_flags_unions_three_named_rule_branches(self) -> None:
        content = _RELEASE_FLAGS_SQL.read_text(encoding="utf-8")
        for rule_id in (
            "parser_contract_version_known_v1",
            "capture_outcome_available_v1",
            "keyed_coverage_change_fraction_v1",
        ):
            self.assertIn(rule_id, content)
        self.assertGreaterEqual(content.lower().count("union all"), 2)

    def test_release_flags_separates_deterministic_from_heuristic(self) -> None:
        content = _RELEASE_FLAGS_SQL.read_text(encoding="utf-8")
        self.assertIn("'deterministic'", content)
        self.assertIn("'heuristic_review'", content)

    def test_release_flags_never_reopens_int_keyed_snapshots(self) -> None:
        # This plan's own `depends_on` boundary is 04-01 only; the
        # heuristic coverage rule must derive its keyed count from the
        # Phase 3 disposition relation directly, never from Plan 02's
        # parallel `int_keyed_snapshots` view.
        content = _RELEASE_FLAGS_SQL.read_text(encoding="utf-8")
        self.assertNotIn("ref('int_keyed_snapshots')", content)
        self.assertIn("ref('int_registry_record_dispositions')", content)


class PreflightSqlBindingShapeTests(unittest.TestCase):
    """T-04-04B: every attempt field crosses into SQL only as a bound
    parameter, never interpolated into SQL text.
    """

    def test_capture_attempts_insert_uses_parameter_placeholders(self) -> None:
        content = _PREFLIGHT_PY.read_text(encoding="utf-8")
        self.assertIn("INSERT INTO {RUNTIME_SCHEMA}.capture_attempts VALUES", content)
        self.assertIn("(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", content)

    def test_staging_model_never_reopens_a_json_document(self) -> None:
        content = _STG_CAPTURE_ATTEMPTS_SQL.read_text(encoding="utf-8").lower()
        for forbidden in ("read_json", "read_csv", ".json'", "glob("):
            self.assertNotIn(forbidden, content)


if __name__ == "__main__":
    unittest.main()
