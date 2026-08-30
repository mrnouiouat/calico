{{
    config(
        materialized='view'
    )
}}

-- Total, one-row-per-promoted-record disposition (D-11).
--
-- Record identity for reconciliation is the immutable full release key
-- (as_of_date, release_revision, revision_fingerprint) plus source_list and
-- source_line_no -- never organization identity, and never the excluded
-- federal identifier. Every promoted row receives exactly one closed,
-- documented disposition from this single exhaustive `case`, evaluated in
-- this fixed order:
--
--   1. `untracked_blank_registration` -- the trimmed State Charity
--      registration number is blank (D-10). This check runs first and
--      unconditionally, regardless of any other column: a blank key is
--      always untracked coverage, never dropped, never substring-matched,
--      and never coalesced onto FEIN or any other column.
--   2. `excluded_from_typed_path_blank_status` -- the registration is
--      nonblank but the trimmed Registry Status is blank, so the row
--      carries a key but still cannot satisfy a typed downstream path
--      (D-11).
--   3. `eligible_for_keyed_path` -- both the registration and the status
--      are nonblank.
--
-- Checking blank registration first is a deliberate precedence choice: a
-- row's longitudinal trackability (D-10) is a more fundamental property
-- than any single typed-path failure, and giving every row exactly one
-- disposition requires an explicit order whenever more than one defect
-- could apply to the same row. No currently admitted or fixture row
-- combines both defects.
--
-- No row is ever dropped: `assert_registry_record_disposition_reconciliation.sql`
-- proves this relation's row count equals `int_promoted_registry_records`'s
-- row count, both globally and by partition.
--
-- `parser_contract_version` is carried in from `int_promoted_releases` on
-- the same full release key (`int_promoted_registry_records` itself does
-- not select it) so every disposition row stays traceable to the exact
-- admitted parser contract that produced it, alongside its
-- `revision_fingerprint` manifest/object-hash release identity. The join
-- is exactly one-to-one: every `int_promoted_registry_records` row's full
-- release key already matches exactly one `int_promoted_releases` row,
-- since the former is itself built by joining to the latter.

select

    promoted_records.as_of_date,
    promoted_records.release_revision,
    promoted_records.revision_fingerprint,
    promoted_releases.parser_contract_version,
    promoted_records.source_list,
    promoted_records.source_line_no,

    promoted_records.state_charity_registration_number,
    promoted_records.source_reported_registry_status,
    promoted_records.source_reported_organization_name,
    promoted_records.source_reported_city,
    promoted_records.source_reported_state,

    case
        when promoted_records.state_charity_registration_number is null
            then 'untracked_blank_registration'
        when promoted_records.source_reported_registry_status is null
            then 'excluded_from_typed_path_blank_status'
        else 'eligible_for_keyed_path'
    end as disposition

from {{ ref('int_promoted_registry_records') }} as promoted_records
inner join {{ ref('int_promoted_releases') }} as promoted_releases
    on promoted_records.as_of_date = promoted_releases.as_of_date
   and promoted_records.release_revision = promoted_releases.release_revision
   and promoted_records.revision_fingerprint = promoted_releases.revision_fingerprint
