{{
    config(
        materialized='view'
    )
}}

-- Authoritative aggregated unkeyed-coverage grain (D-04). Groups the
-- delivered row-level `int_keyless_registry_coverage` audit relation by
-- full promoted release identity, source list, and source-reported
-- status -- never reimplementing the blank-registration predicate itself,
-- so this relation can never drift from Phase 3's one exhaustive
-- disposition `case`. Nullable source-reported status is retained
-- honestly: a blank status groups as null here, never coerced into an
-- invented label.

select

    as_of_date,
    release_revision,
    revision_fingerprint,
    parser_contract_version,
    source_list,
    source_reported_registry_status,
    count(*) as record_count

from {{ ref('int_keyless_registry_coverage') }}
group by
    as_of_date,
    release_revision,
    revision_fingerprint,
    parser_contract_version,
    source_list,
    source_reported_registry_status
