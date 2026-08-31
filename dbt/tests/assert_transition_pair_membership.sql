-- Singular test: succeeds only when it returns zero rows.
--
-- Independently proves every distinct pair identity present in
-- `int_entity_transitions` is a real, exact member of the delivered
-- `int_adjacent_release_pairs` spine (D-05) -- never an invented pair,
-- never a stale/nonpromoted revision on either side, and never a
-- mismatched `gap_days` value silently carried through the transition
-- build. This is the pair-locality half of D-05; the endpoint-completeness
-- half is proven independently by `assert_transition_endpoint_union.sql`.

with distinct_transition_pairs as (

    select distinct
        from_as_of_date, from_release_revision, from_revision_fingerprint,
        to_as_of_date, to_release_revision, to_revision_fingerprint,
        gap_days
    from {{ ref('int_entity_transitions') }}

),

pair_identity_mismatches as (

    select
        'transition_pair_not_in_adjacent_pairs:'
            || coalesce(transitions.from_as_of_date, pairs.from_as_of_date)
            || ':' || coalesce(transitions.to_as_of_date, pairs.to_as_of_date) as failure_reason
    from distinct_transition_pairs as transitions
    full outer join {{ ref('int_adjacent_release_pairs') }} as pairs
        on transitions.from_as_of_date = pairs.from_as_of_date
       and transitions.from_release_revision = pairs.from_release_revision
       and transitions.from_revision_fingerprint = pairs.from_revision_fingerprint
       and transitions.to_as_of_date = pairs.to_as_of_date
       and transitions.to_release_revision = pairs.to_release_revision
       and transitions.to_revision_fingerprint = pairs.to_revision_fingerprint
    where transitions.from_as_of_date is null

),

gap_days_mismatches as (

    select
        'transition_pair_gap_days_mismatch:' || transitions.from_as_of_date as failure_reason
    from distinct_transition_pairs as transitions
    inner join {{ ref('int_adjacent_release_pairs') }} as pairs
        on transitions.from_as_of_date = pairs.from_as_of_date
       and transitions.from_release_revision = pairs.from_release_revision
       and transitions.from_revision_fingerprint = pairs.from_revision_fingerprint
       and transitions.to_as_of_date = pairs.to_as_of_date
       and transitions.to_release_revision = pairs.to_release_revision
       and transitions.to_revision_fingerprint = pairs.to_revision_fingerprint
    where transitions.gap_days <> pairs.gap_days

)

select failure_reason from pair_identity_mismatches
union all
select failure_reason from gap_days_mismatches
