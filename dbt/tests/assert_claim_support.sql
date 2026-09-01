-- Independent identity-free recomputation of the one governed support row.
-- Any returned row is a failure and exposes only safe pair dates and counts.
with support_counts as (

    select
        from_as_of_date,
        from_release_revision,
        from_revision_fingerprint,
        to_as_of_date,
        to_release_revision,
        to_revision_fingerprint,
        count(*) as support_count
    from {{ ref('int_entity_transitions') }}
    where observed_at_start
      and observed_at_end
      and start_status = 'Current - Reporting Incomplete'
      and end_is_delinquent
    group by
        from_as_of_date,
        from_release_revision,
        from_revision_fingerprint,
        to_as_of_date,
        to_release_revision,
        to_revision_fingerprint

),

date_counts as (

    select
        from_as_of_date,
        from_release_revision,
        from_revision_fingerprint,
        to_as_of_date,
        to_release_revision,
        to_revision_fingerprint,
        end_source_reported_current_status_date,
        count(*) as status_date_count
    from {{ ref('int_entity_transitions') }}
    where transition_class = 'delinquency_entry_observed'
    group by
        from_as_of_date,
        from_release_revision,
        from_revision_fingerprint,
        to_as_of_date,
        to_release_revision,
        to_revision_fingerprint,
        end_source_reported_current_status_date

),

ranked_dates as (

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
                status_date_count desc,
                end_source_reported_current_status_date asc nulls last
        ) as status_date_rank
    from date_counts

),

all_pairs as (

    select
        pair.from_as_of_date,
        pair.from_release_revision,
        pair.from_revision_fingerprint,
        pair.to_as_of_date,
        pair.to_release_revision,
        pair.to_revision_fingerprint,
        pair.gap_days,
        coalesce(support.support_count, 0) as support_count,
        pair.matched_observed_entry_count as matched_entry_count,
        pair.total_entrant_count,
        dates.end_source_reported_current_status_date as dominant_status_date,
        coalesce(dates.status_date_count, 0) as dominant_status_date_count
    from {{ ref('mart_adjacent_pair_metrics') }} as pair
    left join support_counts as support
        on pair.from_as_of_date = support.from_as_of_date
       and pair.from_release_revision = support.from_release_revision
       and pair.from_revision_fingerprint = support.from_revision_fingerprint
       and pair.to_as_of_date = support.to_as_of_date
       and pair.to_release_revision = support.to_release_revision
       and pair.to_revision_fingerprint = support.to_revision_fingerprint
    left join ranked_dates as dates
        on pair.from_as_of_date = dates.from_as_of_date
       and pair.from_release_revision = dates.from_release_revision
       and pair.from_revision_fingerprint = dates.from_revision_fingerprint
       and pair.to_as_of_date = dates.to_as_of_date
       and pair.to_release_revision = dates.to_release_revision
       and pair.to_revision_fingerprint = dates.to_revision_fingerprint
       and dates.status_date_rank = 1

),

expected as (

    select
        *,
        lead(from_as_of_date) over pair_order as next_from_as_of_date,
        lead(to_as_of_date) over pair_order as next_to_as_of_date,
        lead(gap_days) over pair_order as next_gap_days,
        lead(matched_entry_count) over pair_order as next_matched_entry_count,
        row_number() over pair_order as pair_ordinal
    from all_pairs
    window pair_order as (
        order by
            from_as_of_date,
            from_release_revision,
            from_revision_fingerprint,
            to_as_of_date,
            to_release_revision,
            to_revision_fingerprint
    )

),

failures as (

    select
        'claim_support_mismatch:'
            || coalesce(expected.from_as_of_date, actual.from_as_of_date)::varchar
            || ':' || coalesce(expected.to_as_of_date, actual.to_as_of_date)::varchar
            || ':expected_support=' || coalesce(expected.support_count, -1)::varchar
            || ':actual_support=' || coalesce(actual.support_count, -1)::varchar
            as failure_reason
    from (select * from expected where pair_ordinal = 1) as expected
    full outer join {{ ref('mart_claim_support') }} as actual
        on expected.from_as_of_date = actual.from_as_of_date
       and expected.from_release_revision = actual.from_release_revision
       and expected.from_revision_fingerprint = actual.from_revision_fingerprint
       and expected.to_as_of_date = actual.to_as_of_date
       and expected.to_release_revision = actual.to_release_revision
       and expected.to_revision_fingerprint = actual.to_revision_fingerprint
    where actual.claim_support_version is distinct from 'claim_support_status_movement_v1'
       or actual.matched_start_status is distinct from 'Current - Reporting Incomplete'
       or actual.matched_end_population is distinct from 'published_delinquent_population_v1'
       or actual.support_count is distinct from expected.support_count
       or actual.support_denominator_count is distinct from expected.matched_entry_count
       or actual.total_matched_entry_count is distinct from expected.matched_entry_count
       or actual.total_entrant_count is distinct from expected.total_entrant_count
       or actual.dominant_source_reported_status_date is distinct from expected.dominant_status_date
       or actual.dominant_status_date_count is distinct from expected.dominant_status_date_count
       or actual.next_from_as_of_date is distinct from expected.next_from_as_of_date
       or actual.next_to_as_of_date is distinct from expected.next_to_as_of_date
       or actual.next_gap_days is distinct from expected.next_gap_days
       or actual.next_total_matched_entry_count is distinct from expected.next_matched_entry_count

)

select failure_reason from failures
