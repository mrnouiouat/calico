{{ config(materialized='view') }}

-- Grain: one row per accepted release and source-reported prevalent-snapshot age bucket; never duration or onset.
select
    as_of_date, release_revision, revision_fingerprint,
    case
      when source_reported_current_status_date_nonblank_unparseable then 'invalid_nonblank'
      when source_reported_current_status_date is null then 'missing'
      when source_reported_current_status_date > cast(as_of_date as date) then 'future_reported_date'
      else 'valid_reported_date'
    end as source_reported_status_age_state,
    case when source_reported_current_status_date <= cast(as_of_date as date)
      then date_diff('day', source_reported_current_status_date, cast(as_of_date as date)) end as source_reported_status_age_days,
    count(*) as record_count
from {{ ref('int_keyed_snapshots') }}
group by as_of_date, release_revision, revision_fingerprint,
    source_reported_status_age_state, source_reported_status_age_days
