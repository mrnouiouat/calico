"""Machine-checkable Phase 4 closure contract (04-06-PLAN.md Task 1,
T-04-06A/T-04-06D): the authoritative ten-grain map, forbidden duplicate
ownership, the Phase 3 -> Phase 4 -> safe-output lineage edges, the Phase 5
non-ownership boundary, the three D-21 representative models, and the seven
required SQL techniques at their correct representative/upstream models
(REQ-model-grains, REQ-sql-techniques).

Reads only committed-candidate source text from disk -- SQL, YAML, and
`docs/model-grains.md` itself -- exactly like `test_repository_contract.py`.
Never runs dbt and never echoes a matched source value; every assertion is a
membership/count/order check over known-safe structural strings (D-15).

Run:
    py -V:3.13 -m unittest tests.dbt_longitudinal.test_lineage_contract -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DBT_ROOT = REPO_ROOT / "dbt"


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _strip_sql_line_comments(content: str) -> str:
    """Drop everything from `--` to end-of-line, mirroring
    `test_repository_contract.py`'s own helper -- explanatory SQL comments
    legitimately name techniques/forbidden tokens; only executable SQL is
    ever inspected below."""

    return "\n".join(line.split("--", 1)[0] for line in content.splitlines())


#: The exact ten required grains and their one authoritative owning
#: relation, in the 04-06-PLAN.md interfaces block's own stated order.
REQUIRED_GRAIN_OWNERS: dict[str, str] = {
    "landed source record": "base_admitted_registry_records",
    "keyed snapshot": "int_keyed_snapshots",
    "unkeyed coverage": "int_unkeyed_coverage",
    "transition": "int_entity_transitions",
    "status spell": "int_delinquency_spells",
    "capture run": "int_capture_runs",
    "release flag": "int_release_flags",
    "aggregate report mart": "mart_registry_population_coverage",
    "public organization": "dim_public_organizations",
    "public status observation": "fct_public_status_observations",
}

#: D-21: exactly these three models are marked for later annotated
#: README/walkthrough treatment.
REPRESENTATIVE_MODELS: frozenset[str] = frozenset(
    {"int_entity_transitions", "int_delinquency_spells", "fct_public_status_observations"}
)

#: Every helper/audit relation this phase's DAG contains that is *not* one
#: of the ten grain owners above -- nine delivered Phase 3 relations plus
#: four new Phase 4 relations this plan documents but does not rewrite.
HELPER_MODEL_NAMES: frozenset[str] = frozenset(
    {
        "stg_registry_records",
        "int_promoted_releases",
        "int_promoted_registry_records",
        "int_promoted_date_spine",
        "int_adjacent_release_pairs",
        "int_registry_record_dispositions",
        "int_keyless_registry_coverage",
        "int_registry_record_exclusions",
        "int_revision_catalog",
        "int_transition_matrix",
        "int_entity_observation_sequence",
        "stg_capture_attempts",
        "int_public_organization_eligibility",
    }
)

#: The new Phase 4 models this plan requires to carry a `Grain:`-prefixed
#: description -- the nine new grain owners plus the four new Phase 4
#: helpers. `base_admitted_registry_records` is handled by its own
#: dedicated test below since it is the one pre-existing Phase 3 relation
#: this plan is explicitly allowed to touch (D-01).
_NEW_PHASE_4_MODEL_NAMES: frozenset[str] = frozenset(
    {
        "int_keyed_snapshots",
        "int_unkeyed_coverage",
        "int_entity_transitions",
        "int_transition_matrix",
        "int_entity_observation_sequence",
        "int_delinquency_spells",
        "stg_capture_attempts",
        "int_capture_runs",
        "int_release_flags",
        "int_public_organization_eligibility",
        "mart_registry_population_coverage",
        "dim_public_organizations",
        "fct_public_status_observations",
    }
)

#: Safe output models: publication-facing positive projections (D-007/D-17).
SAFE_OUTPUT_MODELS: frozenset[str] = frozenset(
    {"mart_registry_population_coverage", "dim_public_organizations", "fct_public_status_observations"}
)

#: Every discovered lineage edge required for a reviewer to follow the
#: complete Phase 3 -> Phase 4 -> safe-output path (D-01/D-20). Expressed as
#: (upstream_model, downstream_model) pairs; each must appear as a
#: `ref('upstream_model')` call inside `downstream_model.sql`.
_REQUIRED_LINEAGE_EDGES: tuple[tuple[str, str], ...] = (
    # Phase 3 -> Phase 4
    ("int_registry_record_dispositions", "int_keyed_snapshots"),
    ("int_promoted_registry_records", "int_keyed_snapshots"),
    ("int_keyless_registry_coverage", "int_unkeyed_coverage"),
    ("int_adjacent_release_pairs", "int_entity_transitions"),
    ("int_keyed_snapshots", "int_entity_transitions"),
    ("int_promoted_date_spine", "int_entity_observation_sequence"),
    ("int_keyed_snapshots", "int_entity_observation_sequence"),
    ("int_registry_record_dispositions", "mart_registry_population_coverage"),
    ("int_promoted_releases", "int_release_flags"),
    ("int_adjacent_release_pairs", "int_release_flags"),
    ("int_registry_record_dispositions", "int_release_flags"),
    ("int_promoted_releases", "fct_public_status_observations"),
    # Phase 4 -> Phase 4 (safe-output edges)
    ("int_entity_transitions", "int_transition_matrix"),
    ("int_entity_observation_sequence", "int_delinquency_spells"),
    ("int_keyed_snapshots", "int_public_organization_eligibility"),
    ("int_public_organization_eligibility", "dim_public_organizations"),
    ("int_keyed_snapshots", "dim_public_organizations"),
    ("int_public_organization_eligibility", "fct_public_status_observations"),
    ("int_keyed_snapshots", "fct_public_status_observations"),
    ("stg_capture_attempts", "int_capture_runs"),
    ("int_capture_runs", "int_release_flags"),
)

#: A model name looking like a competing owner for one of the five Phase 3
#: foundational responsibilities (D-01) would need one of these substrings.
_FOUNDATIONAL_OWNER_TERMS: tuple[str, ...] = (
    "promoted_release",
    "date_spine",
    "adjacent_release_pair",
    "landed_source",
    "staging_record",
)

_LANDED_STAGING_PROMOTION_ADJACENCY_OWNERS: frozenset[str] = frozenset(
    {
        "base_admitted_registry_records",
        "stg_registry_records",
        "int_promoted_releases",
        "int_promoted_date_spine",
        "int_adjacent_release_pairs",
    }
)

#: Analytical assignments that must never appear in a Python source file --
#: every one of these is exclusively a dbt SQL calculation (D-02, T-04-06A).
_FORBIDDEN_PYTHON_ANALYTICAL_ASSIGNMENTS: tuple[str, ...] = (
    "transition_class =",
    "spell_number =",
    "is_left_censored =",
    "is_right_censored =",
    "is_lost_to_observation =",
    "onset_left =",
    "onset_right =",
    "exit_left =",
    "exit_right =",
    "normalized_outcome =",
    "terminal_state =",
)

#: Numbers that belong exclusively to Phase 5's headline reconciliation/claim
#: contract (GATE-A-EVIDENCE.md). D-03 forbids Phase 4 from asserting them.
_FORBIDDEN_PHASE_5_NUMBERS: tuple[str, ...] = (
    "7,737",
    "557,067",
    "557,291",
    "557,211",
    "13,169",
    "13,071",
    "7,750",
)


def _all_model_sql_paths() -> tuple[Path, ...]:
    # Scoped to dbt/models/ only -- dbt/tests/*.sql holds singular test
    # files, never a model, and several of their own names legitimately
    # contain a foundational-owner-looking substring (e.g.
    # `assert_adjacent_release_pairs_positive`) purely because they assert
    # against that model; they must never be mistaken for a competing model.
    return tuple(
        sorted(
            path
            for path in (DBT_ROOT / "models").rglob("*.sql")
            if "target" not in path.parts and "dbt_packages" not in path.parts and "logs" not in path.parts
        )
    )


def _all_model_names() -> set[str]:
    return {path.stem for path in _all_model_sql_paths()}


def _model_path(model_name: str) -> Path:
    matches = [path for path in _all_model_sql_paths() if path.stem == model_name]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one model file named {model_name!r}, found {matches}")
    return matches[0]


def _model_sql(model_name: str) -> str:
    return _strip_sql_line_comments(_model_path(model_name).read_text(encoding="utf-8"))


def _all_model_yaml_paths() -> tuple[Path, ...]:
    return tuple(sorted((DBT_ROOT / "models").rglob("*.yml")))


def _model_yaml_block(model_name: str) -> str:
    """Return the full `- name: <model_name>` YAML block (including every
    deeper-indented line up to the next sibling key) from whichever
    committed model YAML file declares it. Hand-rolled, matching this
    project's stdlib-only test convention -- no `pyyaml` dependency."""

    name_pattern = re.compile(r"^(\s*)-\s*name:\s*" + re.escape(model_name) + r"\s*$")
    for yaml_path in _all_model_yaml_paths():
        lines = yaml_path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            match = name_pattern.match(line)
            if not match:
                continue
            indent = len(match.group(1))
            end = len(lines)
            for j in range(i + 1, len(lines)):
                candidate = lines[j]
                if not candidate.strip():
                    continue
                candidate_indent = len(candidate) - len(candidate.lstrip(" "))
                if candidate_indent <= indent:
                    end = j
                    break
            return "\n".join(lines[i:end])
    raise AssertionError(f"model {model_name!r} not found in any dbt/models/**/*.yml file")


def _model_description(model_name: str) -> str:
    """Extract one model's folded `description:` scalar from its YAML
    block, joined into a single space-separated string."""

    block_lines = _model_yaml_block(model_name).splitlines()
    for i, line in enumerate(block_lines):
        stripped = line.strip()
        desc_match = re.match(r"description:\s*(.*)$", stripped)
        if not desc_match:
            continue
        desc_indent = len(line) - len(line.lstrip(" "))
        parts = [desc_match.group(1).strip().lstrip(">|").strip()]
        for continuation in block_lines[i + 1 :]:
            if not continuation.strip():
                continue
            continuation_indent = len(continuation) - len(continuation.lstrip(" "))
            if continuation_indent <= desc_indent:
                break
            parts.append(continuation.strip())
        return " ".join(part for part in parts if part)
    raise AssertionError(f"model {model_name!r} has no `description:` key in its YAML block")


class GrainMapCompletenessTests(unittest.TestCase):
    """D-01: exactly ten unique authoritative grain owners; every helper is
    labeled; no second landed/staging/promotion/date-spine/adjacency owner
    appears anywhere in the DAG."""

    def test_exactly_ten_grains_map_to_ten_unique_existing_relations(self) -> None:
        self.assertEqual(len(REQUIRED_GRAIN_OWNERS), 10)
        owners = list(REQUIRED_GRAIN_OWNERS.values())
        self.assertEqual(len(set(owners)), 10, "grain owners must be ten unique relations")
        all_names = _all_model_names()
        for grain, owner in REQUIRED_GRAIN_OWNERS.items():
            self.assertIn(owner, all_names, f"grain {grain!r}'s owner {owner!r} does not exist as a model file")

    def test_helper_set_plus_owner_set_is_the_complete_model_universe(self) -> None:
        # Proves no undocumented thirteenth-or-more helper or eleventh owner
        # silently exists anywhere under dbt/models/.
        self.assertEqual(
            HELPER_MODEL_NAMES | set(REQUIRED_GRAIN_OWNERS.values()),
            _all_model_names(),
        )

    def test_no_second_landed_staging_promotion_adjacency_owner_exists(self) -> None:
        all_names = _all_model_names()
        for name in _LANDED_STAGING_PROMOTION_ADJACENCY_OWNERS:
            self.assertIn(name, all_names)
        for name in all_names:
            if name in _LANDED_STAGING_PROMOTION_ADJACENCY_OWNERS:
                continue
            for term in _FOUNDATIONAL_OWNER_TERMS:
                self.assertNotIn(
                    term,
                    name,
                    f"model {name!r} looks like a second owner for a Phase 3 foundational "
                    f"responsibility (D-01)",
                )

    def test_model_grains_doc_maps_each_grain_to_its_exact_owner_on_one_line(self) -> None:
        content = _read("docs/model-grains.md")
        for grain, owner in REQUIRED_GRAIN_OWNERS.items():
            grain_title = grain[0].upper() + grain[1:]
            expected_row = f"| {grain_title} | `{owner}` |"
            self.assertIn(
                expected_row,
                content,
                f"docs/model-grains.md must map grain {grain!r} to `{owner}` on one exact row",
            )

    def test_model_grains_doc_labels_every_helper(self) -> None:
        content = _read("docs/model-grains.md")
        for helper in HELPER_MODEL_NAMES:
            self.assertIn(f"`{helper}`", content, f"docs/model-grains.md must label helper {helper!r}")


class GrainStatementAndTestCoverageTests(unittest.TestCase):
    """D-19: every model receives a description, an explicit grain
    statement, and appropriate tests."""

    def test_base_admitted_registry_records_has_grain_prefix_and_generic_tests(self) -> None:
        description = _model_description("base_admitted_registry_records")
        self.assertTrue(
            description.startswith("Grain:"),
            f"base_admitted_registry_records's description must start with 'Grain:': {description[:80]!r}",
        )
        block = _model_yaml_block("base_admitted_registry_records")
        self.assertIn("tests:", block)
        self.assertIn("not_null", block)

    def test_every_new_phase_4_model_description_starts_with_grain(self) -> None:
        for model_name in _NEW_PHASE_4_MODEL_NAMES:
            description = _model_description(model_name)
            self.assertTrue(
                description.startswith("Grain:"),
                f"{model_name}'s description must start with 'Grain:' (D-19): {description[:80]!r}",
            )

    def test_every_new_phase_4_model_has_at_least_one_declared_test(self) -> None:
        singular_test_paths = sorted((DBT_ROOT / "tests").glob("*.sql"))
        singular_test_content = "\n".join(
            _strip_sql_line_comments(path.read_text(encoding="utf-8")) for path in singular_test_paths
        )
        for model_name in _NEW_PHASE_4_MODEL_NAMES:
            block = _model_yaml_block(model_name)
            has_generic_test = "tests:" in block
            has_singular_test = f"ref('{model_name}')" in singular_test_content
            self.assertTrue(
                has_generic_test or has_singular_test,
                f"{model_name} must carry either a generic YAML test or a singular SQL test (D-19)",
            )


class RepresentativeModelTests(unittest.TestCase):
    """D-21: exactly `int_entity_transitions`, `int_delinquency_spells`, and
    `fct_public_status_observations` are marked for later annotated
    README/walkthrough treatment -- no fourth model is ever added."""

    def test_representative_models_section_names_exactly_the_three_d21_models(self) -> None:
        content = _read("docs/model-grains.md")
        start = content.index("## Representative models (D-21)")
        section = content[start:]
        all_model_names = HELPER_MODEL_NAMES | set(REQUIRED_GRAIN_OWNERS.values())
        mentioned = {name for name in all_model_names if f"`{name}`" in section}
        self.assertEqual(mentioned, REPRESENTATIVE_MODELS)

    def test_representative_model_yaml_descriptions_say_representative_model(self) -> None:
        for model_name in REPRESENTATIVE_MODELS:
            description = _model_description(model_name)
            self.assertIn("representative model", description.lower())


class Phase5BoundaryTests(unittest.TestCase):
    """D-03: the doc states the downstream Phase 5 non-ownership boundary
    and never leaks a Phase 5 headline number the fixture cannot support."""

    def test_doc_states_the_phase_5_non_ownership_boundary(self) -> None:
        content = _read("docs/model-grains.md").lower()
        for phrase in ("cohort persistence", "last renewal", "reconciliation", "confidence interval", "claim"):
            self.assertIn(phrase, content, f"docs/model-grains.md must state the Phase 5 boundary term {phrase!r}")

    def test_doc_never_leaks_a_phase_5_headline_number(self) -> None:
        content = _read("docs/model-grains.md")
        for forbidden_number in _FORBIDDEN_PHASE_5_NUMBERS:
            self.assertNotIn(forbidden_number, content)


class LineageEdgeTests(unittest.TestCase):
    """D-01/D-20: the documented Phase 3 -> Phase 4 -> safe-output lineage
    is machine-checkable directly from committed `ref()` calls, and the
    three safe-output models are explicit positive projections."""

    def test_lineage_contains_every_required_edge(self) -> None:
        for upstream, downstream in _REQUIRED_LINEAGE_EDGES:
            sql = _model_sql(downstream)
            self.assertIn(
                f"ref('{upstream}')",
                sql,
                f"expected {downstream}.sql to ref('{upstream}') per the documented lineage",
            )

    def test_safe_output_models_live_under_marts_and_never_select_star(self) -> None:
        for model_name in SAFE_OUTPUT_MODELS:
            path = _model_path(model_name)
            self.assertIn("marts", path.parts, f"{model_name} must live under dbt/models/marts/")
            sql = _model_sql(model_name).lower()
            self.assertNotRegex(sql, r"select\s*\*")


class SqlTechniqueTests(unittest.TestCase):
    """REQ-sql-techniques: all seven named techniques are found in the
    correct representative/upstream models, at their natural problems --
    never as decorative complexity."""

    def test_technique_staged_reusable_ctes(self) -> None:
        for model_name in ("int_entity_transitions", "int_delinquency_spells", "int_entity_observation_sequence"):
            sql = _model_sql(model_name)
            cte_stage_count = len(re.findall(r"\bas\s*\(", sql, re.IGNORECASE))
            self.assertGreaterEqual(
                cte_stage_count, 2, f"{model_name} must expose multiple staged reusable CTEs"
            )

    def test_technique_lag_lead_and_cumulative_window(self) -> None:
        sequence_sql = _model_sql("int_entity_observation_sequence").lower()
        self.assertIn("lag(", sequence_sql)
        self.assertIn("lead(", sequence_sql)
        spells_sql = _model_sql("int_delinquency_spells").lower()
        self.assertIn("sum(", spells_sql)
        self.assertIn("rows between unbounded preceding and current row", spells_sql)

    def test_technique_phase_3_deterministic_promotion_is_reused_not_reimplemented(self) -> None:
        promotion_sql = _model_sql("int_promoted_releases").lower()
        self.assertIn("row_number()", promotion_sql)
        signature = "partition by r.as_of_date"
        for model_name in HELPER_MODEL_NAMES | set(REQUIRED_GRAIN_OWNERS.values()):
            if model_name == "int_promoted_releases":
                continue
            sql = _model_sql(model_name).lower()
            self.assertNotIn(
                signature, sql, f"{model_name} must not reimplement Phase 3's promotion ranking"
            )

    def test_technique_exact_key_adjacent_joins(self) -> None:
        sql = _model_sql("int_entity_transitions").lower()
        self.assertIn("inner join", sql)
        self.assertIn("pairs.from_as_of_date", sql)
        self.assertIn("pairs.to_as_of_date", sql)

    def test_technique_anti_joins(self) -> None:
        sql = _model_sql("int_entity_transitions").lower()
        self.assertIn("anti join", sql)

    def test_technique_conditional_aggregation_and_transition_matrix(self) -> None:
        matrix_sql = _model_sql("int_transition_matrix").lower()
        self.assertIn("group by", matrix_sql)
        self.assertIn("count(*)", matrix_sql)
        flags_sql = _model_sql("int_release_flags").lower()
        self.assertIn("count(*) filter", flags_sql)

    def test_technique_gaps_and_islands(self) -> None:
        sql = _model_sql("int_delinquency_spells").lower()
        self.assertIn("is_island_start", sql)
        self.assertIn("spell_number", sql)


class NoPythonAnalyticsTests(unittest.TestCase):
    """D-02/T-04-06A: Python remains structural preflight/runner/docs
    orchestration only -- no transition, spell, capture-outcome, or flag
    classification is ever computed in Python."""

    def test_no_python_source_computes_a_transition_spell_or_flag_analytical_value(self) -> None:
        python_dirs = (REPO_ROOT / "calico_dbt", REPO_ROOT / "calico_landing")
        for directory in python_dirs:
            for path in directory.rglob("*.py"):
                content = path.read_text(encoding="utf-8")
                for forbidden in _FORBIDDEN_PYTHON_ANALYTICAL_ASSIGNMENTS:
                    self.assertNotIn(
                        forbidden,
                        content,
                        f"{path.relative_to(REPO_ROOT)} must not compute the analytical value "
                        f"{forbidden!r} in Python (D-02)",
                    )


if __name__ == "__main__":
    unittest.main()
