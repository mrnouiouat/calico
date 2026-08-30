{{
    config(
        materialized='view'
    )
}}

-- Universal trimming and honest outside-in staging semantics (D-09, D-10).
--
-- Every source string is passed through `nullif(trim(source), '')` --
-- never a substring, never a coalesce with another column, and never
-- truncated. `state_charity_registration_number` is the sole longitudinal
-- key (D-10): its value is the exact full trimmed source registration
-- string or null, with no fallback to the excluded federal identifier.
-- `source_reported_federal_employer_identification_number` (FEIN) is
-- retained only as a plain trimmed source field for row-level
-- reconciliation; it is never used, exposed as identity, hashed, or
-- joined against anything downstream (D-005/D-010).
--
-- The two source-reported date fields keep the exact required D-09/D-13
-- names -- `source_reported_last_renewal_date` (D1) and
-- `source_reported_current_status_date` (D3) -- and neither implies
-- accepted-submission or exact-onset semantics. Every date field also
-- exposes a companion `..._nonblank_unparseable` boolean so a nonblank
-- source value that fails `try_cast` stays visibly detectable rather than
-- silently disappearing into a null.
--
-- Release identity, structural provenance, and source_list/source_line_no
-- pass through from `base_admitted_registry_records` completely unchanged.

with source_rows as (

    select * from {{ ref('base_admitted_registry_records') }}

)

select

    nullif(trim("Registry Status"), '') as source_reported_registry_status,

    -- Sole longitudinal identity (D-10). Blank stays null; never
    -- substring-matched, never coalesced with FEIN or any other column.
    nullif(trim("State Charity Reg#"), '') as state_charity_registration_number,

    -- Retained only for row-level reconciliation; never identity (D-005/D-010).
    nullif(trim("FEIN"), '') as source_reported_federal_employer_identification_number,

    nullif(trim("SOS/FTB#"), '') as source_reported_sos_or_ftb_number,
    nullif(trim("Name"), '') as source_reported_organization_name,
    nullif(trim("City"), '') as source_reported_city,
    nullif(trim("State"), '') as source_reported_state,

    try_cast(nullif(trim("Issue Date"), '') as date) as source_reported_issue_date,
    (
        nullif(trim("Issue Date"), '') is not null
        and try_cast(nullif(trim("Issue Date"), '') as date) is null
    ) as source_reported_issue_date_nonblank_unparseable,

    -- D1: explicitly source-reported, semantically unresolved -- never
    -- named or treated as an accepted-submission date.
    try_cast(nullif(trim("Last Renewal"), '') as date) as source_reported_last_renewal_date,
    (
        nullif(trim("Last Renewal"), '') is not null
        and try_cast(nullif(trim("Last Renewal"), '') as date) is null
    ) as source_reported_last_renewal_date_nonblank_unparseable,

    -- D3: exposed only as a source-reported current-status date -- never
    -- named or treated as an exact onset date.
    try_cast(nullif(trim("Date Status Set"), '') as date) as source_reported_current_status_date,
    (
        nullif(trim("Date Status Set"), '') is not null
        and try_cast(nullif(trim("Date Status Set"), '') as date) is null
    ) as source_reported_current_status_date_nonblank_unparseable,

    nullif(trim("As-of Date"), '') as source_reported_as_of_date,

    -- Immutable release identity and structural provenance (D-08):
    -- unchanged, uncast, pass-through.
    as_of_date,
    release_revision,
    revision_fingerprint,
    source_list,
    source_line_no

from source_rows
