-- Singular test: succeeds only when it returns zero rows.
--
-- Within-build locality invariant (D-07): every pair-side release
-- identity in `int_adjacent_release_pairs` must be the current promoted
-- winner for that exact date in `int_promoted_releases` -- never a stale
-- or nonpromoted revision. This is the within-build half of same-date
-- pointer-switch locality; the cross-build half (only the two pair-side
-- fingerprints touching a replaced middle date actually change) is
-- proven by `tests/dbt_foundation/test_promotion.py`'s two-build
-- comparison, never inferred from a single dbt invocation here.

with from_side_mismatches as (

    select
        'from_side_not_promoted_winner:' || pairs.from_as_of_date as failure_reason
    from {{ ref('int_adjacent_release_pairs') }} as pairs
    inner join {{ ref('int_promoted_releases') }} as promoted
        on pairs.from_as_of_date = promoted.as_of_date
    where pairs.from_release_revision <> promoted.release_revision
       or pairs.from_revision_fingerprint <> promoted.revision_fingerprint

),

to_side_mismatches as (

    select
        'to_side_not_promoted_winner:' || pairs.to_as_of_date as failure_reason
    from {{ ref('int_adjacent_release_pairs') }} as pairs
    inner join {{ ref('int_promoted_releases') }} as promoted
        on pairs.to_as_of_date = promoted.as_of_date
    where pairs.to_release_revision <> promoted.release_revision
       or pairs.to_revision_fingerprint <> promoted.revision_fingerprint

)

select failure_reason from from_side_mismatches
union all
select failure_reason from to_side_mismatches
