-- Singular test: succeeds only when it returns zero rows.
--
-- Independently proves D4/D-10 exact-identity retention on
-- `int_registry_record_dispositions`, joined back to
-- `int_promoted_registry_records` on the full row identity -- never
-- trusting the disposition model's own passthrough:
--
-- 1. Exact retention: every disposition row's
--    `state_charity_registration_number` equals its promoted source row's
--    trimmed value exactly -- no truncation, no substring, no
--    re-derivation.
-- 2. No FEIN fallback: for every row whose promoted registration is blank
--    (null), the disposition's registration stays null even when that same
--    row's federal identifier is nonblank -- proving the identity column
--    is never coalesced onto the excluded FEIN column.
-- 3. No accidental FEIN aliasing: for every row whose disposition
--    registration is nonblank, it never equals that same row's trimmed
--    FEIN value, which would indicate the excluded federal identifier
--    leaking into longitudinal identity.
--
-- Every failure surfaces only a safe source_list/source_line_no
-- identifier -- never the registration or FEIN value itself.

with joined as (

    select
        disposed.source_list,
        disposed.source_line_no,
        disposed.state_charity_registration_number as disposed_registration,
        promoted.state_charity_registration_number as promoted_registration,
        promoted.source_reported_federal_employer_identification_number as promoted_fein
    from {{ ref('int_registry_record_dispositions') }} as disposed
    inner join {{ ref('int_promoted_registry_records') }} as promoted
        on disposed.as_of_date = promoted.as_of_date
       and disposed.release_revision = promoted.release_revision
       and disposed.revision_fingerprint = promoted.revision_fingerprint
       and disposed.source_list = promoted.source_list
       and disposed.source_line_no = promoted.source_line_no

),

retention_failures as (

    select 'registration_not_exactly_retained:' || source_list || ':' || source_line_no as failure_reason
    from joined
    where disposed_registration is distinct from promoted_registration

),

fein_fallback_failures as (

    select 'registration_backfilled_from_fein:' || source_list || ':' || source_line_no as failure_reason
    from joined
    where promoted_registration is null
      and disposed_registration is not null

),

fein_aliasing_failures as (

    select 'registration_equals_fein:' || source_list || ':' || source_line_no as failure_reason
    from joined
    where disposed_registration is not null
      and promoted_fein is not null
      and disposed_registration = promoted_fein

)

select failure_reason from retention_failures
union all
select failure_reason from fein_fallback_failures
union all
select failure_reason from fein_aliasing_failures
