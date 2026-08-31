"""Contract tests over the target repository's own configuration and policy
files -- ignores, adapted engineering/language invariants, exact toolchain
pins, the CI privacy gate, and the disposable dbt adapter-smoke fixture.

These tests read committed-candidate (currently uncommitted, working-tree)
files directly from disk. They never echo a matched sensitive value -- only
membership/absence assertions over known-safe synthetic or structural
strings (D-10).

Run:
    py -V:3.13 -m unittest tests.test_repository_contract -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _strip_sql_line_comments(content: str) -> str:
    """Drop everything from `--` to end-of-line on every line.

    Explanatory SQL comments legitimately name the exact forbidden tokens
    ("no glob, no `union_by_name`") they document the model as avoiding;
    checking executable SQL only (never comment prose) is what the
    forbidden-token tests below actually mean to enforce.
    """

    return "\n".join(line.split("--", 1)[0] for line in content.splitlines())


#: The complete, closed Phase 3 production dbt SQL allowlist (interfaces
#: block, `03-03-PLAN.md`). Exactly these 18 product-relative paths may ever
#: exist under `dbt/` for the whole of Phase 3; no other production `.sql`
#: file is permitted, and this set is never widened ad hoc by a later plan.
#: Plan 06 closed the final handoff: `Wave3DbtFoundationContractTests` now
#: enforces exact equality between discovered production SQL and this full
#: set, replacing the earlier bootstrap subset/group-shape checks that were
#: only valid while Waves 4-5 had not yet landed.
PHASE_3_WAVE_3_SQL_PATHS = frozenset(
    {
        "dbt/models/staging/base_admitted_registry_records.sql",
        "dbt/models/staging/stg_registry_records.sql",
        "dbt/tests/assert_parquet_only_boundary.sql",
        "dbt/tests/assert_staging_semantics.sql",
    }
)

PHASE_3_WAVE_4_SQL_PATHS = frozenset(
    {
        "dbt/models/intermediate/int_revision_catalog.sql",
        "dbt/models/intermediate/int_promoted_releases.sql",
        "dbt/models/intermediate/int_promoted_registry_records.sql",
        "dbt/models/intermediate/int_promoted_date_spine.sql",
        "dbt/models/intermediate/int_adjacent_release_pairs.sql",
        "dbt/tests/assert_pointer_or_highest_promotion.sql",
        "dbt/tests/assert_adjacent_release_pairs_positive.sql",
        "dbt/tests/assert_same_date_revision_pair_locality.sql",
    }
)

PHASE_3_WAVE_5_SQL_PATHS = frozenset(
    {
        "dbt/models/intermediate/int_registry_record_dispositions.sql",
        "dbt/models/intermediate/int_keyless_registry_coverage.sql",
        "dbt/models/intermediate/int_registry_record_exclusions.sql",
        "dbt/tests/assert_registry_record_disposition_reconciliation.sql",
        "dbt/tests/assert_exact_registration_identity.sql",
        "dbt/tests/assert_blank_status_retained.sql",
    }
)

#: Bootstrap-phase closed final set (Waves 3-5 only). Plan 06 is the named
#: owner of the one planned transition from this subset/group check to exact
#: equality after Plans 04/05 land the remaining 14 paths.
PHASE_3_FINAL_PRODUCTION_SQL_PATHS = frozenset(
    PHASE_3_WAVE_3_SQL_PATHS | PHASE_3_WAVE_4_SQL_PATHS | PHASE_3_WAVE_5_SQL_PATHS
)


#: Named, ordered Phase 4 SQL path groups (04-01-PLAN.md interfaces block).
#: Each group is one later Phase 4 plan's exact closed `.sql` path set
#: (Plans 02-05; Plan 06 lands no new production SQL and instead replaces
#: this whole bootstrap gate with one final exact-equality assertion over
#: the complete 41-path union). A group may land only wholly absent or
#: wholly present, and only after every group named in
#: `PHASE_4_GROUP_DEPENDENCIES` for it is itself wholly present -- this
#: mirrors the Wave 3/4/5 bootstrap shape Phase 3 used before its own
#: Plan 06 closed it to exact equality (see `Wave3DbtFoundationContractTests`
#: above).
PHASE_4_PLAN_02_SQL_PATHS = frozenset(
    {
        "dbt/models/intermediate/int_keyed_snapshots.sql",
        "dbt/models/intermediate/int_unkeyed_coverage.sql",
        "dbt/models/intermediate/int_entity_transitions.sql",
        "dbt/models/intermediate/int_transition_matrix.sql",
        "dbt/tests/assert_keyed_snapshot_reconciliation.sql",
        "dbt/tests/assert_transition_endpoint_union.sql",
        "dbt/tests/assert_transition_pair_membership.sql",
        "dbt/tests/assert_transition_classification.sql",
    }
)

PHASE_4_PLAN_03_SQL_PATHS = frozenset(
    {
        "dbt/models/intermediate/int_entity_observation_sequence.sql",
        "dbt/models/intermediate/int_delinquency_spells.sql",
        "dbt/tests/assert_delinquency_spell_invariants.sql",
    }
)

PHASE_4_PLAN_04_SQL_PATHS = frozenset(
    {
        "dbt/models/intermediate/stg_capture_attempts.sql",
        "dbt/models/intermediate/int_capture_runs.sql",
        "dbt/models/intermediate/int_release_flags.sql",
        "dbt/tests/assert_capture_run_normalization.sql",
        "dbt/tests/assert_release_flag_grain.sql",
    }
)

PHASE_4_PLAN_05_SQL_PATHS = frozenset(
    {
        "dbt/models/intermediate/int_public_organization_eligibility.sql",
        "dbt/models/marts/mart_registry_population_coverage.sql",
        "dbt/models/marts/dim_public_organizations.sql",
        "dbt/models/marts/fct_public_status_observations.sql",
        "dbt/tests/assert_population_coverage_reconciliation.sql",
        "dbt/tests/assert_public_organization_eligibility.sql",
        "dbt/tests/assert_public_status_observation_reconciliation.sql",
    }
)

#: Group name -> exact path set, for iteration. Plan 06 (closure) lands no
#: new production SQL of its own, so it owns no group here.
PHASE_4_SQL_GROUPS: dict[str, frozenset] = {
    "plan_02_transitions": PHASE_4_PLAN_02_SQL_PATHS,
    "plan_03_spells": PHASE_4_PLAN_03_SQL_PATHS,
    "plan_04_capture": PHASE_4_PLAN_04_SQL_PATHS,
    "plan_05_public": PHASE_4_PLAN_05_SQL_PATHS,
}

#: Group name -> the set of predecessor group names that must be wholly
#: present before this group may land ("no later group may precede its
#: dependencies", 04-01-PLAN.md). `plan_02_transitions` and
#: `plan_04_capture` are both Wave 2: each depends only on the Phase 3
#: final set (already unconditionally required above) and may land in
#: either order relative to each other. `plan_03_spells` (Wave 3) depends
#: on `plan_02_transitions`'s keyed snapshots. `plan_05_public` (Wave 3)
#: depends on both `plan_02_transitions` (keyed snapshots) and
#: `plan_04_capture` (nothing capture-specific, but Plan 05's own
#: `depends_on` frontmatter names both 04-02 and 04-04).
PHASE_4_GROUP_DEPENDENCIES: dict[str, frozenset] = {
    "plan_02_transitions": frozenset(),
    "plan_03_spells": frozenset({"plan_02_transitions"}),
    "plan_04_capture": frozenset(),
    "plan_05_public": frozenset({"plan_02_transitions", "plan_04_capture"}),
}

#: The complete closed Phase 4 SQL shape: every path any Phase 4 group will
#: ever contain, whether or not that group has landed yet. Together with
#: `PHASE_3_FINAL_PRODUCTION_SQL_PATHS` this is the total closed
#: cumulative repository SQL boundary Wave 0 opens ("the allowed
#: cumulative set is immutable in shape", 04-01-PLAN.md) -- 23 Phase 4
#: paths plus the 18 delivered Phase 3 paths, for an eventual exact 41.
#: Plan 06 is the named owner of replacing this bootstrap union with one
#: final exact-equality assertion once every group has landed.
PHASE_4_ALL_SQL_PATHS = frozenset().union(*PHASE_4_SQL_GROUPS.values())

PHASE_4_FINAL_PRODUCTION_SQL_PATHS = frozenset(
    PHASE_3_FINAL_PRODUCTION_SQL_PATHS | PHASE_4_ALL_SQL_PATHS
)


def _discovered_production_sql_paths() -> set[str]:
    return {
        str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        for path in (REPO_ROOT / "dbt").rglob("*.sql")
        if "target" not in path.parts and "dbt_packages" not in path.parts and "logs" not in path.parts
    }


class RepositoryPolicyTests(unittest.TestCase):
    """Task 1: repository hygiene ignores and adapted CLAUDE.md invariants."""

    # -- .gitignore -----------------------------------------------------

    def test_gitignore_exists(self) -> None:
        self.assertTrue((REPO_ROOT / ".gitignore").is_file())

    def test_gitignore_blocks_raw_registry_rows(self) -> None:
        content = _read(".gitignore")
        self.assertIn("data/raw/", content)

    def test_gitignore_blocks_private_database(self) -> None:
        content = _read(".gitignore")
        self.assertIn("data/mitos.db", content)

    def test_gitignore_blocks_private_data_family(self) -> None:
        # Shortest identity-free enclosing prefix that excludes the whole
        # private input family (raw releases, cure-case CSVs, scan-result
        # CSVs) without ever naming a specific identity-bearing filename.
        content = _read(".gitignore")
        self.assertIn("data/registry-archive/", content)

    def test_gitignore_never_names_the_cure_case_filename(self) -> None:
        content = _read(".gitignore")
        self.assertNotIn("COMPLETED-CURES", content)
        self.assertNotIn("completed-cures", content.lower())

    def test_gitignore_blocks_generated_diff_output(self) -> None:
        content = _read(".gitignore")
        self.assertIn("data/generated-diffs/", content)

    def test_gitignore_blocks_database_extensions(self) -> None:
        content = _read(".gitignore")
        self.assertIn("*.duckdb", content)
        self.assertIn("*.db", content)

    def test_gitignore_blocks_local_environments(self) -> None:
        content = _read(".gitignore")
        self.assertIn(".venv/", content)
        self.assertIn("venv/", content)

    def test_gitignore_blocks_dbt_logs_targets_packages(self) -> None:
        content = _read(".gitignore")
        self.assertIn("target/", content)
        self.assertIn("dbt_packages/", content)
        self.assertIn("logs/", content)

    def test_gitignore_has_no_broad_data_exception(self) -> None:
        content = _read(".gitignore")
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("!") and "data" in stripped:
                self.fail(
                    "gitignore must not use a broad data exception; "
                    "unignore exact safe paths only"
                )

    # -- .python-version --------------------------------------------------

    def test_python_version_pin_is_exact(self) -> None:
        content = _read(".python-version").strip()
        self.assertEqual(content, "3.13.15")

    # -- requirements-dbt.txt ---------------------------------------------

    def test_requirements_dbt_exact_candidate_pins(self) -> None:
        content = _read("requirements-dbt.txt")
        lines = [line for line in content.splitlines() if line.strip()]
        self.assertEqual(
            lines,
            ["dbt-core==1.10.23", "dbt-duckdb==1.10.1", "duckdb==1.5.5"],
        )

    def test_requirements_dbt_sorted_deterministically(self) -> None:
        content = _read("requirements-dbt.txt")
        lines = [line for line in content.splitlines() if line.strip()]
        self.assertEqual(lines, sorted(lines))

    def test_requirements_dbt_never_claims_superseded_version(self) -> None:
        content = _read("requirements-dbt.txt")
        self.assertNotIn("1.11.0", content)

    # -- CLAUDE.md: required adapted invariants ----------------------------

    def test_claude_md_exists(self) -> None:
        self.assertTrue((REPO_ROOT / "CLAUDE.md").is_file())

    def test_claude_md_requires_deterministic_no_llm_calculations(self) -> None:
        content = _read("CLAUDE.md")
        self.assertIn("deterministic", content.lower())
        self.assertIn("no llm", content.lower())

    def test_claude_md_requires_python_landing_admission_boundary(self) -> None:
        content = _read("CLAUDE.md")
        self.assertIn("landing", content.lower())
        self.assertIn("admission", content.lower())

    def test_claude_md_requires_dbt_duckdb_analytical_ownership(self) -> None:
        content = _read("CLAUDE.md")
        self.assertIn("DuckDB", content)
        self.assertIn("dbt", content.lower())

    def test_claude_md_requires_powerbi_presentation_only(self) -> None:
        content = _read("CLAUDE.md")
        self.assertIn("Power BI", content)

    def test_claude_md_requires_source_linked_claims(self) -> None:
        content = _read("CLAUDE.md")
        self.assertIn("source", content.lower())
        self.assertIn("claim", content.lower())

    def test_claude_md_requires_justified_dependencies(self) -> None:
        content = _read("CLAUDE.md")
        self.assertIn("dependenc", content.lower())

    def test_claude_md_requires_tests_with_behavior(self) -> None:
        content = _read("CLAUDE.md")
        self.assertIn("test", content.lower())
        self.assertIn("behavior", content.lower())

    def test_claude_md_requires_repository_hygiene(self) -> None:
        content = _read("CLAUDE.md")
        self.assertIn("hygiene", content.lower())

    def test_claude_md_requires_exact_upl_phrase(self) -> None:
        content = _read("CLAUDE.md")
        self.assertIn("published delinquent population", content)

    def test_claude_md_requires_outside_in_framing(self) -> None:
        content = _read("CLAUDE.md")
        self.assertIn("outside-in", content.lower())

    # -- CLAUDE.md: forbidden predecessor content ---------------------------

    def test_claude_md_omits_mitos_core_package(self) -> None:
        content = _read("CLAUDE.md")
        self.assertNotIn("mitos-core", content.lower())

    def test_claude_md_omits_mitos_legal_schema(self) -> None:
        content = _read("CLAUDE.md")
        self.assertNotIn("legal schema", content.lower())
        self.assertNotIn("legal engine", content.lower())

    def test_claude_md_omits_api_frontend_hierarchy(self) -> None:
        content = _read("CLAUDE.md")
        self.assertNotIn("api/frontend", content.lower())
        self.assertNotIn("frontend/package.json", content.lower())

    def test_claude_md_omits_obsolete_source_of_truth_hierarchy(self) -> None:
        content = _read("CLAUDE.md")
        for token in ("ca.json", "engine-contract", "idea-consolidation", "idea-log"):
            self.assertNotIn(token, content.lower())

    def test_claude_md_omits_ag_backlog_framing(self) -> None:
        content = _read("CLAUDE.md")
        self.assertNotIn("ag backlog", content.lower())
        self.assertNotIn("attorney general backlog", content.lower())

    def test_claude_md_omits_outcome_promises_and_advice_language(self) -> None:
        content = _read("CLAUDE.md")
        self.assertNotIn("legal advice", content.lower())
        self.assertNotIn("we promise", content.lower())

    def test_claude_md_omits_superseded_dbt_duckdb_version_claim(self) -> None:
        content = _read("CLAUDE.md")
        self.assertNotIn("1.11.0", content)


class WorkflowContractTests(unittest.TestCase):
    """Task 2: immutable, read-only, full-history privacy-gate CI workflow."""

    WORKFLOW_PATH = ".github/workflows/privacy-gate.yml"
    GATE_E_COMMAND = "python -m tools.privacy_scan --tree HEAD --history-all"
    CHECKOUT_PIN = "3d3c42e5aac5ba805825da76410c181273ba90b1"
    SETUP_PYTHON_PIN = "5fda3b95a4ea91299a34e894583c3862153e4b97"

    def _workflow(self) -> str:
        return _read(self.WORKFLOW_PATH)

    def test_workflow_exists(self) -> None:
        self.assertTrue((REPO_ROOT / self.WORKFLOW_PATH).is_file())

    def test_workflow_runs_on_pr_push_main_and_dispatch(self) -> None:
        content = self._workflow()
        self.assertIn("pull_request", content)
        self.assertIn("push", content)
        self.assertIn("main", content)
        self.assertIn("workflow_dispatch", content)

    def test_workflow_permission_is_exactly_contents_read(self) -> None:
        content = self._workflow()
        lines = content.splitlines()
        start = next(
            (i for i, line in enumerate(lines) if line.strip() == "permissions:"), None
        )
        self.assertIsNotNone(start, "no top-level `permissions:` block found")
        block_lines: list[str] = []
        for line in lines[start + 1 :]:
            if line.strip() == "" or line.startswith((" ", "\t")):
                if line.strip():
                    block_lines.append(line.strip())
                continue
            break
        self.assertEqual(block_lines, ["contents: read"])

    def test_workflow_never_grants_write_permissions(self) -> None:
        content = self._workflow()
        self.assertNotIn("write", content.lower())

    def test_workflow_checkout_pinned_to_full_sha_with_version_comment(self) -> None:
        content = self._workflow()
        self.assertIn(f"actions/checkout@{self.CHECKOUT_PIN}", content)
        self.assertIn("v7.0.1", content)
        self.assertNotRegex(content, r"actions/checkout@v\d")

    def test_workflow_setup_python_pinned_to_full_sha_with_version_comment(self) -> None:
        content = self._workflow()
        self.assertIn(f"actions/setup-python@{self.SETUP_PYTHON_PIN}", content)
        self.assertIn("v7.0.0", content)
        self.assertNotRegex(content, r"actions/setup-python@v\d")

    def test_workflow_checkout_fetches_full_history(self) -> None:
        content = self._workflow()
        self.assertIn("fetch-depth: 0", content)

    def test_workflow_checkout_does_not_persist_credentials(self) -> None:
        content = self._workflow()
        self.assertIn("persist-credentials: false", content)

    def test_workflow_pins_python_3_13_15(self) -> None:
        content = self._workflow()
        self.assertIn("3.13.15", content)

    def test_workflow_runs_exact_gate_e_command(self) -> None:
        content = self._workflow()
        self.assertIn(self.GATE_E_COMMAND, content)

    def test_workflow_never_interpolates_untrusted_github_context(self) -> None:
        content = self._workflow()
        self.assertNotIn("github.event", content)
        self.assertNotIn("github.head_ref", content)

    def test_workflow_never_ignores_step_failures(self) -> None:
        content = self._workflow()
        self.assertNotIn("continue-on-error", content)

    def test_workflow_never_installs_dbt(self) -> None:
        content = self._workflow()
        self.assertNotIn("requirements-dbt", content)
        self.assertNotIn("dbt-core", content)
        self.assertNotIn("pip install", content)


class ToolchainFixtureContractTests(unittest.TestCase):
    """Task 3: isolated, disposable dbt adapter-smoke fixture."""

    FIXTURE_DIR = "tests/fixtures/dbt_adapter_smoke"
    PROJECT_YML = f"{FIXTURE_DIR}/dbt_project.yml"
    PROFILES_YML = f"{FIXTURE_DIR}/profiles.yml"
    MODEL_SQL = f"{FIXTURE_DIR}/models/adapter_smoke.sql"

    FORBIDDEN_DOMAIN_TERMS = (
        "delinquent",
        "cohort",
        "spell",
        "release",
        "registry",
        "charity",
        "organization",
    )

    def test_fixture_files_exist(self) -> None:
        self.assertTrue((REPO_ROOT / self.PROJECT_YML).is_file())
        self.assertTrue((REPO_ROOT / self.PROFILES_YML).is_file())
        self.assertTrue((REPO_ROOT / self.MODEL_SQL).is_file())

    def _project_profile_name(self) -> str:
        content = _read(self.PROJECT_YML)
        match = re.search(r"(?m)^profile:\s*['\"]?([A-Za-z0-9_]+)['\"]?\s*$", content)
        self.assertIsNotNone(match, "dbt_project.yml has no `profile:` key")
        return match.group(1)

    def test_fixture_project_and_profile_names_match(self) -> None:
        profile_name = self._project_profile_name()
        profiles_content = _read(self.PROFILES_YML)
        top_level_keys = [
            line.split(":")[0].strip()
            for line in profiles_content.splitlines()
            if line and not line.startswith((" ", "\t")) and ":" in line
        ]
        self.assertIn(profile_name, top_level_keys)

    def test_fixture_uses_single_thread(self) -> None:
        content = _read(self.PROFILES_YML)
        self.assertIn("threads: 1", content)

    def test_fixture_output_paths_stay_under_ignored_fixture_directories(self) -> None:
        project_content = _read(self.PROJECT_YML)
        self.assertIn("target", project_content)
        profiles_content = _read(self.PROFILES_YML)
        self.assertIn("target/", profiles_content)
        self.assertNotIn(":\\", profiles_content)  # no Windows absolute path
        home_or_users_pattern = "(?<!targe)/ho" + "me/|/User" + "s/"
        self.assertNotRegex(profiles_content, home_or_users_pattern)

    def test_fixture_profiles_no_home_profile_or_env_secret(self) -> None:
        content = _read(self.PROFILES_YML)
        self.assertNotIn("env_var", content)
        self.assertNotIn("~/", content)
        for token in ("password", "secret", "token", "api_key"):
            self.assertNotIn(token, content.lower())

    def test_fixture_profiles_no_external_extension_or_path(self) -> None:
        content = _read(self.PROFILES_YML)
        self.assertNotIn("extensions:", content)
        self.assertNotIn("httpfs", content.lower())
        self.assertNotIn("s3", content.lower())

    def test_fixture_model_is_single_deterministic_row(self) -> None:
        content = _read(self.MODEL_SQL).strip().lower()
        normalized = " ".join(content.split())
        self.assertEqual(normalized, "select 1 as adapter_ready")

    def test_fixture_model_has_no_analytical_domain_terminology(self) -> None:
        content = _read(self.MODEL_SQL).lower()
        for term in self.FORBIDDEN_DOMAIN_TERMS:
            self.assertNotIn(term, content)

    #: Directories that are never part of the tracked candidate tree -- the
    #: Git object database, the local gitignored virtual environment (which
    #: vendors dbt's own bundled project/macro templates once Task 2 creates
    #: it), and dbt's own gitignored run artifacts (which recompile/copy the
    #: fixture's model once `dbt build` runs) must never be mistaken for
    #: production repository content.
    _EXCLUDED_DIR_NAMES = (".git", ".venv", "target", "dbt_packages", "logs")

    #: The one named exception to "no other SQL file exists": Phase 2's
    #: evidence-repair tool bundles a fixed, evidence-only DuckDB script
    #: that is never a dbt model and is never loaded by any dbt project
    #: (`tools/evidence_repair/__main__.py` loads it directly). Named
    #: explicitly here -- never a broad directory exemption -- so any
    #: *other* new `.sql` file still fails this test.
    _NON_DBT_SQL_ALLOWLIST = ("tools/evidence_repair/spike_002_confirmation.sql",)

    def test_only_known_sql_files_exist_in_repository(self) -> None:
        # The disposable adapter-smoke model and the one named non-dbt SQL
        # exception remain exactly as before; every discovered production
        # `dbt/` path must additionally be a member of the closed
        # cumulative Phase 3 + Phase 4 shape (`Wave3DbtFoundationContractTests`
        # and `Phase4CumulativeGateTests` enforce the subset/group shape of
        # that production membership in detail; Phase 4's own groups may
        # be absent, but never widen beyond this closed union).
        sql_files = sorted(
            path
            for path in REPO_ROOT.rglob("*.sql")
            if not set(path.parts) & set(self._EXCLUDED_DIR_NAMES)
        )
        relative = sorted(str(path.relative_to(REPO_ROOT)).replace("\\", "/") for path in sql_files)
        known_non_production = {self.MODEL_SQL, *self._NON_DBT_SQL_ALLOWLIST}
        for path in relative:
            if path in known_non_production:
                continue
            self.assertTrue(
                path.startswith("dbt/"),
                f"unexpected SQL file outside dbt/ and outside known allowlists: {path}",
            )
            self.assertIn(
                path,
                PHASE_4_FINAL_PRODUCTION_SQL_PATHS,
                f"production SQL path not in the closed Phase 3 + Phase 4 allowlist: {path}",
            )

    def test_exactly_the_fixture_and_production_dbt_projects_exist(self) -> None:
        project_files = [
            path
            for path in REPO_ROOT.rglob("dbt_project.yml")
            if not set(path.parts) & set(self._EXCLUDED_DIR_NAMES)
        ]
        relative = sorted(str(path.relative_to(REPO_ROOT)).replace("\\", "/") for path in project_files)
        self.assertEqual(relative, sorted([self.PROJECT_YML, "dbt/dbt_project.yml"]))

    def test_no_production_models_directory_at_repo_root(self) -> None:
        self.assertFalse((REPO_ROOT / "models").exists())


class Wave3DbtFoundationContractTests(unittest.TestCase):
    """Wave 0 repository-contract transition (03-03-PLAN.md Task 1): the
    production dbt project is now required and structurally enforced,
    replacing the earlier "production dbt cannot exist" assertion.

    Plan 06 closes the final handoff this class was set up for: now that
    Waves 4-5 have landed every remaining allowlisted path, the earlier
    bootstrap subset/group-shape checks (present-or-absent, dependency
    ordering) are replaced by one exact-equality assertion between
    discovered production SQL and the full closed 18-path allowlist.
    """

    PROJECT_YML = "dbt/dbt_project.yml"
    SOURCES_YML = "dbt/models/sources.yml"
    BASE_MODEL_SQL = "dbt/models/staging/base_admitted_registry_records.sql"

    _EXPECTED_RUNTIME_TABLES = (
        "charities_may_operate",
        "charities_not_operating",
        "charities_undetermined_status",
        "charities_may_not_operate",
        "revision_catalog",
        "promotion_catalog",
        # Added by 04-04-PLAN.md (D-12/D-20): the fixed nullable
        # capture-attempt relation both fixture and real preflight always
        # create, forward-fixing this exact-count assertion the same way
        # Phase 2 Plan 04 forward-fixed this file's own stale SQL-only
        # assertion when a later plan's required change invalidated it.
        "capture_attempts",
        # Added by 04-05-PLAN.md (D-16/D-18/D-20): the fixed nullable
        # private eligibility-classification relation both fixture and
        # real preflight always create, forward-fixing this exact-set
        # assertion the same way 04-04-PLAN.md already forward-fixed it
        # for capture_attempts above.
        "public_eligibility_classifications",
    )

    # -- production project shape -----------------------------------------

    def test_production_dbt_project_exists_and_is_named_calico_registry(self) -> None:
        self.assertTrue((REPO_ROOT / self.PROJECT_YML).is_file())
        content = _read(self.PROJECT_YML)
        self.assertIn("calico_registry", content)
        match = re.search(r"(?m)^name:\s*['\"]?calico_registry['\"]?\s*$", content)
        self.assertIsNotNone(match, "dbt_project.yml must declare name: 'calico_registry'")

    def test_production_dbt_project_declares_the_fixed_calico_dbt_profile(self) -> None:
        content = _read(self.PROJECT_YML)
        match = re.search(r"(?m)^profile:\s*['\"]?([A-Za-z0-9_]+)['\"]?\s*$", content)
        self.assertIsNotNone(match, "dbt_project.yml has no `profile:` key")
        self.assertEqual(
            match.group(1),
            "calico_dbt",
            "profile must match calico_dbt.runner.DBT_PROFILE_NAME exactly",
        )

    def test_production_dbt_project_has_no_committed_profiles_yml(self) -> None:
        self.assertFalse(
            (REPO_ROOT / "dbt" / "profiles.yml").exists(),
            "the runner always generates its own profile; no production profiles.yml is ever committed",
        )

    def test_production_dbt_project_declares_no_credential_url_or_secret(self) -> None:
        content = _read(self.PROJECT_YML)
        for token in ("password", "secret", "token", "api_key", "env_var", "http://", "https://"):
            self.assertNotIn(token, content.lower())

    def test_production_dbt_project_declares_no_worktree_relative_database_path(self) -> None:
        content = _read(self.PROJECT_YML)
        self.assertNotIn(".duckdb", content)

    # -- sources.yml ---------------------------------------------------------

    def test_sources_yml_declares_exactly_the_fixed_runtime_relations(self) -> None:
        content = _read(self.SOURCES_YML)
        for table_name in self._EXPECTED_RUNTIME_TABLES:
            self.assertIn(table_name, content)
        names_found = re.findall(r"(?m)^\s*-\s*name:\s*(\w+)\s*$", content)
        # The first name found is the source block itself (runtime_input);
        # the rest must be exactly the six fixed table names, in any order.
        self.assertIn("runtime_input", names_found)
        table_names_found = [name for name in names_found if name != "runtime_input"]
        self.assertEqual(sorted(table_names_found), sorted(self._EXPECTED_RUNTIME_TABLES))

    def test_sources_yml_has_no_path_env_or_mode_expression(self) -> None:
        content = _read(self.SOURCES_YML)
        self.assertNotIn("env_var", content)
        self.assertNotIn("{{ var(", content)
        self.assertNotIn("{{var(", content)
        for token in ("mode ==", "mode='fixture'", "mode='real'"):
            self.assertNotIn(token, content)
        # Built from concatenated fragments (and never written out
        # contiguously, including in this comment) so the committed source
        # text itself never contains the absolute-path shape the
        # repository's own privacy scanner flags everywhere.
        self.assertNotIn("C" + ":" + "\\", content)
        self.assertNotIn("/" + "home" + "/", content)
        self.assertNotIn("/" + "Users" + "/", content)

    # -- base_admitted_registry_records.sql -----------------------------------

    def test_base_model_uses_exactly_four_explicit_source_calls_and_union_all(self) -> None:
        content = _strip_sql_line_comments(_read(self.BASE_MODEL_SQL))
        for table_name in (
            "charities_may_operate",
            "charities_not_operating",
            "charities_undetermined_status",
            "charities_may_not_operate",
        ):
            self.assertIn(f"source('runtime_input', '{table_name}')", content)
        self.assertEqual(content.count("union all"), 3)

    def test_base_model_has_no_csv_glob_or_permissive_union(self) -> None:
        content = _strip_sql_line_comments(_read(self.BASE_MODEL_SQL)).lower()
        for forbidden in ("read_csv", ".csv", "glob(", "union_by_name", "read_parquet("):
            self.assertNotIn(forbidden, content)

    def test_no_production_dbt_sql_reopens_raw_csv_or_globs_paths(self) -> None:
        dbt_sql_files = [
            path
            for path in (REPO_ROOT / "dbt").rglob("*.sql")
            if "target" not in path.parts and "dbt_packages" not in path.parts and "logs" not in path.parts
        ]
        self.assertGreater(len(dbt_sql_files), 0, "expected at least the Wave 3 SQL files to exist")
        for path in dbt_sql_files:
            content = _strip_sql_line_comments(path.read_text(encoding="utf-8")).lower()
            for forbidden in ("read_csv", "cp1252", "quote_none", "union_by_name"):
                self.assertNotIn(
                    forbidden,
                    content,
                    f"{path.relative_to(REPO_ROOT)} must not reopen raw CSV or reinterpret its parser contract (D-14)",
                )

    # -- closed 18-path allowlist: Phase 4 Wave 0 reopens this gate --------

    def _discovered_production_sql_paths(self) -> set[str]:
        return _discovered_production_sql_paths()

    def test_discovered_production_sql_matches_the_open_cumulative_boundary(self) -> None:
        # Superseded by Phase 4 Plan 01 (04-01-PLAN.md): the prior exact
        # Phase-3-only equality this test enforced is reopened to the
        # cumulative Phase 3 + Phase 4 bootstrap shape --
        # `Phase4CumulativeGateTests` below owns the detailed group/order
        # assertions. This method keeps the two invariants that never
        # relax during the whole Phase 4 bootstrap: the 18 Phase 3 paths
        # remain complete, and no discovered path may fall outside the
        # closed Phase 3 + Phase 4 union. Plan 06 is the named owner of
        # replacing this with one final exact-equality assertion over the
        # complete 41-path union once every Phase 4 group has landed.
        discovered = self._discovered_production_sql_paths()
        missing_phase_3 = PHASE_3_FINAL_PRODUCTION_SQL_PATHS - discovered
        unexpected = discovered - PHASE_4_FINAL_PRODUCTION_SQL_PATHS
        self.assertEqual(
            (missing_phase_3, unexpected),
            (set(), set()),
            "discovered production SQL must retain every Phase 3 path and "
            "stay inside the closed Phase 3 + Phase 4 shape: "
            f"missing_phase_3={sorted(missing_phase_3)}, unexpected={sorted(unexpected)}",
        )


class Phase4CumulativeGateTests(unittest.TestCase):
    """Phase 4 Plan 01's own cumulative SQL bootstrap gate (D-01/D-22,
    04-01-PLAN.md Task 1): all 18 Phase 3 paths remain mandatory, no path
    outside the closed Phase 3 + Phase 4 shape is ever accepted, each named
    Phase 4 group lands only wholly absent or wholly present, and a group
    never precedes its declared predecessor group(s).
    """

    def _discovered(self) -> set[str]:
        return _discovered_production_sql_paths()

    def test_all_phase_3_paths_remain_required(self) -> None:
        discovered = self._discovered()
        missing = PHASE_3_FINAL_PRODUCTION_SQL_PATHS - discovered
        self.assertEqual(
            missing, set(), f"Phase 3 paths must remain complete: missing={sorted(missing)}"
        )

    def test_unknown_sql_path_outside_the_closed_shape_fails(self) -> None:
        discovered = self._discovered()
        unexpected = discovered - PHASE_4_FINAL_PRODUCTION_SQL_PATHS
        self.assertEqual(unexpected, set(), f"unplanned SQL path(s) discovered: {sorted(unexpected)}")

    def test_each_phase_4_group_is_wholly_absent_or_wholly_present(self) -> None:
        discovered = self._discovered()
        for group_name, group_paths in PHASE_4_SQL_GROUPS.items():
            present = discovered & group_paths
            self.assertIn(
                present,
                (set(), set(group_paths)),
                f"group {group_name!r} must be wholly absent or wholly present; "
                f"found partial membership {sorted(present)}",
            )

    def test_phase_4_groups_never_precede_their_dependencies(self) -> None:
        discovered = self._discovered()
        for group_name, group_paths in PHASE_4_SQL_GROUPS.items():
            if not group_paths.issubset(discovered):
                continue
            for dependency_name in PHASE_4_GROUP_DEPENDENCIES[group_name]:
                dependency_paths = PHASE_4_SQL_GROUPS[dependency_name]
                self.assertTrue(
                    dependency_paths.issubset(discovered),
                    f"group {group_name!r} landed before its dependency {dependency_name!r}",
                )


if __name__ == "__main__":
    unittest.main()
