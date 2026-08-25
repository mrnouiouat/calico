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

    def test_fixture_is_the_only_dbt_model_in_repository(self) -> None:
        sql_files = sorted(
            path
            for path in REPO_ROOT.rglob("*.sql")
            if not set(path.parts) & set(self._EXCLUDED_DIR_NAMES)
        )
        relative = sorted(str(path.relative_to(REPO_ROOT)).replace("\\", "/") for path in sql_files)
        self.assertEqual(relative, [self.MODEL_SQL])

    def test_no_production_dbt_project_exists_outside_fixture(self) -> None:
        project_files = [
            path
            for path in REPO_ROOT.rglob("dbt_project.yml")
            if not set(path.parts) & set(self._EXCLUDED_DIR_NAMES)
        ]
        relative = sorted(str(path.relative_to(REPO_ROOT)).replace("\\", "/") for path in project_files)
        self.assertEqual(relative, [self.PROJECT_YML])

    def test_no_production_models_directory_at_repo_root(self) -> None:
        self.assertFalse((REPO_ROOT / "models").exists())


if __name__ == "__main__":
    unittest.main()
