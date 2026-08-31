{{
    config(
        materialized='view'
    )
}}

-- Structural pass-through of the fixed nullable `runtime_input.capture_attempts`
-- relation (D-02/D-12/D-13). Grain: one row per durable attempt JSON file
-- `calico_dbt.preflight` validated and parameter-bound.
--
-- Casts/passes through only the safe structural fields
-- `calico_landing.attempts` already validated and `calico_dbt.preflight`
-- already normalized from its three closed document shapes -- no outcome
-- normalization, timing-state derivation, or duration calculation happens
-- here; that is exclusively `int_capture_runs.sql`'s job (D-02).
-- `attempt_shape` and `source_schema_version` are retained unchanged so
-- `int_capture_runs.sql` can apply the exact per-shape closed status
-- vocabulary independently, never guessing the originating shape back out
-- of which columns happen to be null.

select
    schema_version as source_schema_version,
    attempt_shape,
    attempt_id,
    raw_status,
    as_of_date,
    release_revision,
    revision_fingerprint,
    reason_count,
    recovered,
    started_at_utc,
    ended_at_utc
from {{ source('runtime_input', 'capture_attempts') }}
