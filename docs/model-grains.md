# Model Grains (Phase 4, Gate B)

Authoritative map from each of the ten required grains (`REQ-model-grains`) to its one owning dbt
relation. Every downstream reader -- README, walkthrough, and reviewer -- treats this table as the
single source of truth for "which relation is the grain." No other relation in this project is ever
a competing owner for one of these ten responsibilities (D-01).

## The ten grains

| Grain | Owning relation |
|---|---|
| Landed source record | `base_admitted_registry_records` |
| Keyed snapshot | `int_keyed_snapshots` |
| Unkeyed coverage | `int_unkeyed_coverage` |
| Transition | `int_entity_transitions` |
| Status spell | `int_delinquency_spells` |
| Capture run | `int_capture_runs` |
| Release flag | `int_release_flags` |
| Aggregate report mart | `mart_registry_population_coverage` |
| Public organization | `dim_public_organizations` |
| Public status observation | `fct_public_status_observations` |

`base_admitted_registry_records` is reused unchanged from Phase 3 (D-01): this plan added only its
`Grain:` description and its generic identity/provenance tests, never a second landed-record model.
The other nine are new Phase 4 owners, built in dependency order across Plans 02-05.

## One-line grain contracts

- **Landed source record** -- one source row per source object, across every admitted revision.
- **Keyed snapshot** -- one exact, full, nonblank registration number per promoted accepted release.
- **Unkeyed coverage** -- counts of rows without a registration number, by release/list/status.
- **Transition** -- one exact key per adjacent accepted-release pair, with a closed observation-aware
  classification.
- **Status spell** -- one contiguous *observed* delinquency spell, with observation-bounded onset/exit
  and left/right/lost censoring flags.
- **Capture run** -- one durable capture attempt, with a normalized outcome and (v2-only) timing.
- **Release flag** -- one named/versioned rule result per release or release pair, deterministic or
  labeled heuristic.
- **Aggregate report mart** -- one bounded, low-cardinality release/list/status/coverage baseline, with
  no organization-level dimension.
- **Public organization** -- one row per eligible exact registration key, with deterministic latest
  display attributes.
- **Public status observation** -- one row per eligible key and promoted release, with an explicit
  `not_observed` state rather than a silently missing row.

## Helper and audit relations (not grain owners)

These relations exist to build or audit a grain above; none of them is ever a second owner of one of
the ten responsibilities (D-01).

**Delivered by Phase 3, extended with tests/docs only, never rewritten by this plan:**

| Relation | Role |
|---|---|
| `stg_registry_records` | Universally trimmed, honestly named staging over the landed grain. |
| `int_promoted_releases` | Pointer-authoritative/highest-revision promotion per `as_of_date`. |
| `int_promoted_registry_records` | Promoted staging rows joined on the full release key. |
| `int_promoted_date_spine` | One row per promoted date, named for `int_adjacent_release_pairs` to `lead()` over. |
| `int_adjacent_release_pairs` | One row per adjacent distinct promoted-date pair, with `gap_days`. |
| `int_registry_record_dispositions` | Total per-promoted-record disposition: keyed, unkeyed, or blank-status excluded. |
| `int_keyless_registry_coverage` | Row-level blank-registration audit detail behind the unkeyed coverage grain. |
| `int_registry_record_exclusions` | Row-level blank-status-typed-path-exclusion audit detail. |
| `int_revision_catalog` | Audit relation over every admitted revision, not only the promoted one. |

**New in Phase 4, owned by this plan's own machine-checked lineage/technique contract:**

| Relation | Role |
|---|---|
| `int_transition_matrix` | Internal Phase 5 integration helper: conditional aggregation over transitions. Asserts no real-data total or claim. |
| `int_entity_observation_sequence` | Ordered-observation helper feeding the spell grain: global ordinal plus per-key `lag`/`lead`. |
| `stg_capture_attempts` | Structural pass-through of the fixed `runtime_input.capture_attempts` relation. |
| `int_public_organization_eligibility` | Private audit helper: normalizes every exact key to `eligible`/`ambiguous_natural_person`/`unclassified`. |

## Lineage

```text
base_admitted_registry_records (landed source record)
  -> stg_registry_records
  -> int_promoted_releases -> int_promoted_registry_records
  -> int_promoted_date_spine -> int_adjacent_release_pairs
  -> int_registry_record_dispositions
       +-> int_keyed_snapshots (keyed snapshot)
       |     +-> int_unkeyed_coverage (unkeyed coverage, via int_keyless_registry_coverage)
       |     +-> int_entity_transitions (transition)
       |     |     +-> int_transition_matrix                     [-> Phase 5]
       |     +-> int_entity_observation_sequence
       |     |     +-> int_delinquency_spells (status spell)     [-> Phase 5]
       |     +-> int_public_organization_eligibility
       |           +-> dim_public_organizations (public organization)          [safe output]
       |           +-> fct_public_status_observations (public status observation) [safe output]
       +-> mart_registry_population_coverage (aggregate report mart)           [safe output]

stg_capture_attempts (from runtime_input.capture_attempts)
  -> int_capture_runs (capture run)
       +-> int_release_flags (release flag)                      [-> Phase 5]
int_promoted_releases -----------------------------------------> int_release_flags
int_adjacent_release_pairs ------------------------------------> int_release_flags
```

A reviewer can follow one path from admitted source rows through promotion, exact-key transitions,
observation-bounded spells, and the two safe-output families (the aggregate mart and the public
organization/history pair) without consulting a Python calculation anywhere in the chain (D-02).

## Phase 5 integration points and non-ownership

Phase 4 produces metric-ready atomic facts and a bounded baseline aggregate, not the final v1 metric
contract (D-03). The following remain fully owned by Phase 5, and Phase 4 exposes atomic facts for
them without pre-empting their logic:

- Starting-cohort persistence.
- The three `Last Renewal` diagnostic measures (conditional precision, eligible-exit sensitivity,
  all-exit sensitivity).
- Headline reconciliation against `GATE-A-EVIDENCE.md`.
- Interval proportions and their confidence intervals.
- The approved lead-finding claim and its prohibited-claims tests.

`int_transition_matrix` is Phase 4's own internal Phase 5 integration helper: it demonstrates
conditional aggregation and a transition matrix but asserts no real-data total, reconciliation figure,
or claim -- that boundary is exactly what keeps this plan from pre-empting Phase 5.

## Representative models (D-21)

Three models are marked for later annotated README/walkthrough treatment:

- `int_entity_transitions` -- exact-key adjacent-release joins and not-observed classification.
- `int_delinquency_spells` -- windows and gaps-and-islands censoring logic.
- `fct_public_status_observations` -- the bounded named-publication projection.

No other model is a representative for that later treatment.
