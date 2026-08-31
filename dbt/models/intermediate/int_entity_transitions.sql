{{
    config(
        materialized='table'
    )
}}

-- Authoritative exact-key adjacent transition grain (D-05/D-06/D-07,
-- D-21 representative model). The full union of exact registration keys
-- observed at either endpoint of every delivered Phase 3 adjacent
-- distinct-date pair -- never a substring match, never a fallback
-- identity, and never a same-date revision treated as a time point.
--
-- Built as three explicit, identically shaped branches over the exact
-- three-field release identity at each endpoint:
--   1. `observed_at_both`  -- exact key present at both endpoints (inner join)
--   2. `start_only`        -- exact key present only at the start (anti join)
--   3. `end_only`          -- exact key present only at the end (anti join)
-- `union all` of the three branches is the complete endpoint union
-- (`assert_transition_endpoint_union.sql` independently recomputes this).
--
-- A missing endpoint stays a missing endpoint: `observed_at_start` /
-- `observed_at_end` carry absence, and the corresponding status/source
-- list/delinquency/diagnostic fields stay null rather than being coalesced
-- into an invented status (D-06). Exactly one of eight closed,
-- mutually exclusive classes is assigned by one exhaustive `case`
-- (`assert_transition_classification.sql`). Both source-reported
-- diagnostic dates pass through with an explicit start/end prefix only
-- (D-07): neither is an onset/exit/filing timestamp and neither drives
-- classification.

with start_observations as (

    select
        pairs.from_as_of_date,
        pairs.from_release_revision,
        pairs.from_revision_fingerprint,
        pairs.to_as_of_date,
        pairs.to_release_revision,
        pairs.to_revision_fingerprint,
        pairs.gap_days,
        snapshot.state_charity_registration_number,
        snapshot.source_reported_registry_status as start_status,
        snapshot.source_list as start_source_list,
        snapshot.is_delinquent as start_is_delinquent,
        snapshot.source_reported_last_renewal_date as start_source_reported_last_renewal_date,
        snapshot.source_reported_last_renewal_date_nonblank_unparseable
            as start_source_reported_last_renewal_date_nonblank_unparseable,
        snapshot.source_reported_current_status_date as start_source_reported_current_status_date,
        snapshot.source_reported_current_status_date_nonblank_unparseable
            as start_source_reported_current_status_date_nonblank_unparseable
    from {{ ref('int_adjacent_release_pairs') }} as pairs
    inner join {{ ref('int_keyed_snapshots') }} as snapshot
        on snapshot.as_of_date = pairs.from_as_of_date
       and snapshot.release_revision = pairs.from_release_revision
       and snapshot.revision_fingerprint = pairs.from_revision_fingerprint

),

end_observations as (

    select
        pairs.from_as_of_date,
        pairs.from_release_revision,
        pairs.from_revision_fingerprint,
        pairs.to_as_of_date,
        pairs.to_release_revision,
        pairs.to_revision_fingerprint,
        pairs.gap_days,
        snapshot.state_charity_registration_number,
        snapshot.source_reported_registry_status as end_status,
        snapshot.source_list as end_source_list,
        snapshot.is_delinquent as end_is_delinquent,
        snapshot.source_reported_last_renewal_date as end_source_reported_last_renewal_date,
        snapshot.source_reported_last_renewal_date_nonblank_unparseable
            as end_source_reported_last_renewal_date_nonblank_unparseable,
        snapshot.source_reported_current_status_date as end_source_reported_current_status_date,
        snapshot.source_reported_current_status_date_nonblank_unparseable
            as end_source_reported_current_status_date_nonblank_unparseable
    from {{ ref('int_adjacent_release_pairs') }} as pairs
    inner join {{ ref('int_keyed_snapshots') }} as snapshot
        on snapshot.as_of_date = pairs.to_as_of_date
       and snapshot.release_revision = pairs.to_release_revision
       and snapshot.revision_fingerprint = pairs.to_revision_fingerprint

),

observed_at_both as (

    select
        s.from_as_of_date,
        s.from_release_revision,
        s.from_revision_fingerprint,
        s.to_as_of_date,
        s.to_release_revision,
        s.to_revision_fingerprint,
        s.gap_days,
        s.state_charity_registration_number,
        true as observed_at_start,
        true as observed_at_end,
        s.start_status,
        e.end_status,
        s.start_source_list,
        e.end_source_list,
        s.start_is_delinquent,
        e.end_is_delinquent,
        s.start_source_reported_last_renewal_date,
        s.start_source_reported_last_renewal_date_nonblank_unparseable,
        s.start_source_reported_current_status_date,
        s.start_source_reported_current_status_date_nonblank_unparseable,
        e.end_source_reported_last_renewal_date,
        e.end_source_reported_last_renewal_date_nonblank_unparseable,
        e.end_source_reported_current_status_date,
        e.end_source_reported_current_status_date_nonblank_unparseable
    from start_observations as s
    inner join end_observations as e
        on s.from_as_of_date = e.from_as_of_date
       and s.from_release_revision = e.from_release_revision
       and s.from_revision_fingerprint = e.from_revision_fingerprint
       and s.to_as_of_date = e.to_as_of_date
       and s.to_release_revision = e.to_release_revision
       and s.to_revision_fingerprint = e.to_revision_fingerprint
       and s.state_charity_registration_number = e.state_charity_registration_number

),

start_only as (

    select
        s.from_as_of_date,
        s.from_release_revision,
        s.from_revision_fingerprint,
        s.to_as_of_date,
        s.to_release_revision,
        s.to_revision_fingerprint,
        s.gap_days,
        s.state_charity_registration_number,
        true as observed_at_start,
        false as observed_at_end,
        s.start_status,
        null::varchar as end_status,
        s.start_source_list,
        null::varchar as end_source_list,
        s.start_is_delinquent,
        null::boolean as end_is_delinquent,
        s.start_source_reported_last_renewal_date,
        s.start_source_reported_last_renewal_date_nonblank_unparseable,
        s.start_source_reported_current_status_date,
        s.start_source_reported_current_status_date_nonblank_unparseable,
        null::date as end_source_reported_last_renewal_date,
        null::boolean as end_source_reported_last_renewal_date_nonblank_unparseable,
        null::date as end_source_reported_current_status_date,
        null::boolean as end_source_reported_current_status_date_nonblank_unparseable
    from start_observations as s
    anti join end_observations as e
        on s.from_as_of_date = e.from_as_of_date
       and s.from_release_revision = e.from_release_revision
       and s.from_revision_fingerprint = e.from_revision_fingerprint
       and s.to_as_of_date = e.to_as_of_date
       and s.to_release_revision = e.to_release_revision
       and s.to_revision_fingerprint = e.to_revision_fingerprint
       and s.state_charity_registration_number = e.state_charity_registration_number

),

end_only as (

    select
        e.from_as_of_date,
        e.from_release_revision,
        e.from_revision_fingerprint,
        e.to_as_of_date,
        e.to_release_revision,
        e.to_revision_fingerprint,
        e.gap_days,
        e.state_charity_registration_number,
        false as observed_at_start,
        true as observed_at_end,
        null::varchar as start_status,
        e.end_status,
        null::varchar as start_source_list,
        e.end_source_list,
        null::boolean as start_is_delinquent,
        e.end_is_delinquent,
        null::date as start_source_reported_last_renewal_date,
        null::boolean as start_source_reported_last_renewal_date_nonblank_unparseable,
        null::date as start_source_reported_current_status_date,
        null::boolean as start_source_reported_current_status_date_nonblank_unparseable,
        e.end_source_reported_last_renewal_date,
        e.end_source_reported_last_renewal_date_nonblank_unparseable,
        e.end_source_reported_current_status_date,
        e.end_source_reported_current_status_date_nonblank_unparseable
    from end_observations as e
    anti join start_observations as s
        on s.from_as_of_date = e.from_as_of_date
       and s.from_release_revision = e.from_release_revision
       and s.from_revision_fingerprint = e.from_revision_fingerprint
       and s.to_as_of_date = e.to_as_of_date
       and s.to_release_revision = e.to_release_revision
       and s.to_revision_fingerprint = e.to_revision_fingerprint
       and s.state_charity_registration_number = e.state_charity_registration_number

),

unioned as (

    select * from observed_at_both
    union all
    select * from start_only
    union all
    select * from end_only

)

select

    from_as_of_date,
    from_release_revision,
    from_revision_fingerprint,
    to_as_of_date,
    to_release_revision,
    to_revision_fingerprint,
    gap_days,
    state_charity_registration_number,
    observed_at_start,
    observed_at_end,
    start_status,
    end_status,
    start_source_list,
    end_source_list,
    start_is_delinquent,
    end_is_delinquent,
    start_source_reported_last_renewal_date,
    start_source_reported_last_renewal_date_nonblank_unparseable,
    start_source_reported_current_status_date,
    start_source_reported_current_status_date_nonblank_unparseable,
    end_source_reported_last_renewal_date,
    end_source_reported_last_renewal_date_nonblank_unparseable,
    end_source_reported_current_status_date,
    end_source_reported_current_status_date_nonblank_unparseable,

    -- One exhaustive, mutually exclusive closed classification (D-06).
    -- A missing endpoint is always an observation-flag distinction, never
    -- a coalesced status; each branch below tests observation flags and
    -- delinquency flags only, never a nullable status column directly.
    case
        when observed_at_start and observed_at_end
                and not start_is_delinquent and end_is_delinquent
            then 'delinquency_entry_observed'
        when not observed_at_start and observed_at_end and end_is_delinquent
            then 'delinquent_newly_observed'
        when observed_at_start and observed_at_end
                and start_is_delinquent and not end_is_delinquent
            then 'delinquency_exit_observed'
        when observed_at_start and observed_at_end
                and start_is_delinquent and end_is_delinquent
            then 'delinquency_still_observed'
        when observed_at_start and not observed_at_end and start_is_delinquent
            then 'delinquent_lost_to_observation'
        when observed_at_start and observed_at_end
            then 'observed_status_movement_other'
        when not observed_at_start and observed_at_end
            then 'newly_observed_other'
        when observed_at_start and not observed_at_end
            then 'not_observed_after_other'
    end as transition_class

from unioned
