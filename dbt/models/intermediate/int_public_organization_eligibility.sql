{{
    config(
        materialized='view'
    )
}}

-- Private audit helper (not a required grain owner; D-01). Grain: one row
-- per distinct exact full nonblank State Charity registration key ever
-- observed in int_keyed_snapshots. Left joins every such key to the private
-- runtime_input.public_eligibility_classifications source.
--
-- Owner decision 2026-09-15 (supersedes the D-18 exclude-by-default rule):
-- every identifiable registration key publishes by default, so a key with
-- no sidecar entry normalizes to 'eligible' rather than 'unclassified'.
-- The registry rows this republishes are already published by the state and
-- are reproduced unchanged beside the state's own verification link. The
-- sidecar remains the exclusion mechanism, not the admission mechanism: an
-- explicit 'ambiguous_natural_person' or 'unclassified' entry still keeps
-- that exact key out of dim_public_organizations and
-- fct_public_status_observations. Keys that are blank at source remain
-- excluded everywhere by int_keyed_snapshots itself -- an unidentifiable
-- row cannot be tracked across releases or matched to a lookup.
--
-- Only an explicit 'eligible' classification reaches a public relation, and
-- no fuzzy/name heuristic or score substitutes for a classification
-- anywhere in this model (T-04-05F).

with distinct_keys as (

    select distinct state_charity_registration_number
    from {{ ref('int_keyed_snapshots') }}

)

select

    distinct_keys.state_charity_registration_number,
    coalesce(classifications.classification, 'eligible') as eligibility_classification,
    classifications.classification_version

from distinct_keys
left join {{ source('runtime_input', 'public_eligibility_classifications') }} as classifications
    on distinct_keys.state_charity_registration_number = classifications.registration_number
