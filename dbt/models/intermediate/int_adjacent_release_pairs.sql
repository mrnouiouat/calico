{{
    config(
        materialized='view'
    )
}}

-- Positive-gap adjacent distinct-date spine (D-07). One row per
-- consecutive pair of promoted dates, ordered by `as_of_date` and derived
-- with a single `lead()` over `int_promoted_date_spine` -- never a
-- self-join on release_revision, and never a same-date pair, because the
-- spine already carries exactly one row per distinct promoted date. The
-- terminal date (no successor) is filtered out; `gap_days` is the exact
-- day distance between two distinct dates and is always strictly
-- positive across the whole timeline (enforced independently by
-- `assert_adjacent_release_pairs_positive.sql`).

with ordered as (

    select
        as_of_date as from_as_of_date,
        release_revision as from_release_revision,
        revision_fingerprint as from_revision_fingerprint,
        lead(as_of_date) over (order by as_of_date) as to_as_of_date,
        lead(release_revision) over (order by as_of_date) as to_release_revision,
        lead(revision_fingerprint) over (order by as_of_date) as to_revision_fingerprint
    from {{ ref('int_promoted_date_spine') }}

)

select
    from_as_of_date,
    from_release_revision,
    from_revision_fingerprint,
    to_as_of_date,
    to_release_revision,
    to_revision_fingerprint,
    date_diff('day', cast(from_as_of_date as date), cast(to_as_of_date as date)) as gap_days
from ordered
where to_as_of_date is not null
