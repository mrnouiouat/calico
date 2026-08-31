{{
    config(
        materialized='view'
    )
}}

-- Authoritative keyed-snapshot grain (D-04). Exactly one row per promoted
-- full release identity plus the exact full nonblank State Charity
-- registration number, selected only where Phase 3's one exhaustive
-- disposition `case` classified the row `eligible_for_keyed_path` --
-- both the registration and the status are nonblank. Keyless rows
-- (`untracked_blank_registration`) and blank-status typed-path exclusions
-- (`excluded_from_typed_path_blank_status`) never enter this relation;
-- they remain auditable in `int_unkeyed_coverage` and
-- `int_registry_record_exclusions` respectively. All three closed classes
-- reconcile exactly back to `int_promoted_registry_records`, both
-- globally and by full release identity/source list/source-reported
-- status (`assert_keyed_snapshot_reconciliation.sql`).
--
-- `is_delinquent` is derived here, once, directly from the two exact
-- locked D-06 statuses -- never widened to a substring/heuristic match
-- and never recomputed downstream.
--
-- The join to `int_promoted_registry_records` is exactly one-to-one by
-- construction (every disposition row's full row identity -- release key
-- plus source_list/source_line_no -- already came from exactly one
-- `int_promoted_registry_records` row) and exists only to carry forward
-- the two source-reported diagnostic date fields (D-07) that the
-- disposition relation itself does not select; it never re-filters or
-- widens the disposition-first row set.

select

    dispositions.as_of_date,
    dispositions.release_revision,
    dispositions.revision_fingerprint,
    dispositions.parser_contract_version,
    dispositions.source_list,
    dispositions.source_line_no,

    dispositions.state_charity_registration_number,
    dispositions.source_reported_registry_status,
    dispositions.source_reported_organization_name,
    dispositions.source_reported_city,
    dispositions.source_reported_state,

    dispositions.source_reported_registry_status in (
        'Delinquent', 'Delinquent - Late Fees Due'
    ) as is_delinquent,

    -- Source-reported diagnostic attributes only (D-07): neither date is
    -- an onset, exit, filing, or accepted-submission timestamp, and
    -- neither may drive transition classification downstream.
    promoted_records.source_reported_last_renewal_date,
    promoted_records.source_reported_last_renewal_date_nonblank_unparseable,
    promoted_records.source_reported_current_status_date,
    promoted_records.source_reported_current_status_date_nonblank_unparseable

from {{ ref('int_registry_record_dispositions') }} as dispositions
inner join {{ ref('int_promoted_registry_records') }} as promoted_records
    on dispositions.as_of_date = promoted_records.as_of_date
   and dispositions.release_revision = promoted_records.release_revision
   and dispositions.revision_fingerprint = promoted_records.revision_fingerprint
   and dispositions.source_list = promoted_records.source_list
   and dispositions.source_line_no = promoted_records.source_line_no
where dispositions.disposition = 'eligible_for_keyed_path'
