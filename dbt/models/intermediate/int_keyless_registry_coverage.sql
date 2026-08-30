{{
    config(
        materialized='view'
    )
}}

-- Auditable untracked blank-registration coverage (D-10). A convenience
-- selection over the single total disposition relation -- never an
-- independent filter reimplementing blank-registration logic -- so this
-- relation can never drift from `int_registry_record_dispositions`'s one
-- exhaustive `case`. Every row here carries full source and release
-- lineage; none is ever dropped, substring-matched, or coalesced onto the
-- excluded federal identifier (D-10).

select *
from {{ ref('int_registry_record_dispositions') }}
where disposition = 'untracked_blank_registration'
