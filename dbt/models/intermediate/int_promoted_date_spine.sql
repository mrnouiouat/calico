{{
    config(
        materialized='view'
    )
}}

-- Promoted-date spine (D-07). One row per promoted `as_of_date`, carrying
-- that date's winning full release identity. This is `int_promoted_releases`
-- itself, named separately so adjacency has one clearly named upstream
-- timeline relation to `lead()` over rather than reaching past promotion
-- into raw revisions.

select
    as_of_date,
    release_revision,
    revision_fingerprint
from {{ ref('int_promoted_releases') }}
