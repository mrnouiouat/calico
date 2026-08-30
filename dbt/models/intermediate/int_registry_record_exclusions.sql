{{
    config(
        materialized='view'
    )
}}

-- Auditable stable-reason typed-path exclusions (D-11). A convenience
-- selection over the single total disposition relation -- never an
-- independent filter -- covering every closed `excluded_from_typed_path_*`
-- reason. Every row here carries full source and release lineage plus its
-- stable exclusion reason; counts reconcile exactly back to staging via
-- `assert_registry_record_disposition_reconciliation.sql`, so a filter can
-- never make a source record silently disappear (D-11).

select *
from {{ ref('int_registry_record_dispositions') }}
where disposition like 'excluded_from_typed_path_%'
