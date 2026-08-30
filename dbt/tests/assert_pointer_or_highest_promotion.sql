-- Singular test: succeeds only when it returns zero rows.
--
-- Independently enforces D-06 pointer authority and highest-revision
-- fallback, never trusting `int_promoted_releases`'s own selection logic:
--
-- 1. Every `runtime_input.promotion_catalog` row (a present pointer) must
--    join to exactly one `runtime_input.revision_catalog` row on the full
--    (as_of_date, release_revision, revision_fingerprint) key. A pointer
--    that disagrees with the revision source fails the build outright --
--    it is never silently treated as absent and never falls back to the
--    highest revision.
-- 2. For every date with a present pointer, `int_promoted_releases` must
--    have selected exactly that pointer's full release identity.
-- 3. For every date with no pointer entry, `int_promoted_releases` must
--    have selected the highest accepted revision for that date.

with pointer_join_failures as (

    select
        'pointer_does_not_join_exactly_once:' || p.as_of_date as failure_reason
    from {{ source('runtime_input', 'promotion_catalog') }} as p
    left join {{ source('runtime_input', 'revision_catalog') }} as r
        on r.as_of_date = p.as_of_date
       and r.release_revision = p.release_revision
       and r.revision_fingerprint = p.revision_fingerprint
    group by p.as_of_date, p.release_revision, p.revision_fingerprint
    having count(r.release_revision) <> 1

),

pointer_winner_mismatches as (

    select
        'pointer_winner_mismatch:' || p.as_of_date as failure_reason
    from {{ source('runtime_input', 'promotion_catalog') }} as p
    inner join {{ ref('int_promoted_releases') }} as promoted
        on promoted.as_of_date = p.as_of_date
    where promoted.release_revision <> p.release_revision
       or promoted.revision_fingerprint <> p.revision_fingerprint

),

max_revision_by_date as (

    select
        as_of_date,
        max(release_revision) as max_release_revision
    from {{ source('runtime_input', 'revision_catalog') }}
    group by as_of_date

),

no_pointer_fallback_mismatches as (

    select
        'no_pointer_fallback_mismatch:' || m.as_of_date as failure_reason
    from max_revision_by_date as m
    inner join {{ ref('int_promoted_releases') }} as promoted
        on promoted.as_of_date = m.as_of_date
    where m.as_of_date not in (
            select as_of_date from {{ source('runtime_input', 'promotion_catalog') }}
        )
      and promoted.release_revision <> m.max_release_revision

)

select failure_reason from pointer_join_failures
union all
select failure_reason from pointer_winner_mismatches
union all
select failure_reason from no_pointer_fallback_mismatches
