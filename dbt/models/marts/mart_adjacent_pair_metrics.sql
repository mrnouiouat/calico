{{
    config(
        materialized='view'
    )
}}

-- One identity-free row per exact adjacent accepted-release pair. All
-- analytical arithmetic remains in SQL. `__NULL_STATUS__` is the fixed
-- sort token used only to make nullable status ties deterministic; the
-- projected status values retain their original nulls.

with pair_counts as (

    select
        from_as_of_date,
        from_release_revision,
        from_revision_fingerprint,
        to_as_of_date,
        to_release_revision,
        to_revision_fingerprint,
        gap_days,
        count(*) filter (where observed_at_start and start_is_delinquent)
            as starting_delinquent_count,
        count(*) filter (where observed_at_end and end_is_delinquent)
            as ending_published_delinquent_count,
        count(*) filter (where transition_class = 'delinquency_exit_observed')
            as observed_exit_count,
        count(*) filter (where transition_class = 'delinquency_still_observed')
            as still_delinquent_count,
        count(*) filter (where transition_class = 'delinquent_lost_to_observation')
            as not_observed_count,
        count(*) filter (where transition_class = 'delinquency_entry_observed')
            as matched_observed_entry_count,
        count(*) filter (where transition_class = 'delinquent_newly_observed')
            as newly_observed_delinquent_count
    from {{ ref('int_entity_transitions') }}
    group by
        from_as_of_date,
        from_release_revision,
        from_revision_fingerprint,
        to_as_of_date,
        to_release_revision,
        to_revision_fingerprint,
        gap_days

),

matched_status_transitions as (

    select
        from_as_of_date,
        from_release_revision,
        from_revision_fingerprint,
        to_as_of_date,
        to_release_revision,
        to_revision_fingerprint,
        start_status,
        end_status,
        coalesce(start_status, '__NULL_STATUS__') as normalized_from_status,
        coalesce(end_status, '__NULL_STATUS__') as normalized_to_status,
        transition_count
    from {{ ref('int_transition_matrix') }}
    where observed_at_start and observed_at_end

),

ranked_status_transitions as (

    select
        *,
        row_number() over (
            partition by
                from_as_of_date,
                from_release_revision,
                from_revision_fingerprint,
                to_as_of_date,
                to_release_revision,
                to_revision_fingerprint
            order by
                transition_count desc,
                normalized_from_status asc,
                normalized_to_status asc
        ) as transition_rank
    from matched_status_transitions

),

metrics as (

    select
        pair_counts.*,
        pair_counts.matched_observed_entry_count
            + pair_counts.newly_observed_delinquent_count as total_entrant_count,
        pair_counts.ending_published_delinquent_count - starting_delinquent_count
            as net_delinquent_movement,
        'starting_published_delinquent_cohort_v1' as observed_exit_denominator_id,
        case
            when starting_delinquent_count = 0 then null
            else observed_exit_count::double / starting_delinquent_count
        end as observed_exit_proportion,
        ranked.start_status as largest_transition_from_status,
        ranked.end_status as largest_transition_to_status,
        ranked.transition_count as largest_transition_count,
        'matched_observed_entry_population_v1' as matched_entry_denominator_id,
        'all_entrant_population_v1' as total_entrant_denominator_id
    from pair_counts
    left join ranked_status_transitions as ranked
        on pair_counts.from_as_of_date = ranked.from_as_of_date
       and pair_counts.from_release_revision = ranked.from_release_revision
       and pair_counts.from_revision_fingerprint = ranked.from_revision_fingerprint
       and pair_counts.to_as_of_date = ranked.to_as_of_date
       and pair_counts.to_release_revision = ranked.to_release_revision
       and pair_counts.to_revision_fingerprint = ranked.to_revision_fingerprint
       and ranked.transition_rank = 1

)

select
    *,
    {{ wilson_interval('observed_exit_count', 'starting_delinquent_count', 'lower') }}
        as observed_exit_wilson_95_lower,
    {{ wilson_interval('observed_exit_count', 'starting_delinquent_count', 'upper') }}
        as observed_exit_wilson_95_upper
from metrics
