-- Singular test: succeeds only when it returns zero rows.
--
-- Independently reconciles `int_capture_runs` against the raw
-- `runtime_input.capture_attempts` source (T-04-04C, D-12/D-13), without
-- ever trusting `int_capture_runs`'s own `case` to have classified every
-- row correctly:
--
-- 1. Row-identity reconciliation: every source `attempt_id` appears in
--    `int_capture_runs` exactly once -- never zero times (silently
--    dropped) and never more than once (duplicated).
-- 2. Independently recomputed `normalized_outcome` matches the model's own
--    value for every row.
-- 3. Independently recomputed `timing_state`/`duration_seconds` matches: a
--    legacy v1 record is always `timing_unavailable` with a null
--    duration; a v2 record's duration is present only when both bounds
--    parse and are correctly ordered.
-- 4. Closed vocabulary: `normalized_outcome`/`timing_state` are always one
--    of the four/two closed values -- never anything else.
--
-- Every failure surfaces only a safe `attempt_id` -- never a raw document
-- or excluded value.

with source_rows as (

    select
        attempt_id,
        attempt_shape,
        raw_status,
        recovered,
        started_at_utc,
        ended_at_utc
    from {{ source('runtime_input', 'capture_attempts') }}

),

expected as (

    select
        attempt_id,
        case
            when attempt_shape = 'admission_v1' and raw_status = 'rejected' then 'rejected'
            when attempt_shape = 'store_v1' and raw_status = 'accepted' and recovered = false then 'accepted'
            when attempt_shape = 'store_v1' and raw_status = 'accepted' and recovered = true then 'recovered'
            when attempt_shape = 'store_v1' and raw_status = 'no_new_release' then 'no_new_release'
            when attempt_shape = 'v2' and raw_status = 'accepted' then 'accepted'
            when attempt_shape = 'v2' and raw_status = 'no_new_release' then 'no_new_release'
            when attempt_shape = 'v2' and raw_status = 'rejected' then 'rejected'
            when attempt_shape = 'v2' and raw_status = 'recovered' then 'recovered'
        end as expected_normalized_outcome,
        case
            when attempt_shape = 'v2' and started_at_utc is not null and ended_at_utc is not null
                then 'timing_available'
            else 'timing_unavailable'
        end as expected_timing_state,
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
        end as expected_duration_seconds
    from source_rows

),

modeled as (

    select
        attempt_id,
        normalized_outcome,
        timing_state,
        duration_seconds
    from {{ ref('int_capture_runs') }}

),

identity_reconciliation_failures as (

    select
        'identity_reconciliation_mismatch:' || coalesce(expected.attempt_id, modeled.attempt_id) as failure_reason
    from expected
    full outer join modeled on expected.attempt_id = modeled.attempt_id
    where expected.attempt_id is null or modeled.attempt_id is null

),

duplicate_attempt_failures as (

    select 'duplicate_attempt_id:' || attempt_id as failure_reason
    from modeled
    group by attempt_id
    having count(*) > 1

),

outcome_mismatch_failures as (

    select 'normalized_outcome_mismatch:' || expected.attempt_id as failure_reason
    from expected
    inner join modeled on expected.attempt_id = modeled.attempt_id
    where expected.expected_normalized_outcome is distinct from modeled.normalized_outcome

),

timing_mismatch_failures as (

    select 'timing_state_mismatch:' || expected.attempt_id as failure_reason
    from expected
    inner join modeled on expected.attempt_id = modeled.attempt_id
    where expected.expected_timing_state is distinct from modeled.timing_state
       or expected.expected_duration_seconds is distinct from modeled.duration_seconds

),

closed_vocabulary_failures as (

    select 'unclosed_normalized_outcome_or_timing_state:' || attempt_id as failure_reason
    from modeled
    where normalized_outcome not in ('accepted', 'no_new_release', 'rejected', 'recovered')
       or timing_state not in ('timing_available', 'timing_unavailable')

)

select failure_reason from identity_reconciliation_failures
union all
select failure_reason from duplicate_attempt_failures
union all
select failure_reason from outcome_mismatch_failures
union all
select failure_reason from timing_mismatch_failures
union all
select failure_reason from closed_vocabulary_failures
