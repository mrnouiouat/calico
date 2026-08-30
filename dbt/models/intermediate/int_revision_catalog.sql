{{
    config(
        materialized='view'
    )
}}

-- All-revision audit catalog (D-05). One row per immutable admitted
-- revision preflight verified for this build -- every accepted revision
-- reaches this relation, not only the one promoted per date. Selects
-- directly from the fixed `runtime_input.revision_catalog` source so
-- audit visibility never depends on promotion having already run
-- (D-05: audit and promotion are separately tested relations).

select
    as_of_date,
    release_revision,
    revision_fingerprint,
    parser_contract_version
from {{ source('runtime_input', 'revision_catalog') }}
