{{
    config(
        materialized='view'
    )
}}

-- Authoritative tall, auditable release/pair rule-result grain (D-14).
-- Grain: one row per named/versioned rule plus the release or release-pair
-- scope it evaluates. Every rule union branch below projects the identical
-- explicit eighteen-column shape so `union all` never silently coerces or
-- drops a column; `scope` names which of the two identity shapes
-- (`as_of_date`/`release_revision`/`revision_fingerprint` for `'release'`,
-- or the `pair_from_*`/`pair_to_*` columns for `'release_pair'`) is
-- populated for a given row -- the other identity always stays null on
-- that row (`assert_release_flag_grain.sql` proves the two shapes never
-- overlap).
--
-- `rule_class` visibly separates `'deterministic'` rules from
-- `'heuristic_review'` rules; a heuristic row's `result` is a review
-- signal only -- `not_applicable`, `pass`, or `review` -- never an
-- anomaly, risk, quality score, ranking, recommendation, or admission
-- decision (T-04-04E). `supersedes_rule_id`/`supersedes_rule_version` stay
-- nullable so a later additive rule correction can point back at what it
-- replaces without ever silently rewriting a prior rule version in place
-- (D-022 project-wide no-silent-rewrite convention).

with parser_contract_version_known_v1 as (

    select
        'parser_contract_version_known_v1' as rule_id,
        1 as rule_version,
        'deterministic' as rule_class,
        'release' as scope,
        as_of_date,
        release_revision,
        revision_fingerprint,
        cast(null as varchar) as pair_from_as_of_date,
        cast(null as bigint) as pair_from_release_revision,
        cast(null as varchar) as pair_from_revision_fingerprint,
        cast(null as varchar) as pair_to_as_of_date,
        cast(null as bigint) as pair_to_release_revision,
        cast(null as varchar) as pair_to_revision_fingerprint,
        cast(parser_contract_version as varchar) as observed_value,
        'known-parser-contract-version-1-v1' as parameter_version,
        case
            when parser_contract_version = 1 then 'pass'
            else 'review'
        end as result,
        cast(null as varchar) as supersedes_rule_id,
        cast(null as bigint) as supersedes_rule_version

    from {{ ref('int_promoted_releases') }}

),

capture_run_link_counts as (

    select
        as_of_date,
        release_revision,
        revision_fingerprint,
        count(*) filter (where normalized_outcome in ('accepted', 'recovered')) as linked_capture_run_count
    from {{ ref('int_capture_runs') }}
    where as_of_date is not null
      and release_revision is not null
      and revision_fingerprint is not null
    group by as_of_date, release_revision, revision_fingerprint

),

capture_outcome_available_v1 as (

    select
        'capture_outcome_available_v1' as rule_id,
        1 as rule_version,
        'deterministic' as rule_class,
        'release' as scope,
        promoted.as_of_date,
        promoted.release_revision,
        promoted.revision_fingerprint,
        cast(null as varchar) as pair_from_as_of_date,
        cast(null as bigint) as pair_from_release_revision,
        cast(null as varchar) as pair_from_revision_fingerprint,
        cast(null as varchar) as pair_to_as_of_date,
        cast(null as bigint) as pair_to_release_revision,
        cast(null as varchar) as pair_to_revision_fingerprint,
        cast(coalesce(linked.linked_capture_run_count, 0) as varchar) as observed_value,
        'linked-accepted-or-recovered-capture-run-v1' as parameter_version,
        case
            when coalesce(linked.linked_capture_run_count, 0) > 0 then 'pass'
            else 'review'
        end as result,
        cast(null as varchar) as supersedes_rule_id,
        cast(null as bigint) as supersedes_rule_version

    from {{ ref('int_promoted_releases') }} as promoted
    left join capture_run_link_counts as linked
        on promoted.as_of_date = linked.as_of_date
       and promoted.release_revision = linked.release_revision
       and promoted.revision_fingerprint = linked.revision_fingerprint

),

keyed_counts_by_release as (

    -- Reuses Phase 3's own disposition-first `eligible_for_keyed_path`
    -- predicate directly (never a second filter, and never the parallel
    -- `int_keyed_snapshots` view another Wave 2 plan owns) so this plan's
    -- own `depends_on` boundary never grows an undeclared cross-plan SQL
    -- dependency.
    select
        as_of_date,
        release_revision,
        revision_fingerprint,
        count(*) as keyed_count
    from {{ ref('int_registry_record_dispositions') }}
    where disposition = 'eligible_for_keyed_path'
    group by as_of_date, release_revision, revision_fingerprint

),

keyed_coverage_change_fraction_v1 as (

    select
        'keyed_coverage_change_fraction_v1' as rule_id,
        1 as rule_version,
        'heuristic_review' as rule_class,
        'release_pair' as scope,
        cast(null as varchar) as as_of_date,
        cast(null as bigint) as release_revision,
        cast(null as varchar) as revision_fingerprint,
        pairs.from_as_of_date as pair_from_as_of_date,
        pairs.from_release_revision as pair_from_release_revision,
        pairs.from_revision_fingerprint as pair_from_revision_fingerprint,
        pairs.to_as_of_date as pair_to_as_of_date,
        pairs.to_release_revision as pair_to_release_revision,
        pairs.to_revision_fingerprint as pair_to_revision_fingerprint,
        cast(
            case
                when coalesce(start_counts.keyed_count, 0) = 0 then null
                else abs(coalesce(end_counts.keyed_count, 0) - start_counts.keyed_count)::double
                     / start_counts.keyed_count
            end as varchar
        ) as observed_value,
        'absolute-relative-change-0.10-v1' as parameter_version,
        case
            when coalesce(start_counts.keyed_count, 0) = 0 then 'not_applicable'
            when abs(coalesce(end_counts.keyed_count, 0) - start_counts.keyed_count)::double
                 / start_counts.keyed_count >= 0.10
                then 'review'
            else 'pass'
        end as result,
        cast(null as varchar) as supersedes_rule_id,
        cast(null as bigint) as supersedes_rule_version

    from {{ ref('int_adjacent_release_pairs') }} as pairs
    left join keyed_counts_by_release as start_counts
        on pairs.from_as_of_date = start_counts.as_of_date
       and pairs.from_release_revision = start_counts.release_revision
       and pairs.from_revision_fingerprint = start_counts.revision_fingerprint
    left join keyed_counts_by_release as end_counts
        on pairs.to_as_of_date = end_counts.as_of_date
       and pairs.to_release_revision = end_counts.release_revision
       and pairs.to_revision_fingerprint = end_counts.revision_fingerprint

)

select * from parser_contract_version_known_v1
union all
select * from capture_outcome_available_v1
union all
select * from keyed_coverage_change_fraction_v1
