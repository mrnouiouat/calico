{{ config(materialized='view') }}

-- Grain: one row per exact adjacent accepted-release pair and diagnostic measure.
-- Source-reported Last Renewal presence is a release-quality diagnostic only. It
-- neither defines an observed exit nor supplies a threshold, score, or event date.
with pair_counts as (

    select
        from_as_of_date,
        from_release_revision,
        from_revision_fingerprint,
        to_as_of_date,
        to_release_revision,
        to_revision_fingerprint,
        gap_days,
        count(*) filter (
            where observed_at_start
              and observed_at_end
              and start_is_delinquent
              and (start_source_reported_last_renewal_date is not null
                   or start_source_reported_last_renewal_date_nonblank_unparseable)
              and end_source_reported_last_renewal_date is null
              and not coalesce(end_source_reported_last_renewal_date_nonblank_unparseable, false)
        ) as starting_delinquent_clear_count,
        count(*) filter (
            where transition_class = 'delinquency_exit_observed'
              and (start_source_reported_last_renewal_date is not null
                   or start_source_reported_last_renewal_date_nonblank_unparseable)
              and end_source_reported_last_renewal_date is null
              and not coalesce(end_source_reported_last_renewal_date_nonblank_unparseable, false)
        ) as observed_exit_clear_count,
        count(*) filter (
            where transition_class = 'delinquency_exit_observed'
              and (start_source_reported_last_renewal_date is not null
                   or start_source_reported_last_renewal_date_nonblank_unparseable)
        ) as eligible_observed_exit_count,
        count(*) filter (
            where transition_class = 'delinquency_exit_observed'
        ) as all_observed_exit_count
    from {{ ref('int_entity_transitions') }}
    group by 1, 2, 3, 4, 5, 6, 7

),

measures as (

    select *, 'conditional_precision' as measure_name,
        'starting_delinquent_diagnostic_clears_v1' as denominator_definition_id,
        starting_delinquent_clear_count as diagnostic_eligible_count,
        observed_exit_clear_count as numerator_count,
        starting_delinquent_clear_count as denominator_count
    from pair_counts
    union all
    select *, 'eligible_exit_sensitivity',
        'observed_exits_with_populated_start_v1',
        eligible_observed_exit_count,
        observed_exit_clear_count,
        eligible_observed_exit_count
    from pair_counts
    union all
    select *, 'all_exit_sensitivity',
        'all_observed_exits_v1',
        all_observed_exit_count,
        observed_exit_clear_count,
        all_observed_exit_count
    from pair_counts

)

select
    from_as_of_date, from_release_revision, from_revision_fingerprint,
    to_as_of_date, to_release_revision, to_revision_fingerprint, gap_days,
    'release_quality_diagnostic' as diagnostic_role,
    measure_name,
    denominator_definition_id,
    diagnostic_eligible_count,
    numerator_count,
    denominator_count,
    case when denominator_count = 0 then null
         else numerator_count::double / denominator_count end as measure_value,
    {{ wilson_interval('numerator_count', 'denominator_count', 'lower') }} as wilson_95_lower,
    {{ wilson_interval('numerator_count', 'denominator_count', 'upper') }} as wilson_95_upper
from measures
