{{
    config(
        materialized='view'
    )
}}

-- Authoritative publication-eligible organization grain (D-16, D-21
-- representative model). Grain: one row per explicit eligible exact
-- registration key (D-16/D-18) -- unioned only from
-- int_public_organization_eligibility rows already normalized to
-- 'eligible'; an 'ambiguous_natural_person' or 'unclassified' key never
-- reaches this model. Deterministically selects the one latest observed
-- full-release row per eligible key, ordered by accepted as-of date,
-- release revision, and revision fingerprint descending -- never backfills
-- an individual null display attribute from an older release (D-16
-- anti-pattern: the selected row is chosen and projected as a whole).
--
-- Positive column list only: organization name, exact registration
-- number, city/state for disambiguation, latest observed accepted release
-- identity/status, and a fixed official Registry Search Tool verification
-- instruction (D-007/D-17). Excluded categories -- FEIN/EIN, address,
-- people/contact, source PDFs, raw source columns, and any unapproved-join
-- field -- never appear here by construction; this model never selects
-- `*` and never reads a forbidden staging column.

with eligible_keys as (

    select state_charity_registration_number
    from {{ ref('int_public_organization_eligibility') }}
    where eligibility_classification = 'eligible'

),

ranked_snapshots as (

    select

        snapshot.state_charity_registration_number,
        snapshot.source_reported_organization_name,
        snapshot.source_reported_city,
        snapshot.source_reported_state,
        snapshot.source_reported_registry_status,
        snapshot.is_delinquent,
        snapshot.as_of_date,
        snapshot.release_revision,
        snapshot.revision_fingerprint,

        row_number() over (
            partition by snapshot.state_charity_registration_number
            order by
                snapshot.as_of_date desc,
                snapshot.release_revision desc,
                snapshot.revision_fingerprint desc
        ) as observation_rank

    from {{ ref('int_keyed_snapshots') }} as snapshot
    inner join eligible_keys
        on snapshot.state_charity_registration_number = eligible_keys.state_charity_registration_number

)

select

    state_charity_registration_number,
    source_reported_organization_name as organization_name,
    source_reported_city as city,
    source_reported_state as state,
    source_reported_registry_status as latest_source_reported_status,
    is_delinquent as latest_is_delinquent,
    as_of_date as latest_observed_as_of_date,
    release_revision as latest_observed_release_revision,
    revision_fingerprint as latest_observed_revision_fingerprint,
    'Verify this organization''s current status using the official California Registry Search Tool and the exact registration number above.'
        as official_verification_instructions

from ranked_snapshots
where observation_rank = 1
