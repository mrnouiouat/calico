-- Singular test: succeeds only when it returns zero rows.
--
-- Independently recomputes the full endpoint-union identity (D-05) from
-- `int_adjacent_release_pairs` joined to `int_keyed_snapshots` at each
-- endpoint, without ever trusting `int_entity_transitions`'s own three
-- union branches to have covered every case:
--
-- 1. Completeness/no-invention: every (pair, exact key) observed at the
--    start, the end, or both appears in `int_entity_transitions` exactly
--    once -- never zero times (a dropped observation) and never more than
--    once (a duplicated row), and no row exists in the fact that this
--    independent recomputation does not also expect.
-- 2. No both-absent row: no `int_entity_transitions` row ever has both
--    `observed_at_start` and `observed_at_end` false -- a row only exists
--    because the key was observed at least once.
--
-- Every failure surfaces only a safe pair-date/registration-key identifier
-- -- never an excluded value or an unrelated raw column.

with start_keys as (

    select
        pairs.from_as_of_date, pairs.from_release_revision, pairs.from_revision_fingerprint,
        pairs.to_as_of_date, pairs.to_release_revision, pairs.to_revision_fingerprint,
        snapshot.state_charity_registration_number
    from {{ ref('int_adjacent_release_pairs') }} as pairs
    inner join {{ ref('int_keyed_snapshots') }} as snapshot
        on snapshot.as_of_date = pairs.from_as_of_date
       and snapshot.release_revision = pairs.from_release_revision
       and snapshot.revision_fingerprint = pairs.from_revision_fingerprint

),

end_keys as (

    select
        pairs.from_as_of_date, pairs.from_release_revision, pairs.from_revision_fingerprint,
        pairs.to_as_of_date, pairs.to_release_revision, pairs.to_revision_fingerprint,
        snapshot.state_charity_registration_number
    from {{ ref('int_adjacent_release_pairs') }} as pairs
    inner join {{ ref('int_keyed_snapshots') }} as snapshot
        on snapshot.as_of_date = pairs.to_as_of_date
       and snapshot.release_revision = pairs.to_release_revision
       and snapshot.revision_fingerprint = pairs.to_revision_fingerprint

),

expected_keys as (

    select distinct * from start_keys
    union
    select distinct * from end_keys

),

actual_keys as (

    select
        from_as_of_date, from_release_revision, from_revision_fingerprint,
        to_as_of_date, to_release_revision, to_revision_fingerprint,
        state_charity_registration_number
    from {{ ref('int_entity_transitions') }}

),

completeness_failures as (

    select
        'endpoint_union_mismatch:'
            || coalesce(expected.from_as_of_date, actual.from_as_of_date)
            || ':' || coalesce(expected.to_as_of_date, actual.to_as_of_date) as failure_reason
    from expected_keys as expected
    full outer join actual_keys as actual
        on expected.from_as_of_date = actual.from_as_of_date
       and expected.from_release_revision = actual.from_release_revision
       and expected.from_revision_fingerprint = actual.from_revision_fingerprint
       and expected.to_as_of_date = actual.to_as_of_date
       and expected.to_release_revision = actual.to_release_revision
       and expected.to_revision_fingerprint = actual.to_revision_fingerprint
       and expected.state_charity_registration_number = actual.state_charity_registration_number
    where expected.from_as_of_date is null
       or actual.from_as_of_date is null

),

duplicate_row_failures as (

    select
        'duplicate_transition_row:' || from_as_of_date || ':' || to_as_of_date as failure_reason
    from actual_keys
    group by
        from_as_of_date, from_release_revision, from_revision_fingerprint,
        to_as_of_date, to_release_revision, to_revision_fingerprint,
        state_charity_registration_number
    having count(*) > 1

),

both_absent_failures as (

    select
        'both_observation_flags_false:' || from_as_of_date || ':' || to_as_of_date as failure_reason
    from {{ ref('int_entity_transitions') }}
    where not observed_at_start and not observed_at_end

)

select failure_reason from completeness_failures
union all
select failure_reason from duplicate_row_failures
union all
select failure_reason from both_absent_failures
