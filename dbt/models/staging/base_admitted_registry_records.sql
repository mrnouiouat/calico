{{
    config(
        materialized='view'
    )
}}

-- All-revision union across the four fixed runtime sources (D-08). This is
-- an explicit `union all` over four named `source()` calls -- no glob, no
-- file path, no `union_by_name`, and no cast/trim/rename of any column.
-- Every source value, every structural provenance column
-- (`source_list`, `source_line_no`), and every bound release-identity
-- column (`as_of_date`, `release_revision`, `revision_fingerprint`) passes
-- through unchanged. Universal trimming and honest outside-in naming is
-- `stg_registry_records`'s job, not this model's (D-09).

with unioned as (

    select * from {{ source('runtime_input', 'charities_may_operate') }}
    union all
    select * from {{ source('runtime_input', 'charities_not_operating') }}
    union all
    select * from {{ source('runtime_input', 'charities_undetermined_status') }}
    union all
    select * from {{ source('runtime_input', 'charities_may_not_operate') }}

)

select * from unioned
