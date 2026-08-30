-- Singular test: succeeds only when it returns zero rows.
--
-- Within-build positive-gap invariant (D-07): every adjacent pair's
-- `from_as_of_date` must precede its `to_as_of_date`, and `gap_days` must
-- be strictly positive. A same-date or reversed pair is never valid --
-- the adjacency spine compares distinct promoted dates only.

select
    'non_positive_or_reversed_gap:' || from_as_of_date as failure_reason
from {{ ref('int_adjacent_release_pairs') }}
where from_as_of_date >= to_as_of_date
   or gap_days <= 0
