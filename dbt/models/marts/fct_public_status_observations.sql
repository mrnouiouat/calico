{{
    config(
        materialized='table'
    )
}}

-- Authoritative allowed named status-observation grain (D-16, D-21
-- representative model). Grain: one row per explicit eligible exact
-- registration key and promoted accepted release identity -- at most one
-- row per key/release (D-16). Built from eligible keys cross joined to
-- every promoted release, left joined to int_keyed_snapshots on the full
-- release identity, so a key genuinely absent from a given release is an
-- explicit 'not_observed' observation state -- never silently omitted and
-- never coalesced into an invented status.
--
-- Positive column list only, mirroring dim_public_organizations's allowed-
-- field boundary (D-007/D-17): organization name, exact registration
-- number, city/state, accepted release identity, and source-reported
-- status/history fields. Materialized as an immediate full-refresh table:
-- the reused heavy public observation fact (D-21/materialization policy).

with eligible_keys as (

    select state_charity_registration_number
    from {{ ref('int_public_organization_eligibility') }}
    where eligibility_classification = 'eligible'

),

eligible_key_release_grid as (

    select
        eligible_keys.state_charity_registration_number,
        releases.as_of_date,
        releases.release_revision,
        releases.revision_fingerprint
    from eligible_keys
    cross join {{ ref('int_promoted_releases') }} as releases

)

select

    grid.state_charity_registration_number,
    grid.as_of_date,
    grid.release_revision,
    grid.revision_fingerprint,

    case
        when snapshot.state_charity_registration_number is not null then 'observed'
        else 'not_observed'
    end as observation_state,

    snapshot.source_reported_organization_name as organization_name,
    snapshot.source_reported_city as city,
    snapshot.source_reported_state as state,
    snapshot.source_reported_registry_status as source_reported_status,
    snapshot.is_delinquent

from eligible_key_release_grid as grid
left join {{ ref('int_keyed_snapshots') }} as snapshot
    on grid.state_charity_registration_number = snapshot.state_charity_registration_number
   and grid.as_of_date = snapshot.as_of_date
   and grid.release_revision = snapshot.release_revision
   and grid.revision_fingerprint = snapshot.revision_fingerprint
