{{ config(materialized='view') }}

-- Grain: one row per accepted release; descriptive release-quality diagnostics, never a score or rank.
with releases as (select * from {{ ref('int_promoted_releases') }}),
flags as (
 select as_of_date, release_revision, revision_fingerprint,
   count(*) as release_flag_count,
   count(*) filter (where result='pass') as release_flag_pass_count,
   count(*) filter (where result='review') as release_flag_review_count,
   string_agg(distinct rule_id || ':' || rule_version::varchar || ':' || parameter_version, ',' order by rule_id || ':' || rule_version::varchar || ':' || parameter_version) as release_flag_rule_versions
 from {{ ref('int_release_flags') }} where scope='release'
 group by as_of_date, release_revision, revision_fingerprint
), captures as (
 select as_of_date, release_revision, revision_fingerprint,
  count(*) as capture_attempted_count,
  count(*) filter (where normalized_outcome in ('accepted','recovered')) as capture_succeeded_count,
  count(*) filter (where normalized_outcome='rejected') as capture_failed_count,
  count(*) filter (where normalized_outcome='no_new_release') as capture_unavailable_count
 from {{ ref('int_capture_runs') }}
 group by as_of_date, release_revision, revision_fingerprint
)
select r.as_of_date, r.release_revision, r.revision_fingerprint, r.parser_contract_version,
 case when coalesce(f.release_flag_review_count,0)=0 then 'accepted_healthy' else 'accepted_review' end as release_health_state,
 coalesce(f.release_flag_count,0) as release_flag_count, coalesce(f.release_flag_pass_count,0) as release_flag_pass_count,
 coalesce(f.release_flag_review_count,0) as release_flag_review_count, f.release_flag_rule_versions,
 0 as schema_added_column_count, 0 as schema_removed_column_count, 0 as schema_type_changed_column_count,
 'registry-csv-contract-v1' as schema_contract_version,
 coalesce(c.capture_attempted_count,0) as capture_attempted_count, coalesce(c.capture_succeeded_count,0) as capture_succeeded_count,
 coalesce(c.capture_failed_count,0) as capture_failed_count, coalesce(c.capture_unavailable_count,0) as capture_unavailable_count
from releases r left join flags f using(as_of_date,release_revision,revision_fingerprint)
left join captures c using(as_of_date,release_revision,revision_fingerprint)
