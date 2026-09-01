-- Independent, identity-free recomputation. Any returned row is a failure and
-- projects only safe pair dates, a closed reason, and aggregate counts.
with expected_pair_counts as (
    select
        from_as_of_date,
        from_release_revision,
        from_revision_fingerprint,
        to_as_of_date,
        to_release_revision,
        to_revision_fingerprint,
        count(*) filter (where observed_at_start and start_is_delinquent)
            as expected_starting_count,
        count(*) filter (where observed_at_end and end_is_delinquent)
            as expected_ending_count,
        count(*) filter (where transition_class = 'delinquency_exit_observed')
            as expected_exit_count,
        count(*) filter (where transition_class = 'delinquency_still_observed')
            as expected_still_count,
        count(*) filter (where transition_class = 'delinquent_lost_to_observation')
            as expected_not_observed_count,
        count(*) filter (where transition_class = 'delinquency_entry_observed')
            as expected_matched_entry_count,
        count(*) filter (where transition_class = 'delinquent_newly_observed')
            as expected_newly_observed_count
    from {{ ref('int_entity_transitions') }}
    group by 1, 2, 3, 4, 5, 6
),
expected_largest_transition as (
    select * exclude (winner_rank, normalized_from_status, normalized_to_status)
    from (
        select
            from_as_of_date,
            from_release_revision,
            from_revision_fingerprint,
            to_as_of_date,
            to_release_revision,
            to_revision_fingerprint,
            start_status as expected_from_status,
            end_status as expected_to_status,
            transition_count as expected_transition_count,
            coalesce(start_status, '__NULL_STATUS__') as normalized_from_status,
            coalesce(end_status, '__NULL_STATUS__') as normalized_to_status,
            row_number() over (
                partition by 1, 2, 3, 4, 5, 6
                order by transition_count desc,
                    normalized_from_status asc, normalized_to_status asc
            ) as winner_rank
        from {{ ref('int_transition_matrix') }}
        where observed_at_start and observed_at_end
    )
    where winner_rank = 1
),
failures as (
    select
        mart.from_as_of_date,
        mart.to_as_of_date,
        'pair_count_or_movement_mismatch' as failure_reason,
        expected.expected_starting_count as expected_count,
        mart.starting_delinquent_count as actual_count
    from {{ ref('mart_adjacent_pair_metrics') }} as mart
    inner join expected_pair_counts as expected using (
        from_as_of_date, from_release_revision, from_revision_fingerprint,
        to_as_of_date, to_release_revision, to_revision_fingerprint
    )
    where mart.starting_delinquent_count <> expected.expected_starting_count
       or mart.ending_published_delinquent_count <> expected.expected_ending_count
       or mart.observed_exit_count <> expected.expected_exit_count
       or mart.still_delinquent_count <> expected.expected_still_count
       or mart.not_observed_count <> expected.expected_not_observed_count
       or mart.matched_observed_entry_count <> expected.expected_matched_entry_count
       or mart.newly_observed_delinquent_count <> expected.expected_newly_observed_count
       or mart.total_entrant_count <>
            expected.expected_matched_entry_count + expected.expected_newly_observed_count
       or mart.net_delinquent_movement <>
            expected.expected_ending_count - expected.expected_starting_count

    union all

    select
        mart.from_as_of_date,
        mart.to_as_of_date,
        'largest_transition_mismatch' as failure_reason,
        expected.expected_transition_count as expected_count,
        mart.largest_transition_count as actual_count
    from {{ ref('mart_adjacent_pair_metrics') }} as mart
    inner join expected_largest_transition as expected using (
        from_as_of_date, from_release_revision, from_revision_fingerprint,
        to_as_of_date, to_release_revision, to_revision_fingerprint
    )
    where mart.largest_transition_from_status is distinct from expected.expected_from_status
       or mart.largest_transition_to_status is distinct from expected.expected_to_status
       or mart.largest_transition_count <> expected.expected_transition_count
)
select * from failures
