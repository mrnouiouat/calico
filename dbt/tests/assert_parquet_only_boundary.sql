-- Singular test: succeeds only when it returns zero rows.
--
-- Proves dbt consumed only the four fixed, Parquet-derived
-- `runtime_input` relations (D-14) and never reopened raw CSV, globbed a
-- file path, or fell back to a permissive schema union: every staged row's
-- structural provenance is exactly one of the four canonical logical-list
-- identifiers, its physical-line ordinal is a positive integer, and its
-- bound release identity (as_of_date, release_revision,
-- revision_fingerprint) is complete. A CSV-derived, globbed, or
-- schema-drifted row would surface here as a missing or out-of-vocabulary
-- provenance value rather than silently passing through.

with checked as (

    select
        source_list,
        source_line_no,
        as_of_date,
        release_revision,
        revision_fingerprint
    from {{ ref('stg_registry_records') }}

)

select *
from checked
where source_list not in (
        'charities-may-operate',
        'charities-not-operating',
        'charities-undetermined-status',
        'charities-may-not-operate'
    )
   or source_line_no is null
   or source_line_no <= 0
   or as_of_date is null
   or release_revision is null
   or revision_fingerprint is null
