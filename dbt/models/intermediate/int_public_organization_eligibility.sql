{{
    config(
        materialized='view'
    )
}}

-- Private audit helper (not a required grain owner; D-01). Grain: one row
-- per distinct exact full nonblank State Charity registration key ever
-- observed in int_keyed_snapshots. Left joins every such key to the private
-- runtime_input.public_eligibility_classifications source and normalizes a
-- missing match to 'unclassified' -- the fail-closed default (D-18,
-- T-04-05B). Retains all three closed states here for private audit; only
-- an explicit 'eligible' row may ever reach dim_public_organizations or
-- fct_public_status_observations downstream. No fuzzy/name heuristic or
-- score substitutes for a reviewed classification anywhere in this model
-- (T-04-05F).

with distinct_keys as (

    select distinct state_charity_registration_number
    from {{ ref('int_keyed_snapshots') }}

)

select

    distinct_keys.state_charity_registration_number,
    coalesce(classifications.classification, 'unclassified') as eligibility_classification,
    classifications.classification_version

from distinct_keys
left join {{ source('runtime_input', 'public_eligibility_classifications') }} as classifications
    on distinct_keys.state_charity_registration_number = classifications.registration_number
