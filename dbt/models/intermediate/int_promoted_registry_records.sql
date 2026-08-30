{{
    config(
        materialized='view'
    )
}}

-- Promoted staging records (D-05). `stg_registry_records` rows restricted
-- to the one promoted revision per `as_of_date`, joined on the complete
-- (as_of_date, release_revision, revision_fingerprint) key -- never on
-- `as_of_date` alone -- so a nonpromoted same-date revision's rows never
-- leak in through a partial match.

select
    stg.*
from {{ ref('stg_registry_records') }} as stg
inner join {{ ref('int_promoted_releases') }} as promoted
    on stg.as_of_date = promoted.as_of_date
   and stg.release_revision = promoted.release_revision
   and stg.revision_fingerprint = promoted.revision_fingerprint
