{{
    config(
        materialized='view'
    )
}}

-- Ordered observation helper (D-08 through D-11 supporting relation, not
-- itself one of the ten required grains). Assigns a global observation
-- ordinal from the delivered promoted date spine, then computes -- for
-- every keyed snapshot row -- the exact per-key immediately-prior and
-- immediately-next ordinal, promoted date, and delinquency state.
--
-- Neighbor windows are computed over the *complete* keyed observation
-- sequence, before any filtering to the published delinquent population:
-- filtering first would remove the non-delinquent neighbor rows
-- `int_delinquency_spells` needs to tell an observed entry/exit apart from
-- a left-/right-censored or lost boundary. `int_delinquency_spells`
-- consumes this relation directly and never recomputes `lag`/`lead` or the
-- global ordinal itself.
--
-- A missing global ordinal on either neighbor is an information boundary,
-- never continuous status (D-11): a neighbor may sit on any earlier/later
-- promoted date, and a gap greater than one ordinal between the current
-- row and a neighbor means at least one promoted date passed with no
-- observation of this exact key at all.
--
-- Source-reported diagnostic dates (D-07) pass through unchanged and are
-- never read by any window or filter in this relation.

with date_ordinals as (

    select
        as_of_date,
        release_revision,
        revision_fingerprint,
        row_number() over (order by as_of_date) as observation_ordinal
    from {{ ref('int_promoted_date_spine') }}

),

complete_keyed_observations as (

    select

        snapshot.state_charity_registration_number,
        snapshot.source_reported_registry_status,
        snapshot.is_delinquent,
        snapshot.source_reported_last_renewal_date,
        snapshot.source_reported_last_renewal_date_nonblank_unparseable,
        snapshot.source_reported_current_status_date,
        snapshot.source_reported_current_status_date_nonblank_unparseable,

        ordinals.as_of_date,
        ordinals.release_revision,
        ordinals.revision_fingerprint,
        ordinals.observation_ordinal

    from {{ ref('int_keyed_snapshots') }} as snapshot
    inner join date_ordinals as ordinals
        on snapshot.as_of_date = ordinals.as_of_date
       and snapshot.release_revision = ordinals.release_revision
       and snapshot.revision_fingerprint = ordinals.revision_fingerprint

),

neighbor_windows as (

    select

        observations.*,

        lag(observations.observation_ordinal) over key_window as prior_observation_ordinal,
        lag(observations.as_of_date) over key_window as prior_as_of_date,
        lag(observations.is_delinquent) over key_window as prior_is_delinquent,

        lead(observations.observation_ordinal) over key_window as next_observation_ordinal,
        lead(observations.as_of_date) over key_window as next_as_of_date,
        lead(observations.is_delinquent) over key_window as next_is_delinquent

    from complete_keyed_observations as observations
    window key_window as (
        partition by observations.state_charity_registration_number
        order by observations.observation_ordinal
    )

)

select

    as_of_date,
    release_revision,
    revision_fingerprint,
    observation_ordinal,

    state_charity_registration_number,
    source_reported_registry_status,
    is_delinquent,

    source_reported_last_renewal_date,
    source_reported_last_renewal_date_nonblank_unparseable,
    source_reported_current_status_date,
    source_reported_current_status_date_nonblank_unparseable,

    prior_observation_ordinal,
    prior_as_of_date,
    prior_is_delinquent,

    next_observation_ordinal,
    next_as_of_date,
    next_is_delinquent

from neighbor_windows
