{{
    config(
        materialized='view'
    )
}}

-- Internal conditional-aggregation integration helper for Phase 5 (D-03,
-- REQ-sql-techniques) -- never a published headline mart. Groups only by
-- exact pair identity, both observation flags, the closed transition
-- class, and start/end status, with a plain count. No real-data total,
-- reconciliation figure, or claim is asserted here; Phase 5 owns the
-- actual metric/reconciliation contract built from these atomic counts.

select

    from_as_of_date,
    from_release_revision,
    from_revision_fingerprint,
    to_as_of_date,
    to_release_revision,
    to_revision_fingerprint,
    gap_days,
    observed_at_start,
    observed_at_end,
    transition_class,
    start_status,
    end_status,
    count(*) as transition_count

from {{ ref('int_entity_transitions') }}
group by
    from_as_of_date,
    from_release_revision,
    from_revision_fingerprint,
    to_as_of_date,
    to_release_revision,
    to_revision_fingerprint,
    gap_days,
    observed_at_start,
    observed_at_end,
    transition_class,
    start_status,
    end_status
