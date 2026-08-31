-- Singular test: succeeds only when it returns zero rows.
--
-- Independently reconciles Plan 02's owned keyed/unkeyed relations against
-- Phase 3's delivered disposition and promoted-record relations (D-04),
-- without ever trusting `int_keyed_snapshots`/`int_unkeyed_coverage`'s own
-- selection to have stayed faithful to the disposition-first predicate:
--
-- 1. Keyed-vs-eligible row identity: every `eligible_for_keyed_path` row
--    identity (full release key + source_list + source_line_no) appears
--    in `int_keyed_snapshots` exactly once -- never zero times (a silently
--    dropped eligible row) and never more than once.
-- 2. Duplicate detection: no (full release key + exact registration key)
--    combination appears more than once in `int_keyed_snapshots` -- the
--    declared grain.
-- 3. Blank-key defense: no `int_keyed_snapshots` row ever carries a null
--    registration key.
-- 4. Aggregated unkeyed reconciliation: `int_unkeyed_coverage`'s grouped
--    counts equal an independent `group by` recomputation over the
--    row-level `int_keyless_registry_coverage` relation, at the declared
--    (release identity, source list, source-reported status) grain.
-- 5. Global three-way reconciliation: `eligible_for_keyed_path` count
--    + `untracked_blank_registration` count + every
--    `excluded_from_typed_path_*` count equals the total promoted record
--    count.
-- 6. Partition-level three-way reconciliation: the same equality holds
--    independently for every (as_of_date, release_revision,
--    revision_fingerprint, source_list, source_reported_registry_status)
--    partition, so a compensating error in one partition can never hide
--    inside a matching global total.
--
-- Every failure surfaces only a safe source_list/source_line_no/date
-- identifier or a fixed category -- never an excluded source value, a raw
-- row, or a registration number.

with promoted_identity as (

    select
        as_of_date, release_revision, revision_fingerprint,
        source_list, source_line_no, source_reported_registry_status
    from {{ ref('int_promoted_registry_records') }}

),

eligible_identity as (

    select
        as_of_date, release_revision, revision_fingerprint,
        source_list, source_line_no, source_reported_registry_status
    from {{ ref('int_registry_record_dispositions') }}
    where disposition = 'eligible_for_keyed_path'

),

keyed_identity as (

    select
        as_of_date, release_revision, revision_fingerprint,
        source_list, source_line_no, state_charity_registration_number
    from {{ ref('int_keyed_snapshots') }}

),

keyed_vs_eligible_failures as (

    select
        'keyed_snapshot_eligible_mismatch:'
            || coalesce(eligible.source_list, keyed.source_list)
            || ':' || coalesce(eligible.source_line_no, keyed.source_line_no) as failure_reason
    from eligible_identity as eligible
    full outer join keyed_identity as keyed
        on eligible.as_of_date = keyed.as_of_date
       and eligible.release_revision = keyed.release_revision
       and eligible.revision_fingerprint = keyed.revision_fingerprint
       and eligible.source_list = keyed.source_list
       and eligible.source_line_no = keyed.source_line_no
    where eligible.source_list is null
       or keyed.source_list is null

),

keyed_duplicate_key_failures as (

    select
        'duplicate_keyed_snapshot_key:' || as_of_date
            || ':' || coalesce(state_charity_registration_number, '') as failure_reason
    from keyed_identity
    group by as_of_date, release_revision, revision_fingerprint, state_charity_registration_number
    having count(*) > 1

),

keyed_blank_key_failures as (

    select 'keyed_snapshot_blank_key:' || as_of_date as failure_reason
    from keyed_identity
    where state_charity_registration_number is null

),

keyless_grouped as (

    select
        as_of_date, release_revision, revision_fingerprint,
        source_list, source_reported_registry_status,
        count(*) as keyless_count
    from {{ ref('int_keyless_registry_coverage') }}
    group by as_of_date, release_revision, revision_fingerprint, source_list, source_reported_registry_status

),

unkeyed_coverage_rows as (

    select
        as_of_date, release_revision, revision_fingerprint,
        source_list, source_reported_registry_status, record_count
    from {{ ref('int_unkeyed_coverage') }}

),

unkeyed_coverage_failures as (

    select
        'unkeyed_coverage_count_mismatch:'
            || coalesce(grouped.as_of_date, coverage.as_of_date)
            || ':' || coalesce(grouped.source_list, coverage.source_list) as failure_reason
    from keyless_grouped as grouped
    full outer join unkeyed_coverage_rows as coverage
        on grouped.as_of_date = coverage.as_of_date
       and grouped.release_revision = coverage.release_revision
       and grouped.revision_fingerprint = coverage.revision_fingerprint
       and grouped.source_list = coverage.source_list
       and grouped.source_reported_registry_status is not distinct from coverage.source_reported_registry_status
    where coalesce(grouped.keyless_count, 0) <> coalesce(coverage.record_count, 0)

),

global_count_failures as (

    select 'global_three_way_count_mismatch' as failure_reason
    where (select count(*) from promoted_identity) <> (
        (select count(*) from eligible_identity)
        + (select count(*) from {{ ref('int_keyless_registry_coverage') }})
        + (select count(*) from {{ ref('int_registry_record_exclusions') }})
    )

),

promoted_partition_counts as (

    select
        as_of_date, release_revision, revision_fingerprint,
        source_list, source_reported_registry_status,
        count(*) as promoted_count
    from promoted_identity
    group by as_of_date, release_revision, revision_fingerprint, source_list, source_reported_registry_status

),

partition_three_way_counts as (

    select
        as_of_date, release_revision, revision_fingerprint,
        source_list, source_reported_registry_status,
        count(*) filter (where disposition = 'eligible_for_keyed_path') as eligible_count,
        count(*) filter (where disposition = 'untracked_blank_registration') as untracked_count,
        count(*) filter (where disposition like 'excluded_from_typed_path_%') as excluded_count
    from {{ ref('int_registry_record_dispositions') }}
    group by as_of_date, release_revision, revision_fingerprint, source_list, source_reported_registry_status

),

partition_count_failures as (

    select
        'partition_three_way_count_mismatch:'
            || coalesce(promoted_counts.as_of_date::text, three_way_counts.as_of_date::text)
            || ':' || coalesce(promoted_counts.source_list, three_way_counts.source_list) as failure_reason
    from promoted_partition_counts as promoted_counts
    full outer join partition_three_way_counts as three_way_counts
        on promoted_counts.as_of_date = three_way_counts.as_of_date
       and promoted_counts.release_revision = three_way_counts.release_revision
       and promoted_counts.revision_fingerprint = three_way_counts.revision_fingerprint
       and promoted_counts.source_list = three_way_counts.source_list
       and promoted_counts.source_reported_registry_status is not distinct from three_way_counts.source_reported_registry_status
    where coalesce(promoted_counts.promoted_count, 0) <> (
        coalesce(three_way_counts.eligible_count, 0)
        + coalesce(three_way_counts.untracked_count, 0)
        + coalesce(three_way_counts.excluded_count, 0)
    )

)

select failure_reason from keyed_vs_eligible_failures
union all
select failure_reason from keyed_duplicate_key_failures
union all
select failure_reason from keyed_blank_key_failures
union all
select failure_reason from unkeyed_coverage_failures
union all
select failure_reason from global_count_failures
union all
select failure_reason from partition_count_failures
