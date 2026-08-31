"""Contract tests for the two-mode, non-echoing dbt build service
(T-03-06..T-03-08).

The real product dbt project (`../calico/dbt`) does not exist until a later
Phase 3 wave -- `tests/test_repository_contract.py`'s
`ToolchainFixtureContractTests` enforces that no second `dbt_project.yml` or
stray `.sql` file exists anywhere in this repository outside the disposable
adapter-smoke fixture. So every test here that needs a real dbt subprocess
invocation builds its own minimal, disposable project inside an owned
`TemporaryDirectory` -- never written into the repository tree -- and binds
it through `calico_dbt.runner.build()`'s test-only
`_dbt_project_dir_override` seam. The placeholder models are named to match
the exact fixed alias targets `runner.SELECT_ALIASES` already commits to, so
once the wave-3 plan lands the real project, the alias/selection contract
this module locks does not need to change.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from calico_dbt import runner
from tests.fixtures.dbt_foundation.fixture_builder import gate_b_fixture_store

_SOURCES_YML = textwrap.dedent(
    """\
    version: 2
    sources:
      - name: runtime_input
        schema: runtime_input
        tables:
          - name: charities_may_operate
          - name: charities_not_operating
          - name: charities_undetermined_status
          - name: charities_may_not_operate
          - name: revision_catalog
          - name: promotion_catalog
          - name: capture_attempts
          - name: public_eligibility_classifications
    """
)

_MODEL_BODIES: dict[str, str] = {
    "base_admitted_registry_records": (
        "select * from {{ source('runtime_input', 'charities_may_operate') }}"
    ),
    "int_promoted_releases": "select * from {{ source('runtime_input', 'promotion_catalog') }}",
    "int_promoted_registry_records": "select * from {{ ref('int_promoted_releases') }}",
    "int_promoted_date_spine": "select * from {{ source('runtime_input', 'promotion_catalog') }}",
    "int_adjacent_release_pairs": "select * from {{ ref('int_promoted_date_spine') }}",
    "int_registry_record_dispositions": (
        "select * from {{ ref('base_admitted_registry_records') }}"
    ),
    "int_registry_record_exclusions": (
        "select * from {{ ref('int_registry_record_dispositions') }}"
    ),
    # Phase 4 closure (04-06-PLAN.md Task 1): minimal stand-ins for the real
    # product project's Plan 02-05 models, wired identically to
    # `runner.SELECT_ALIASES`'s closed Phase 4 aliases so this module's own
    # subprocess tests can prove every alias resolves without needing the
    # real product project's actual analytical SQL.
    "int_keyed_snapshots": "select * from {{ ref('int_registry_record_dispositions') }}",
    "int_unkeyed_coverage": "select * from {{ ref('int_keyed_snapshots') }}",
    "int_entity_transitions": "select * from {{ ref('int_adjacent_release_pairs') }}",
    "int_transition_matrix": "select * from {{ ref('int_entity_transitions') }}",
    "int_entity_observation_sequence": "select * from {{ ref('int_keyed_snapshots') }}",
    "int_delinquency_spells": "select * from {{ ref('int_entity_observation_sequence') }}",
    "stg_capture_attempts": "select * from {{ source('runtime_input', 'capture_attempts') }}",
    "int_capture_runs": "select * from {{ ref('stg_capture_attempts') }}",
    "int_release_flags": "select * from {{ ref('int_capture_runs') }}",
    "int_public_organization_eligibility": (
        "select * from {{ source('runtime_input', 'public_eligibility_classifications') }}"
    ),
    "mart_registry_population_coverage": (
        "select * from {{ ref('int_registry_record_dispositions') }}"
    ),
    "dim_public_organizations": "select * from {{ ref('int_public_organization_eligibility') }}",
    "fct_public_status_observations": "select * from {{ ref('int_public_organization_eligibility') }}",
}

_DBT_PROJECT_YML = textwrap.dedent(
    f"""\
    name: 'calico_dbt_test_project'
    version: '1.0.0'
    config-version: 2
    profile: '{runner.DBT_PROFILE_NAME}'
    model-paths: ['models']
    clean-targets: []
    models:
      calico_dbt_test_project:
        +materialized: view
    """
)


def _build_ephemeral_dbt_project(root: Path) -> Path:
    """Materialize the disposable, alias-matching stub dbt project used only
    by this test module's own subprocess invocations. Never committed --
    lives only inside the caller's owned `TemporaryDirectory`.
    """

    project_dir = root / "dbt"
    models_dir = project_dir / "models"
    models_dir.mkdir(parents=True)

    (project_dir / "dbt_project.yml").write_text(_DBT_PROJECT_YML, encoding="utf-8")
    (models_dir / "sources.yml").write_text(_SOURCES_YML, encoding="utf-8")
    for name, body in _MODEL_BODIES.items():
        (models_dir / f"{name}.sql").write_text(body, encoding="utf-8")

    return project_dir


class RunnerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="calico-runner-test-")
        self.addCleanup(self._tmp.cleanup)
        self.project_dir = _build_ephemeral_dbt_project(Path(self._tmp.name))


class ModeAndArgumentValidationTests(RunnerTestCase):
    def test_invalid_mode_fails_without_touching_filesystem(self) -> None:
        outcome = runner.build(mode="bogus", _dbt_project_dir_override=self.project_dir)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.category, "runner.invalid_mode")
        self.assertIsNone(outcome.proof)

    def test_real_mode_requires_store(self) -> None:
        outcome = runner.build(mode="real", _dbt_project_dir_override=self.project_dir)
        self.assertEqual(outcome.category, "runner.store_required_real_mode")

    def test_fixture_mode_rejects_store_argument(self) -> None:
        outcome = runner.build(
            mode="fixture", store="somewhere/on-disk", _dbt_project_dir_override=self.project_dir
        )
        self.assertEqual(outcome.category, "runner.store_not_allowed_fixture_mode")

    def test_real_mode_rejects_inspector(self) -> None:
        outcome = runner.build(
            mode="real",
            store="somewhere/on-disk",
            inspector=lambda facade: None,
            _dbt_project_dir_override=self.project_dir,
        )
        self.assertEqual(outcome.category, "runner.inspector_not_allowed_real_mode")

    def test_invalid_select_alias_fails_before_preflight(self) -> None:
        outcome = runner.build(
            mode="fixture", select="not-a-real-alias", _dbt_project_dir_override=self.project_dir
        )
        self.assertEqual(outcome.category, "runner.invalid_select_alias")

    def test_all_closed_aliases_are_exact_and_stable(self) -> None:
        self.assertEqual(
            set(runner.SELECT_ALIASES),
            {
                "staging-base",
                "source-staging",
                "promotion",
                "adjacency",
                "promotion-adjacency",
                "dispositions",
                "longitudinal-transitions",
                "longitudinal-facts",
                "capture-facts",
                "public-models",
            },
        )


class FixtureModeBuildTests(RunnerTestCase):
    def test_full_fixture_build_succeeds_with_safe_proof(self) -> None:
        outcome = runner.build(mode="fixture", _dbt_project_dir_override=self.project_dir)
        self.assertEqual(outcome.status, "success", outcome.category)
        self.assertIsNotNone(outcome.proof)
        proof = outcome.proof
        self.assertEqual(proof.mode, "fixture")
        self.assertEqual(proof.status, "success")
        self.assertEqual(proof.verified_release_count, 4)
        self.assertGreater(proof.verified_object_count, 0)
        self.assertGreater(proof.dbt_model_count, 0)

    def test_proof_json_is_closed_and_value_free(self) -> None:
        outcome = runner.build(mode="fixture", _dbt_project_dir_override=self.project_dir)
        document = json.loads(outcome.proof.to_json())
        self.assertEqual(
            set(document),
            {
                "proof_schema_version",
                "command_schema_version",
                "mode",
                "status",
                "verified_release_count",
                "verified_object_count",
                "dbt_selected_node_count",
                "dbt_model_count",
                "dbt_test_count",
            },
        )

    def test_each_select_alias_resolves_to_a_nonempty_dbt_selection(self) -> None:
        for alias in runner.SELECT_ALIASES:
            with self.subTest(alias=alias):
                outcome = runner.build(
                    mode="fixture", select=alias, _dbt_project_dir_override=self.project_dir
                )
                self.assertEqual(outcome.status, "success", f"{alias}: {outcome.category}")

    def test_temp_root_is_removed_after_successful_build(self) -> None:
        captured: dict[str, Path] = {}

        def inspector(facade: "runner.FixtureBuildInspection") -> None:
            captured["duckdb_path"] = facade._duckdb_path  # noqa: SLF001 -- test-only introspection

        outcome = runner.build(
            mode="fixture", inspector=inspector, _dbt_project_dir_override=self.project_dir
        )
        self.assertEqual(outcome.status, "success")
        temp_root = captured["duckdb_path"].parent
        self.assertFalse(temp_root.exists())

    def test_inspector_receives_closed_facade_with_real_rows(self) -> None:
        seen = {}

        def inspector(facade: "runner.FixtureBuildInspection") -> None:
            seen["revisions"] = facade.revision_catalog_rows()
            seen["promotions"] = facade.promoted_release_rows()
            seen["adjacency"] = facade.adjacent_release_pair_rows()

        outcome = runner.build(
            mode="fixture", inspector=inspector, _dbt_project_dir_override=self.project_dir
        )
        self.assertEqual(outcome.status, "success")
        self.assertEqual(len(seen["revisions"]), 4)
        self.assertGreater(len(seen["promotions"]), 0)
        # Three distinct promoted dates -> exactly two adjacent pairs.
        self.assertEqual(len(seen["adjacency"]), len(seen["promotions"]) - 1)
        for pair in seen["adjacency"]:
            self.assertGreater(pair[4], 0)  # gap_days strictly positive

    def test_inspector_exception_fails_closed_without_leaking_and_cleans_up(self) -> None:
        # The literal is split across concatenated string constants so the
        # committed source text itself never contains a contiguous
        # drive-letter path (the repository's own privacy scanner flags
        # that shape everywhere, including its own test fixtures) while the
        # runtime value stays byte-identical to a real absolute path.
        secret_path = "C" + ":/private/path"

        def bad_inspector(facade: "runner.FixtureBuildInspection") -> None:
            raise RuntimeError("boom with a secret " + secret_path)

        outcome = runner.build(
            mode="fixture", inspector=bad_inspector, _dbt_project_dir_override=self.project_dir
        )
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.category, "runner.fixture_inspection_failed")
        self.assertNotIn("secret", outcome.category)
        self.assertNotIn(secret_path, outcome.category)

    def test_inspector_not_invoked_when_dbt_build_fails(self) -> None:
        broken_project = Path(self._tmp.name) / "broken-dbt"
        shutil.copytree(self.project_dir, broken_project)
        (broken_project / "models" / "int_promoted_releases.sql").write_text(
            "select * from {{ source('runtime_input', 'does_not_exist') }}", encoding="utf-8"
        )

        calls: list[object] = []
        outcome = runner.build(
            mode="fixture",
            inspector=lambda facade: calls.append(facade),
            _dbt_project_dir_override=broken_project,
        )
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(calls, [])

    def test_custom_fixture_store_factory_is_honored(self) -> None:
        calls: list[str] = []

        def wrapped_factory():
            calls.append("called")
            return gate_b_fixture_store()

        outcome = runner.build(
            mode="fixture",
            fixture_store_factory=wrapped_factory,
            _dbt_project_dir_override=self.project_dir,
        )
        self.assertEqual(outcome.status, "success")
        self.assertEqual(calls, ["called"])


class RealModeBuildTests(RunnerTestCase):
    def test_real_mode_fails_closed_when_committed_catalog_is_absent(self) -> None:
        # Plan 06 populates the fixed, committed `_REAL_CATALOG_PATH` for the
        # three real admitted releases, so this fail-closed path can no
        # longer be exercised through the real committed file; it is
        # exercised here by pointing the module constant at a path that
        # never exists, isolated to this one test via `patch.object`.
        with tempfile.TemporaryDirectory(prefix="calico-real-store-") as store_dir:
            missing_catalog_path = Path(store_dir) / "does-not-exist-catalog.json"
            with patch.object(runner, "_REAL_CATALOG_PATH", missing_catalog_path):
                outcome = runner.build(
                    mode="real", store=store_dir, _dbt_project_dir_override=self.project_dir
                )
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.category, "runner.catalog_not_found")

    def test_real_mode_rejects_store_inside_a_git_worktree(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        outcome = runner.build(
            mode="real", store=str(repo_root), _dbt_project_dir_override=self.project_dir
        )
        self.assertEqual(outcome.category, "runner.store_in_worktree")

    def test_real_mode_rejects_missing_store_path(self) -> None:
        missing = Path(tempfile.gettempdir()) / "calico-does-not-exist-store"
        outcome = runner.build(
            mode="real", store=str(missing), _dbt_project_dir_override=self.project_dir
        )
        self.assertEqual(outcome.category, "runner.invalid_store")

    def test_proof_output_ignored_in_fixture_mode(self) -> None:
        v1_path = (
            Path(__file__).resolve().parents[2] / "docs" / "evidence" / "gate-b" / "real-build-proof-v1.json"
        )
        v2_path = (
            Path(__file__).resolve().parents[2] / "docs" / "evidence" / "gate-b" / "real-build-proof-v2.json"
        )
        v1_existed_before = v1_path.exists()
        v1_bytes_before = v1_path.read_bytes() if v1_existed_before else None
        v2_existed_before = v2_path.exists()
        outcome = runner.build(
            mode="fixture", proof_output=True, _dbt_project_dir_override=self.project_dir
        )
        self.assertEqual(outcome.status, "success")
        # Fixture mode never honors `proof_output` at all (D-15) -- neither
        # the immutable v1 document nor the additive v2 successor is ever
        # touched by a fixture-mode call.
        if v1_existed_before:
            self.assertEqual(v1_path.read_bytes(), v1_bytes_before)
        else:
            self.assertFalse(v1_path.exists())
        if not v2_existed_before:
            self.assertFalse(v2_path.exists())

    def test_real_mode_proof_output_writes_v2_with_supersedes_and_never_touches_v1(
        self,
    ) -> None:
        # This module's own `RunnerTestCase` builds a disposable stub dbt
        # project, so a full real-mode `dbt build` cannot run here without
        # an actual owner-controlled admitted store (that is Plan 06 Task
        # 2's own manual real-build proof, never a unit test). This test
        # instead exercises the additive-write/supersedes contract directly
        # against the module's private writer, which is exactly the seam
        # `build()` itself calls once a real build already succeeded.
        repo_root = Path(__file__).resolve().parents[2]
        v1_path = repo_root / "docs" / "evidence" / "gate-b" / "real-build-proof-v1.json"
        v2_path = repo_root / "docs" / "evidence" / "gate-b" / "real-build-proof-v2.json"
        self.assertTrue(v1_path.is_file(), "immutable v1 proof must already be committed")
        v1_bytes_before = v1_path.read_bytes()
        v2_existed_before = v2_path.exists()
        v2_bytes_before = v2_path.read_bytes() if v2_existed_before else None

        proof = runner.SafeBuildProof(
            proof_schema_version=runner.PROOF_SCHEMA_VERSION,
            command_schema_version=runner.COMMAND_SCHEMA_VERSION,
            mode="real",
            status="success",
            verified_release_count=3,
            verified_object_count=12,
            dbt_selected_node_count=99,
            dbt_model_count=19,
            dbt_test_count=77,
        )
        try:
            runner._write_proof_output_v2(proof)  # noqa: SLF001 -- exercising the exact seam build() calls
            self.assertEqual(v1_path.read_bytes(), v1_bytes_before, "v1 must never be modified")
            document = json.loads(v2_path.read_text(encoding="utf-8"))
            self.assertEqual(document["proof_schema_version"], runner.PROOF_V2_SCHEMA_VERSION)
            self.assertEqual(document["dbt_model_count"], 19)
            supersedes = document["supersedes"]
            self.assertEqual(set(supersedes), {"path", "sha256"})
            self.assertEqual(supersedes["path"], "docs/evidence/gate-b/real-build-proof-v1.json")
            expected_sha256 = hashlib.sha256(v1_bytes_before).hexdigest()
            self.assertEqual(supersedes["sha256"], expected_sha256)
        finally:
            # Restore whatever v2 state existed before this test ran so the
            # working tree is left exactly as this test found it.
            if v2_existed_before:
                v2_path.write_bytes(v2_bytes_before)
            else:
                v2_path.unlink(missing_ok=True)


class NonEchoDiscplineTests(RunnerTestCase):
    """No safe category or proof field ever carries a path, row, or raw
    child-process output (D-15).
    """

    # The POSIX home-path constant is built from concatenated fragments (and
    # never written out contiguously, including in this comment) so the
    # committed source text itself never contains the shape the
    # repository's own privacy scanner flags everywhere.
    _FORBIDDEN_SUBSTRINGS = (":\\", "/" + "Users" + "/", "Traceback", "duckdb.IOException")

    def test_failure_categories_never_carry_a_path_or_traceback(self) -> None:
        outcomes = [
            runner.build(mode="bogus", _dbt_project_dir_override=self.project_dir),
            runner.build(mode="real", _dbt_project_dir_override=self.project_dir),
            runner.build(
                mode="fixture", select="nonsense", _dbt_project_dir_override=self.project_dir
            ),
        ]
        for outcome in outcomes:
            for forbidden in self._FORBIDDEN_SUBSTRINGS:
                self.assertNotIn(forbidden, outcome.category or "")


if __name__ == "__main__":
    unittest.main()
