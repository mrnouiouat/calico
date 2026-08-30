-- Singular test: succeeds only when it returns zero rows.
--
-- Three closed checks over `stg_registry_records`, each contributing a
-- `failure_reason` row on violation:
--
-- 1. Reconciliation (D-08/D-09): every base row has exactly one staged
--    counterpart and vice versa (no row silently dropped or duplicated),
--    release identity/source_list/source_line_no pass through completely
--    unchanged, and every checked source string in staging equals
--    `nullif(trim(source), '')` -- never a substring, never a coalesce with
--    another column. This also proves the exact full trimmed State Charity
--    registration number is retained and never silently backfilled from
--    FEIN (D-10): if a coalesce fallback existed, the equality against the
--    source registration field alone would fail here.
-- 2. Required names (D-09/D-13): `source_reported_last_renewal_date` and
--    `source_reported_current_status_date` must exist on the compiled
--    relation.
-- 3. Forbidden aliases (D-09/D-13): no column name implies
--    accepted-submission or exact-onset semantics.

with reconciliation_failures as (

    select
        'reconciliation_mismatch' as failure_reason
    from {{ ref('base_admitted_registry_records') }} as base
    full outer join {{ ref('stg_registry_records') }} as stg
        on base.source_list = stg.source_list
       and base.source_line_no = stg.source_line_no
       and base.as_of_date = stg.as_of_date
       and base.release_revision = stg.release_revision
       and base.revision_fingerprint = stg.revision_fingerprint
    where base.source_list is null
       or stg.source_list is null
       or base.source_list is distinct from stg.source_list
       or base.source_line_no is distinct from stg.source_line_no
       or base.as_of_date is distinct from stg.as_of_date
       or base.release_revision is distinct from stg.release_revision
       or base.revision_fingerprint is distinct from stg.revision_fingerprint
       or nullif(trim(base."Registry Status"), '') is distinct from stg.source_reported_registry_status
       or nullif(trim(base."State Charity Reg#"), '') is distinct from stg.state_charity_registration_number
       or nullif(trim(base."Name"), '') is distinct from stg.source_reported_organization_name
       or nullif(trim(base."City"), '') is distinct from stg.source_reported_city
       or nullif(trim(base."State"), '') is distinct from stg.source_reported_state
       or nullif(trim(base."SOS/FTB#"), '') is distinct from stg.source_reported_sos_or_ftb_number
       or nullif(trim(base."FEIN"), '') is distinct from stg.source_reported_federal_employer_identification_number

),

required_names_missing as (

    select 'missing_required_name:' || required.name as failure_reason
    from (
        select 'source_reported_last_renewal_date' as name
        union all
        select 'source_reported_current_status_date' as name
    ) as required
    where not exists (
        select 1 from information_schema.columns
        where table_name = 'stg_registry_records'
          and column_name = required.name
    )

),

forbidden_aliases_present as (

    select 'forbidden_alias:' || column_name as failure_reason
    from information_schema.columns
    where table_name = 'stg_registry_records'
      and (
            lower(column_name) like '%accepted_submission%'
         or lower(column_name) like '%accepted%submission%'
         or lower(column_name) like '%exact_onset%'
         or lower(column_name) like '%onset_date%'
      )

)

select failure_reason from reconciliation_failures
union all
select failure_reason from required_names_missing
union all
select failure_reason from forbidden_aliases_present
