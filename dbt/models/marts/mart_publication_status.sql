{{ config(materialized='view') }}

-- Shared report banner: one latest promoted published release, never a clock.
select
    as_of_date as published_as_of_date,
    release_revision as published_release_revision
from {{ ref('int_promoted_releases') }}
qualify row_number() over (order by as_of_date desc) = 1
