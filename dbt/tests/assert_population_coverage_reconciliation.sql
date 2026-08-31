-- Singular test: succeeds only when it returns zero rows.
--
-- Independently reconciles mart_registry_population_coverage against
-- int_registry_record_dispositions (D-15), without ever trusting the
-- mart's own `group by`/`case` to have covered every row:
--
-- 1. Grain uniqueness: no (as_of_date, release_revision,
--    revision_fingerprint, source_list, source_reported_registry_status,
--    coverage_class) combination appears more than once in the mart.
-- 2. Per-group count reconciliation: independently re-grouping
--    int_registry_record_dispositions by the identical dimension set and
--    the identical keyed/unkeyed coverage_class mapping produces the exact
--    same record_count the mart reports for every group -- no group is
--    silently dropped, duplicated, or miscounted.
-- 3. Global count reconciliation: the mart's total sum(record_count)
--    equals int_registry_record_dispositions's total row count, which in
--    turn equals int_promoted_registry_records's total row count
--    (transitively proving reconciliation all the way back to promoted
--    rows, mirroring assert_registry_record_disposition_reconciliation.sql).
-- 4. No organization-level drillthrough: this singular test's own recomputed
--    grouping never references state_charity_registration_number,
--    source_reported_organization_name, or source_reported_city, and the
--    mart itself is proven schema-closed by
--    tests/dbt_longitudinal/test_public_models.py's static SQL-shape check
--    -- this singular test focuses on reconciliation, not schema shape.
--
-- Every failure surfaces only a safe as_of_date/source_list/coverage_class
-- identifier -- never an excluded source value or a full raw row.

with recomputed_groups as (

    select
        as_of_date,
        release_revision,
        revision_fingerprint,
        source_list,
        source_reported_registry_status,
        case
            when disposition = 'eligible_for_keyed_path' then 'keyed'
            else 'unkeyed'
        end as coverage_class,
        count(*) as expected_record_count
    from {{ ref('int_registry_record_dispositions') }}
    group by
        as_of_date,
        release_revision,
        revision_fingerprint,
        source_list,
        source_reported_registry_status,
        case
            when disposition = 'eligible_for_keyed_path' then 'keyed'
            else 'unkeyed'
        end

),

mart_groups as (

    select
        as_of_date,
        release_revision,
        revision_fingerprint,
        source_list,
        source_reported_registry_status,
        coverage_class,
        record_count
    from {{ ref('mart_registry_population_coverage') }}

),

duplicate_grain_failures as (

    select
        'duplicate_grain:'
            || as_of_date || ':' || source_list || ':' || coverage_class as failure_reason
    from mart_groups
    group by as_of_date, release_revision, revision_fingerprint, source_list,
             source_reported_registry_status, coverage_class
    having count(*) > 1

),

per_group_count_failures as (

    select
        'per_group_count_mismatch:'
            || coalesce(recomputed.as_of_date, mart.as_of_date)
            || ':' || coalesce(recomputed.source_list, mart.source_list)
            || ':' || coalesce(recomputed.coverage_class, mart.coverage_class) as failure_reason
    from recomputed_groups as recomputed
    full outer join mart_groups as mart
        on recomputed.as_of_date = mart.as_of_date
       and recomputed.release_revision = mart.release_revision
       and recomputed.revision_fingerprint = mart.revision_fingerprint
       and recomputed.source_list = mart.source_list
       and recomputed.source_reported_registry_status is not distinct from mart.source_reported_registry_status
       and recomputed.coverage_class = mart.coverage_class
    where coalesce(recomputed.expected_record_count, -1) <> coalesce(mart.record_count, -1)

),

global_count_failures as (

    select 'global_count_mismatch' as failure_reason
    where (select count(*) from {{ ref('int_registry_record_dispositions') }})
       <> (select coalesce(sum(record_count), 0) from mart_groups)

)

select failure_reason from duplicate_grain_failures
union all
select failure_reason from per_group_count_failures
union all
select failure_reason from global_count_failures
