"""One safe fixture-default/explicit-real dbt build service (D-01..D-04, D-15).

`build()` is the single entry point both the CLI (`__main__.py`) and test
callers use. Fixture mode admits Plan 01's closed Gate B fixture through the
real `calico_landing.admission.admit()` boundary into a throwaway store,
derives an *ephemeral* manifest-anchor-only catalog from that store's own
just-written manifests, and calls `preflight.prepare_runtime_input()` --
exactly the same call real mode makes. Real mode always loads the one fixed,
committed catalog document and requires an explicit, existing,
non-worktree `store` path. Both modes then generate one dbt profile inside
one runner-owned OS temporary root, invoke pinned dbt with explicit
`--project-dir/--profiles-dir/--target-path/--log-path
--packages-install-path` arguments all contained by that root, and remove
the whole root in `finally` -- on success, on dbt failure, and on any
exception.

Every safe result this module returns or writes -- `BuildOutcome`,
`SafeBuildProof`, and the fixture-only `FixtureBuildInspection` facade --
carries only fixed schema/command IDs, mode, counts, and closed-vocabulary
categories. Nothing here ever serializes a path, row, excluded value, SQL
string, or raw dbt/child stdout/stderr (D-15).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from calico_dbt import catalog as cat
from calico_dbt import preflight as pf
from calico_landing.candidate import CandidateError, reject_store_in_git_worktree
from calico_landing.contracts import LOGICAL_LIST_ORDER

#: The fixed, committed real-mode catalog path -- resolved the same way
#: `calico_landing.admission` resolves its own fixed contract path. Real
#: mode always loads exactly this document; it is never a CLI argument
#: (D-02: real mode is explicit about the *store*, never about which
#: catalog to trust). A later evidence/proof plan is responsible for
#: populating this file for the three real admitted releases; its absence
#: here fails real mode closed with `runner.catalog_not_found`, which is
#: the expected state prior to that plan landing.
_REAL_CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "contracts" / "dbt-input-catalog-v1.json"
)

#: The fixed product dbt project directory a later Phase 3 plan (wave 3)
#: creates at `../calico/dbt`. Both build modes point at this same project
#: and DAG (D-03/D-04) unless a caller overrides it -- overriding is a
#: test-only seam (`_dbt_project_dir_override`), never a CLI option, so
#: this plan's own tests can exercise real `dbt ls`/`dbt build` subprocess
#: behavior against a disposable project before that project exists.
_DEFAULT_DBT_PROJECT_DIR = Path(__file__).resolve().parent.parent / "dbt"

#: The fixed dbt profile name this module always generates and that the
#: wave-3 `dbt_project.yml` must declare via `profile: 'calico_dbt'` for
#: both build modes to keep working unchanged.
DBT_PROFILE_NAME = "calico_dbt"

#: The fixed, closed `--select` alias vocabulary (D-04 discretion). Every
#: alias maps to an exact internal dbt selection string; no other selector,
#: Jinja, SQL, or path is ever accepted or forwarded.
SELECT_ALIASES: dict[str, str] = {
    "staging-base": "base_admitted_registry_records",
    "source-staging": "source:runtime_input+",
    # Ancestor-inclusive (`+`): a standalone verification build of
    # `int_promoted_registry_records` needs `stg_registry_records` (and its
    # own `base_admitted_registry_records` ancestor) to already exist, or
    # `dbt build` fails with a missing-relation error rather than proving
    # the promotion join. `int_promoted_releases` itself has no model
    # ancestors (it reads the two fixed `runtime_input` sources directly),
    # so including it is free.
    "promotion": "+int_promoted_registry_records",
    # Ancestor-inclusive for the same reason: `int_adjacent_release_pairs`
    # needs its `int_promoted_date_spine` ancestor (which in turn needs
    # `int_promoted_releases`) to already exist.
    "adjacency": "+int_adjacent_release_pairs",
    "promotion-adjacency": "+int_promoted_registry_records +int_adjacent_release_pairs",
    # Both directions: a standalone verification build of this alias must
    # also build its upstream staging dependency, or `dbt build` fails with
    # a missing-relation error rather than proving the disposition rule.
    "dispositions": "+int_registry_record_dispositions+",
    # Phase 4 closure (04-06-PLAN.md Task 1): one ancestor-inclusive alias
    # per Plan 02-05 SQL group, mirroring
    # `tests.test_repository_contract.PHASE_4_SQL_GROUPS`'s own four named
    # groups so a targeted verification build always matches the same
    # boundary the repository contract enforces. Never a caller-supplied
    # selector -- every alias resolves to exactly these fixed internal
    # selection strings.
    "longitudinal-transitions": (
        "+int_keyed_snapshots +int_unkeyed_coverage +int_entity_transitions +int_transition_matrix"
    ),
    "longitudinal-facts": "+int_entity_observation_sequence +int_delinquency_spells",
    "capture-facts": "+stg_capture_attempts +int_capture_runs +int_release_flags",
    "public-models": (
        "+int_public_organization_eligibility +mart_registry_population_coverage "
        "+dim_public_organizations +fct_public_status_observations"
    ),
}

#: The fixed product-relative real-mode proof destination (D-15). Always
#: this exact path -- never a caller-supplied value.
_PROOF_OUTPUT_RELATIVE_PATH = Path("docs") / "evidence" / "gate-b" / "real-build-proof-v1.json"

_DBT_SUBPROCESS_TIMEOUT_SECONDS = 600

PROOF_SCHEMA_VERSION = 1
COMMAND_SCHEMA_VERSION = 1

_MODES = frozenset({"fixture", "real"})


class RunnerError(Exception):
    """Raised on any invalid mode/select/store combination or internal
    build failure this module decided. Carries only a fixed safe
    `category` -- never a path, argument value, or exception text.
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class SafeBuildProof:
    """One closed, deterministic, JSON-serializable build outcome (D-15).

    Every field is a fixed schema/command ID, the mode, a fixed status, a
    safe count, or a safe closed-vocabulary category -- never a path, row,
    excluded value, or raw child output.
    """

    proof_schema_version: int
    command_schema_version: int
    mode: str
    status: str
    verified_release_count: int
    verified_object_count: int
    dbt_selected_node_count: int
    dbt_model_count: int
    dbt_test_count: int

    def to_json(self) -> str:
        document = {
            "proof_schema_version": self.proof_schema_version,
            "command_schema_version": self.command_schema_version,
            "mode": self.mode,
            "status": self.status,
            "verified_release_count": self.verified_release_count,
            "verified_object_count": self.verified_object_count,
            "dbt_selected_node_count": self.dbt_selected_node_count,
            "dbt_model_count": self.dbt_model_count,
            "dbt_test_count": self.dbt_test_count,
        }
        return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True)
class BuildOutcome:
    """The safe, non-echo result of one `build()` call."""

    status: str
    category: str | None
    proof: SafeBuildProof | None

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


DOCS_PROOF_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SafeDocsProof:
    """One closed, deterministic, JSON-serializable fixture-only docs proof
    (D-15, D-20). Every field is a fixed schema/command ID, the fixed
    `"fixture"` mode, a fixed status, or a safe count -- never a path, row,
    excluded value, or raw child output.
    """

    proof_schema_version: int
    command_schema_version: int
    mode: str
    status: str
    dbt_selected_node_count: int
    dbt_model_count: int
    dbt_test_count: int
    docs_node_count: int
    docs_artifact_count: int

    def to_json(self) -> str:
        document = {
            "proof_schema_version": self.proof_schema_version,
            "command_schema_version": self.command_schema_version,
            "mode": self.mode,
            "status": self.status,
            "dbt_selected_node_count": self.dbt_selected_node_count,
            "dbt_model_count": self.dbt_model_count,
            "dbt_test_count": self.dbt_test_count,
            "docs_node_count": self.docs_node_count,
            "docs_artifact_count": self.docs_artifact_count,
        }
        return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True)
class DocsOutcome:
    """The safe, non-echo result of one `docs()` call."""

    status: str
    category: str | None
    proof: SafeDocsProof | None

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


class FixtureBuildInspection:
    """A closed, read-only, fixture-only facade over the still-open runner
    temporary database (T-03-08).

    Exposes exactly three fixed, constant-SQL projections -- never a path,
    connection, generic query method, or registry-record projection.
    Constructed only after `dbt build` succeeds and only while the
    runner-owned database file still exists; never available in real mode
    or from the CLI.
    """

    def __init__(self, duckdb_path: Path) -> None:
        self._duckdb_path = duckdb_path

    def _query(self, sql: str) -> tuple[tuple, ...]:
        import duckdb

        connection = duckdb.connect(str(self._duckdb_path), read_only=True)
        try:
            return tuple(connection.execute(sql).fetchall())
        finally:
            connection.close()

    def revision_catalog_rows(self) -> tuple[tuple[str, int, str], ...]:
        rows = self._query(
            "SELECT as_of_date, release_revision, revision_fingerprint "
            "FROM runtime_input.revision_catalog "
            "ORDER BY as_of_date, release_revision"
        )
        return tuple((row[0], row[1], row[2]) for row in rows)

    def promoted_release_rows(self) -> tuple[tuple[str, int, str], ...]:
        rows = self._query(
            "SELECT as_of_date, release_revision, revision_fingerprint "
            "FROM runtime_input.promotion_catalog "
            "ORDER BY as_of_date"
        )
        return tuple((row[0], row[1], row[2]) for row in rows)

    def adjacent_release_pair_rows(self) -> tuple[tuple[str, str, str, str, int], ...]:
        rows = self._query(
            "WITH ordered AS ("
            "  SELECT as_of_date, revision_fingerprint, "
            "         ROW_NUMBER() OVER (ORDER BY as_of_date) AS rn "
            "  FROM runtime_input.promotion_catalog"
            ") "
            "SELECT a.as_of_date, a.revision_fingerprint, b.as_of_date, b.revision_fingerprint, "
            "       DATE_DIFF('day', CAST(a.as_of_date AS DATE), CAST(b.as_of_date AS DATE)) AS gap_days "
            "FROM ordered a JOIN ordered b ON b.rn = a.rn + 1 "
            "ORDER BY a.as_of_date"
        )
        return tuple((row[0], row[1], row[2], row[3], row[4]) for row in rows)


def _resolve_select(select: str | None) -> str | None:
    if select is None:
        return None
    if select not in SELECT_ALIASES:
        raise RunnerError("runner.invalid_select_alias")
    return SELECT_ALIASES[select]


def _dbt_executable() -> str:
    """Resolve the pinned `dbt` console script installed alongside the
    current interpreter. dbt-core 1.10 ships no `dbt.__main__`, so
    `python -m dbt` cannot invoke it -- the console script is the only
    stable entry point.
    """

    scripts_dir = Path(sys.executable).resolve().parent
    candidate = scripts_dir / ("dbt.exe" if os.name == "nt" else "dbt")
    if candidate.is_file():
        return str(candidate)
    return "dbt"  # fall back to PATH resolution (e.g. a non-venv interpreter)


def _dbt_command(sub: list[str], *, project_dir: Path, profiles_dir: Path, target_path: Path, log_path: Path) -> list[str]:
    # `--packages-install-path` is intentionally not passed: dbt-core 1.10
    # exposes no such CLI flag (only a `dbt_project.yml` key), so package
    # containment is the product project's own configuration, not this
    # runner's concern.
    return [
        _dbt_executable(),
        *sub,
        "--project-dir",
        str(project_dir),
        "--profiles-dir",
        str(profiles_dir),
        "--target-path",
        str(target_path),
        "--log-path",
        str(log_path),
    ]


def _run_dbt(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_DBT_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RunnerError("runner.dbt_invocation_failed") from exc


def _write_profile(profiles_dir: Path, duckdb_path: Path) -> None:
    # Written by hand (never a templating engine) so the generated document
    # never carries an owner path outside the two fixed, safe fields below.
    content = (
        f"{DBT_PROFILE_NAME}:\n"
        "  target: runtime\n"
        "  outputs:\n"
        "    runtime:\n"
        "      type: duckdb\n"
        f"      path: '{duckdb_path.as_posix()}'\n"
        "      threads: 2\n"
    )
    (profiles_dir / "profiles.yml").write_text(content, encoding="utf-8")


def _ls_selected_nodes(selection: str | None, *, project_dir: Path, profiles_dir: Path, target_path: Path, log_path: Path) -> list[str]:
    sub = ["--quiet", "ls", "--output", "name"]
    if selection is not None:
        sub += ["--select", *selection.split()]
    cmd = _dbt_command(
        sub,
        project_dir=project_dir,
        profiles_dir=profiles_dir,
        target_path=target_path,
        log_path=log_path,
    )
    result = _run_dbt(cmd)
    if result.returncode != 0:
        raise RunnerError("runner.dbt_ls_failed")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _run_dbt_build(selection: str | None, *, project_dir: Path, profiles_dir: Path, target_path: Path, log_path: Path) -> None:
    sub = ["build"]
    if selection is not None:
        sub += ["--select", *selection.split()]
        # `cautious`, not the `eager` default: a partial-selection verify
        # build must never pull in a singular test whose *other* parent
        # (e.g. a sibling Wave 4 model not part of this alias) was not
        # itself selected/built -- that test would immediately fail with a
        # missing-relation error even though the alias's own owned models
        # and tests are all correct.
        sub += ["--indirect-selection", "cautious"]
    cmd = _dbt_command(
        sub,
        project_dir=project_dir,
        profiles_dir=profiles_dir,
        target_path=target_path,
        log_path=log_path,
    )
    result = _run_dbt(cmd)
    if result.returncode != 0:
        raise RunnerError("runner.dbt_build_failed")


def _run_results_counts(target_path: Path) -> tuple[int, int]:
    run_results_path = target_path / "run_results.json"
    try:
        document = json.loads(run_results_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return (0, 0)

    results = document.get("results")
    if not isinstance(results, list):
        return (0, 0)

    model_count = 0
    test_count = 0
    for entry in results:
        if not isinstance(entry, dict):
            continue
        unique_id = entry.get("unique_id")
        if not isinstance(unique_id, str):
            continue
        if unique_id.startswith("model."):
            model_count += 1
        elif unique_id.startswith("test."):
            test_count += 1
    return (model_count, test_count)


def _run_dbt_docs_generate(
    *, project_dir: Path, profiles_dir: Path, target_path: Path, log_path: Path
) -> None:
    cmd = _dbt_command(
        ["docs", "generate"],
        project_dir=project_dir,
        profiles_dir=profiles_dir,
        target_path=target_path,
        log_path=log_path,
    )
    result = _run_dbt(cmd)
    if result.returncode != 0:
        raise RunnerError("runner.dbt_docs_generate_failed")


def _docs_safe_counts(target_path: Path) -> tuple[int, int]:
    """Read only safe counts from the generated `catalog.json` plus a count
    of generated artifact files -- never a name, path, or content beyond
    those two counts (D-15).
    """

    catalog_path = target_path / "catalog.json"
    try:
        document = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return (0, 0)

    nodes = document.get("nodes")
    node_count = len(nodes) if isinstance(nodes, dict) else 0

    try:
        artifact_count = sum(1 for entry in target_path.iterdir() if entry.is_file())
    except OSError:
        artifact_count = 0

    return (node_count, artifact_count)


def _build_fixture_catalog(store_root: Path, admissions) -> cat.InputCatalog:
    manifests = []
    for admission in admissions:
        result = admission.result
        manifest_path = (
            store_root
            / "releases"
            / result.as_of_date
            / f"rev-{result.release_revision:04d}-{result.revision_fingerprint[:8]}"
            / "manifest.json"
        )
        manifests.append(
            (
                result.as_of_date,
                result.release_revision,
                result.revision_fingerprint,
                manifest_path.read_bytes(),
            )
        )
    return cat.build_catalog_from_manifests(manifests)


def _load_real_catalog() -> cat.InputCatalog:
    try:
        return cat.load_input_catalog(_REAL_CATALOG_PATH)
    except cat.CatalogError as exc:
        raise RunnerError("runner.catalog_not_found") from exc


def _prepare_environment(
    *,
    mode: str,
    store: str | Path | None,
    temp_root: Path,
    fixture_store_factory: Callable[[], AbstractContextManager] | None,
) -> tuple[AbstractContextManager | None, "pf.RuntimeInputBinding"]:
    """Shared fixture-admit-or-real-verify plus preflight-bind plus
    profile-write lifecycle both `build()` and `docs()` call identically
    (D-01..D-04/D-20).

    Returns the still-open fixture context (`None` in real mode) and the
    resulting `RuntimeInputBinding`. Writes `profiles.yml` into `temp_root`
    as a side effect. The caller owns closing the returned fixture context
    (`__exit__`) once it is done with it -- this function never closes it
    itself, since the fixture store's admitted objects must stay readable
    for the whole build/docs lifecycle that follows.
    """

    fixture_context = None
    if mode == "fixture":
        factory = fixture_store_factory
        if factory is None:
            from tests.fixtures.dbt_foundation.fixture_builder import gate_b_fixture_store

            factory = gate_b_fixture_store
        fixture_context = factory()
        fixture_store = fixture_context.__enter__()
        catalog = _build_fixture_catalog(fixture_store.store_root, fixture_store.admissions)
        resolved_store_root = fixture_store.store_root
    else:
        # Store legitimacy (existence, non-worktree) is the explicit D-02
        # gate and is checked before the committed catalog is even loaded,
        # so a store-path mistake is never masked by a not-yet-populated
        # catalog document.
        resolved_store_root = _resolve_real_store(store)
        catalog = _load_real_catalog()

    binding = pf.prepare_runtime_input(
        store_root=resolved_store_root, catalog=catalog, temp_root=temp_root
    )

    _write_profile(temp_root, binding.duckdb_path)

    return fixture_context, binding


def _resolve_real_store(store: str | Path) -> Path:
    try:
        reject_store_in_git_worktree(store)
    except CandidateError as exc:
        raise RunnerError("runner.store_in_worktree") from exc

    raw = Path(store)
    if raw.is_symlink():
        raise RunnerError("runner.invalid_store")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise RunnerError("runner.invalid_store") from exc
    if not resolved.is_dir():
        raise RunnerError("runner.invalid_store")
    return resolved


def build(
    *,
    mode: str,
    store: str | Path | None = None,
    select: str | None = None,
    proof_output: bool = False,
    fixture_store_factory: Callable[[], AbstractContextManager] | None = None,
    inspector: Callable[["FixtureBuildInspection"], None] | None = None,
    _dbt_project_dir_override: str | Path | None = None,
) -> BuildOutcome:
    """Prepare verified input and run one full pinned dbt build for `mode`.

    `mode` must be exactly `"fixture"` or `"real"`. `store` is required
    (and must be an existing, non-worktree directory) for `"real"` and
    forbidden for `"fixture"`. `select` must be `None` (full build) or one
    of the closed `SELECT_ALIASES` keys. `proof_output` is honored only for
    `"real"` mode and only after every manifest/object verification and dbt
    node/test succeed.

    `fixture_store_factory` and `inspector` are test/integration-only
    seams -- `fixture_store_factory` defaults to Plan 01's
    `gate_b_fixture_store` and is never exposed by the CLI; `inspector` is
    rejected outright in real mode and is never invoked when dbt fails.
    `_dbt_project_dir_override` exists solely so this module's own test
    suite can exercise real dbt subprocess behavior before the wave-3
    product project exists; it is never a CLI option and production
    callers must never pass it.

    Every generated file (opaque input copies, the on-disk DuckDB database,
    the generated profile, and every dbt target/log/package artifact) lives
    beneath one runner-owned OS temporary root that is removed in `finally`
    on every exit path -- success, dbt failure, preflight failure, or an
    interrupting exception.
    """

    if mode not in _MODES:
        return BuildOutcome(status="failed", category="runner.invalid_mode", proof=None)

    if mode == "real" and inspector is not None:
        return BuildOutcome(status="failed", category="runner.inspector_not_allowed_real_mode", proof=None)

    if mode == "fixture" and store is not None:
        return BuildOutcome(status="failed", category="runner.store_not_allowed_fixture_mode", proof=None)

    if mode == "real" and store is None:
        return BuildOutcome(status="failed", category="runner.store_required_real_mode", proof=None)

    try:
        selection = _resolve_select(select)
    except RunnerError as exc:
        return BuildOutcome(status="failed", category=exc.category, proof=None)

    project_dir = Path(_dbt_project_dir_override) if _dbt_project_dir_override is not None else _DEFAULT_DBT_PROJECT_DIR

    temp_root = Path(tempfile.mkdtemp(prefix="calico-dbt-build-"))
    try:
        target_path = temp_root / "target"
        log_path = temp_root / "logs"
        for path in (target_path, log_path):
            path.mkdir(parents=True, exist_ok=True)

        fixture_context = None
        try:
            fixture_context, binding = _prepare_environment(
                mode=mode, store=store, temp_root=temp_root, fixture_store_factory=fixture_store_factory
            )

            selected_nodes = _ls_selected_nodes(
                selection,
                project_dir=project_dir,
                profiles_dir=temp_root,
                target_path=target_path,
                log_path=log_path,
            )
            if not selected_nodes:
                return BuildOutcome(status="failed", category="runner.empty_selection", proof=None)

            _run_dbt_build(
                selection,
                project_dir=project_dir,
                profiles_dir=temp_root,
                target_path=target_path,
                log_path=log_path,
            )

            model_count, test_count = _run_results_counts(target_path)

            if mode == "fixture" and inspector is not None:
                try:
                    inspector(FixtureBuildInspection(binding.duckdb_path))
                except Exception as exc:  # noqa: BLE001 -- fixed safe category only
                    return BuildOutcome(
                        status="failed", category="runner.fixture_inspection_failed", proof=None
                    )

            proof = SafeBuildProof(
                proof_schema_version=PROOF_SCHEMA_VERSION,
                command_schema_version=COMMAND_SCHEMA_VERSION,
                mode=mode,
                status="success",
                verified_release_count=binding.verified_release_count,
                verified_object_count=binding.verified_object_count,
                dbt_selected_node_count=len(selected_nodes),
                dbt_model_count=model_count,
                dbt_test_count=test_count,
            )

            if mode == "real" and proof_output:
                _write_proof_output(proof)

            return BuildOutcome(status="success", category=None, proof=proof)
        finally:
            if fixture_context is not None:
                fixture_context.__exit__(None, None, None)
    except (pf.PreflightError, cat.CatalogError) as exc:
        return BuildOutcome(status="failed", category=exc.category, proof=None)
    except RunnerError as exc:
        return BuildOutcome(status="failed", category=exc.category, proof=None)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def docs(*, _dbt_project_dir_override: str | Path | None = None) -> DocsOutcome:
    """Run one full pinned fixture-mode dbt build, then pinned dbt 1.10.23
    `docs generate`, inside the same kind of runner-owned temporary root
    `build()` itself uses (D-20).

    Always fixture mode -- there is no `mode`/`store` argument, because the
    docs proof must never run against a real, private store (T-04-06B).
    Returns a closed `SafeDocsProof` carrying only fixed schema/status/count
    fields -- never a path, row, or raw dbt/child output. Every generated
    file (the fixture's opaque input copies, the on-disk DuckDB database,
    the generated profile, and every dbt target/log/package/catalog
    artifact `docs generate` writes) lives beneath one runner-owned OS
    temporary root that is removed in `finally` on every exit path --
    success, dbt failure, preflight failure, or an interrupting exception.

    `_dbt_project_dir_override` is the identical test/integration-only seam
    `build()` accepts; production callers must never pass it.
    """

    project_dir = (
        Path(_dbt_project_dir_override) if _dbt_project_dir_override is not None else _DEFAULT_DBT_PROJECT_DIR
    )

    temp_root = Path(tempfile.mkdtemp(prefix="calico-dbt-docs-"))
    try:
        target_path = temp_root / "target"
        log_path = temp_root / "logs"
        for path in (target_path, log_path):
            path.mkdir(parents=True, exist_ok=True)

        fixture_context = None
        try:
            fixture_context, binding = _prepare_environment(
                mode="fixture", store=None, temp_root=temp_root, fixture_store_factory=None
            )

            selected_nodes = _ls_selected_nodes(
                None,
                project_dir=project_dir,
                profiles_dir=temp_root,
                target_path=target_path,
                log_path=log_path,
            )
            if not selected_nodes:
                return DocsOutcome(status="failed", category="runner.empty_selection", proof=None)

            _run_dbt_build(
                None,
                project_dir=project_dir,
                profiles_dir=temp_root,
                target_path=target_path,
                log_path=log_path,
            )

            model_count, test_count = _run_results_counts(target_path)

            _run_dbt_docs_generate(
                project_dir=project_dir,
                profiles_dir=temp_root,
                target_path=target_path,
                log_path=log_path,
            )

            docs_node_count, docs_artifact_count = _docs_safe_counts(target_path)

            proof = SafeDocsProof(
                proof_schema_version=DOCS_PROOF_SCHEMA_VERSION,
                command_schema_version=COMMAND_SCHEMA_VERSION,
                mode="fixture",
                status="success",
                dbt_selected_node_count=len(selected_nodes),
                dbt_model_count=model_count,
                dbt_test_count=test_count,
                docs_node_count=docs_node_count,
                docs_artifact_count=docs_artifact_count,
            )
            return DocsOutcome(status="success", category=None, proof=proof)
        finally:
            if fixture_context is not None:
                fixture_context.__exit__(None, None, None)
    except (pf.PreflightError, cat.CatalogError) as exc:
        return DocsOutcome(status="failed", category=exc.category, proof=None)
    except RunnerError as exc:
        return DocsOutcome(status="failed", category=exc.category, proof=None)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def _write_proof_output(proof: SafeBuildProof) -> None:
    destination = Path(__file__).resolve().parent.parent / _PROOF_OUTPUT_RELATIVE_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)

    fd_dir = destination.parent
    temp_name = destination.name + f".tmp-{os.getpid()}"
    temp_path = fd_dir / temp_name
    try:
        temp_path.write_text(proof.to_json(), encoding="utf-8", newline="\n")
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)


__all__ = [
    "DBT_PROFILE_NAME",
    "SELECT_ALIASES",
    "RunnerError",
    "SafeBuildProof",
    "BuildOutcome",
    "SafeDocsProof",
    "DocsOutcome",
    "FixtureBuildInspection",
    "build",
    "docs",
]
