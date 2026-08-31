{{
    config(
        materialized='view'
    )
}}

-- Authoritative one-row-per-durable-attempt capture-run grain (D-12/D-13).
-- Grain: one row per durable `attempt_id`.
--
-- Consumes only durable attempt records `calico_landing.attempts` already
-- validated and `calico_dbt.preflight` already bound into the fixed
-- `runtime_input.capture_attempts` relation -- never infers an attempt
-- from an accepted release. Retains every attempt: accepted,
-- no_new_release, rejected, and recovered outcomes all survive here,
-- including outcomes (rejected/no_new_release/recovered) the accepted-
-- revision history alone can never reconstruct.
--
-- `normalized_outcome` is one exhaustive `case` over the closed per-shape
-- (`attempt_shape`, `raw_status`) pairs -- SQL, not Python, decides what
-- each legacy/v2 status means analytically (D-02). Every admitted shape
-- combination is covered; an unreachable combination surfaces as null and
-- is caught by this model's own `not_null` schema test, never silently
-- coerced to a default.
--
-- `timing_state`/`duration_seconds` implement D-13 exactly: legacy v1
-- records (`attempt_shape` in ('admission_v1', 'store_v1')) are always
-- `timing_unavailable` with a null duration -- their timing was genuinely
-- never captured, and this model must never invent it. A v2 record is
-- `timing_available` only when both UTC bounds are present; its duration
-- is calculated only when the bounds also parse and are correctly ordered
-- (`ended_at_utc >= started_at_utc`), otherwise both stay null rather than
-- surfacing a negative or fabricated span.

select

    attempt_id,
    attempt_shape,
    source_schema_version,
    raw_status,

    as_of_date,
    release_revision,
    revision_fingerprint,
    reason_count,

    case
        when attempt_shape = 'admission_v1' and raw_status = 'rejected'
            then 'rejected'
        when attempt_shape = 'store_v1' and raw_status = 'accepted' and recovered = false
            then 'accepted'
        when attempt_shape = 'store_v1' and raw_status = 'accepted' and recovered = true
            then 'recovered'
        when attempt_shape = 'store_v1' and raw_status = 'no_new_release'
            then 'no_new_release'
        when attempt_shape = 'v2' and raw_status = 'accepted'
            then 'accepted'
        when attempt_shape = 'v2' and raw_status = 'no_new_release'
            then 'no_new_release'
        when attempt_shape = 'v2' and raw_status = 'rejected'
            then 'rejected'
        when attempt_shape = 'v2' and raw_status = 'recovered'
            then 'recovered'
    end as normalized_outcome,

    case
        when attempt_shape = 'v2' and started_at_utc is not null and ended_at_utc is not null
            then 'timing_available'
        else 'timing_unavailable'
    end as timing_state,

    started_at_utc,
    ended_at_utc,

    case
        when attempt_shape = 'v2'
             and started_at_utc is not null
             and ended_at_utc is not null
             and try_cast(ended_at_utc as timestamp) >= try_cast(started_at_utc as timestamp)
            then date_diff(
                'second',
                try_cast(started_at_utc as timestamp),
                try_cast(ended_at_utc as timestamp)
            )
    end as duration_seconds

from {{ ref('stg_capture_attempts') }}
