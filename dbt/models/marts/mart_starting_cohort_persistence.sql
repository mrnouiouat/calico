{{ config(materialized='view') }}

-- Grain: one row per actual accepted-release endpoint pair for each starting published delinquent population.
select
    from_as_of_date, from_release_revision, from_revision_fingerprint,
    to_as_of_date, to_release_revision, to_revision_fingerprint, gap_days,
    count(*) filter (where start_is_delinquent) as starting_delinquent_count,
    count(*) filter (where start_is_delinquent and observed_at_end and end_is_delinquent) as still_delinquent_count,
    count(*) filter (where start_is_delinquent and observed_at_end and not end_is_delinquent) as observed_exit_count,
    count(*) filter (where start_is_delinquent and not observed_at_end) as not_observed_count,
    'starting_published_delinquent_cohort_v1' as persistence_denominator_id,
    case when count(*) filter (where start_is_delinquent)=0 then null else
      count(*) filter (where start_is_delinquent and observed_at_end and end_is_delinquent)::double /
      count(*) filter (where start_is_delinquent) end as persistence_proportion
from {{ ref('int_entity_transitions') }}
group by from_as_of_date, from_release_revision, from_revision_fingerprint,
         to_as_of_date, to_release_revision, to_revision_fingerprint, gap_days
