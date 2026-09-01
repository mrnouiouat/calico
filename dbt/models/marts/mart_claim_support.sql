{{
    config(
        materialized='view'
    )
}}

-- One identity-free row for the first exact adjacent accepted-release pair,
-- with its next-pair comparison. This relation supports only the bounded
-- descriptive status-movement claim; it does not create publication machinery.

with matched_support as (

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

matched_entry_status_dates as (

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

ranked_status_dates as (

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
    from matched_entry_status_dates

),

pair_support as (

    select
        'claim_support_status_movement_v1' as claim_support_version,
        pair.from_as_of_date,
        pair.from_release_revision,
        pair.from_revision_fingerprint,
        pair.to_as_of_date,
        pair.to_release_revision,
        pair.to_revision_fingerprint,
        pair.gap_days,
        'Current - Reporting Incomplete' as matched_start_status,
        'published_delinquent_population_v1' as matched_end_population,
        coalesce(support.support_count, 0) as support_count,
        pair.matched_observed_entry_count as support_denominator_count,
        'matched_observed_entry_population_v1' as support_denominator_id,
        case
            when pair.matched_observed_entry_count = 0 then null
            else coalesce(support.support_count, 0)::double
                 / pair.matched_observed_entry_count
        end as support_share_of_matched_entries,
        pair.matched_observed_entry_count as total_matched_entry_count,
        'matched_observed_entry_population_v1' as matched_entry_denominator_id,
        pair.total_entrant_count,
        'all_entrant_population_v1' as total_entrant_denominator_id,
        dates.end_source_reported_current_status_date
            as dominant_source_reported_status_date,
        coalesce(dates.status_date_count, 0) as dominant_status_date_count,
        pair.matched_observed_entry_count as dominant_status_date_denominator_count,
        'matched_observed_entry_population_v1'
            as dominant_status_date_denominator_id,
        case
            when pair.matched_observed_entry_count = 0 then null
            else coalesce(dates.status_date_count, 0)::double
                 / pair.matched_observed_entry_count
        end as dominant_status_date_share
    from {{ ref('mart_adjacent_pair_metrics') }} as pair
    left join matched_support as support
        on pair.from_as_of_date = support.from_as_of_date
       and pair.from_release_revision = support.from_release_revision
       and pair.from_revision_fingerprint = support.from_revision_fingerprint
       and pair.to_as_of_date = support.to_as_of_date
       and pair.to_release_revision = support.to_release_revision
       and pair.to_revision_fingerprint = support.to_revision_fingerprint
    left join ranked_status_dates as dates
        on pair.from_as_of_date = dates.from_as_of_date
       and pair.from_release_revision = dates.from_release_revision
       and pair.from_revision_fingerprint = dates.from_revision_fingerprint
       and pair.to_as_of_date = dates.to_as_of_date
       and pair.to_release_revision = dates.to_release_revision
       and pair.to_revision_fingerprint = dates.to_revision_fingerprint
       and dates.status_date_rank = 1

),

with_next_pair as (

    select
        *,
        lead(from_as_of_date) over pair_order as next_from_as_of_date,
        lead(to_as_of_date) over pair_order as next_to_as_of_date,
        lead(gap_days) over pair_order as next_gap_days,
        lead(total_matched_entry_count) over pair_order
            as next_total_matched_entry_count,
        row_number() over pair_order as pair_ordinal
    from pair_support
    window pair_order as (
        order by
            from_as_of_date,
            from_release_revision,
            from_revision_fingerprint,
            to_as_of_date,
            to_release_revision,
            to_revision_fingerprint
    )

)

select
    claim_support_version,
    from_as_of_date,
    from_release_revision,
    from_revision_fingerprint,
    to_as_of_date,
    to_release_revision,
    to_revision_fingerprint,
    gap_days,
    matched_start_status,
    matched_end_population,
    support_count,
    support_denominator_count,
    support_denominator_id,
    support_share_of_matched_entries,
    total_matched_entry_count,
    matched_entry_denominator_id,
    total_entrant_count,
    total_entrant_denominator_id,
    dominant_source_reported_status_date,
    dominant_status_date_count,
    dominant_status_date_denominator_count,
    dominant_status_date_denominator_id,
    dominant_status_date_share,
    next_from_as_of_date,
    next_to_as_of_date,
    next_gap_days,
    next_total_matched_entry_count
from with_next_pair
where pair_ordinal = 1
