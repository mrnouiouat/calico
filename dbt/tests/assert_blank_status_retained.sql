-- Singular test: succeeds only when it returns zero rows.
--
-- Universal, vacuously-true-when-absent rule (D-01/D-03/D-04/D-11): every
-- row in `int_promoted_registry_records` whose trimmed Registry Status is
-- blank must be disposed as `excluded_from_typed_path_blank_status` in
-- `int_registry_record_dispositions`, joined on the full row identity so
-- lineage is proven to carry through. Written as a predicate over the
-- model's own promoted rows -- never a fixture-specific presence
-- assertion -- so it passes unchanged whether zero such rows exist (real
-- mode; constraints.md C-005 records 33 nonblank status values and none
-- are blank) or one exists (fixture mode's synthetic blank-status row).
-- The fixture row exercises this rule in CI; it is never hardcoded or
-- required to exist for the test itself to pass.
--
-- The only failure surfaced is a safe source_list/source_line_no
-- identifier -- never an excluded source value or a full raw row.

select
    'blank_status_not_disposed_as_excluded:' || promoted.source_list || ':' || promoted.source_line_no
        as failure_reason
from {{ ref('int_promoted_registry_records') }} as promoted
inner join {{ ref('int_registry_record_dispositions') }} as disposed
    on promoted.as_of_date = disposed.as_of_date
   and promoted.release_revision = disposed.release_revision
   and promoted.revision_fingerprint = disposed.revision_fingerprint
   and promoted.source_list = disposed.source_list
   and promoted.source_line_no = disposed.source_line_no
where promoted.source_reported_registry_status is null
  and disposed.disposition <> 'excluded_from_typed_path_blank_status'
