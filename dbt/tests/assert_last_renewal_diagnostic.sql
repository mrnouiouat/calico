-- Independent recomputation: zero rows pass. Failure output is identity-free.
with source_counts as (
    select
        from_as_of_date, from_release_revision, from_revision_fingerprint,
        to_as_of_date, to_release_revision, to_revision_fingerprint,
        count(*) filter (where observed_at_start and observed_at_end and start_is_delinquent
            and (start_source_reported_last_renewal_date is not null or start_source_reported_last_renewal_date_nonblank_unparseable)
            and end_source_reported_last_renewal_date is null
            and not coalesce(end_source_reported_last_renewal_date_nonblank_unparseable, false)) as clear_count,
        count(*) filter (where transition_class = 'delinquency_exit_observed'
            and (start_source_reported_last_renewal_date is not null or start_source_reported_last_renewal_date_nonblank_unparseable)
            and end_source_reported_last_renewal_date is null
            and not coalesce(end_source_reported_last_renewal_date_nonblank_unparseable, false)) as exit_clear_count,
        count(*) filter (where transition_class = 'delinquency_exit_observed'
            and (start_source_reported_last_renewal_date is not null or start_source_reported_last_renewal_date_nonblank_unparseable)) as eligible_exit_count,
        count(*) filter (where transition_class = 'delinquency_exit_observed') as all_exit_count
    from {{ ref('int_entity_transitions') }} group by 1,2,3,4,5,6
), expected as (
    select *, 'conditional_precision' measure_name, exit_clear_count numerator_count, clear_count denominator_count from source_counts
    union all select *, 'eligible_exit_sensitivity', exit_clear_count, eligible_exit_count from source_counts
    union all select *, 'all_exit_sensitivity', exit_clear_count, all_exit_count from source_counts
), actual as (
    select * from {{ ref('mart_last_renewal_diagnostic') }}
)
select 'last_renewal_diagnostic_mismatch' as failure_reason
from expected e full outer join actual a
  on e.from_as_of_date = a.from_as_of_date and e.from_release_revision = a.from_release_revision
 and e.from_revision_fingerprint = a.from_revision_fingerprint and e.to_as_of_date = a.to_as_of_date
 and e.to_release_revision = a.to_release_revision and e.to_revision_fingerprint = a.to_revision_fingerprint
 and e.measure_name = a.measure_name
where e.measure_name is null or a.measure_name is null
   or e.numerator_count <> a.numerator_count or e.denominator_count <> a.denominator_count
   or a.diagnostic_role <> 'release_quality_diagnostic'
