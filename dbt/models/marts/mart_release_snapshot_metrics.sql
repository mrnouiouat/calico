{{ config(materialized='view') }}

-- Grain: one row per accepted release and locked published delinquent category.
with releases as (
    select * from {{ ref('int_promoted_releases') }}
), categories(category) as (
    values ('Delinquent'), ('Delinquent - Late Fees Due')
), counts as (
    select
        as_of_date, release_revision, revision_fingerprint,
        count(*) as raw_total_record_count,
        count(*) filter (where disposition = 'eligible_for_keyed_path') as keyed_record_count,
        count(*) filter (where disposition <> 'eligible_for_keyed_path') as unkeyed_record_count
    from {{ ref('int_registry_record_dispositions') }}
    group by as_of_date, release_revision, revision_fingerprint
), delinquent as (
    select as_of_date, release_revision, revision_fingerprint,
           source_reported_registry_status as published_delinquent_category,
           count(*) as published_delinquent_category_count
    from {{ ref('int_registry_record_dispositions') }}
    where source_reported_registry_status in ('Delinquent', 'Delinquent - Late Fees Due')
    group by as_of_date, release_revision, revision_fingerprint, source_reported_registry_status
)
select
    r.as_of_date, r.release_revision, r.revision_fingerprint, r.parser_contract_version,
    c.category as published_delinquent_category,
    coalesce(d.published_delinquent_category_count, 0) as published_delinquent_category_count,
    coalesce(n.keyed_record_count, 0) as keyed_record_count,
    coalesce(n.unkeyed_record_count, 0) as unkeyed_record_count,
    coalesce(n.raw_total_record_count, 0) as raw_total_record_count,
    'all_promoted_release_records_v1' as keyed_coverage_denominator_id,
    'all_promoted_release_records_v1' as unkeyed_coverage_denominator_id,
    case when n.raw_total_record_count = 0 then null else n.keyed_record_count::double / n.raw_total_record_count end as keyed_coverage_proportion,
    {{ wilson_interval('n.keyed_record_count', 'n.raw_total_record_count', 'lower') }} as keyed_coverage_wilson_95_lower,
    {{ wilson_interval('n.keyed_record_count', 'n.raw_total_record_count', 'upper') }} as keyed_coverage_wilson_95_upper,
    case when n.raw_total_record_count = 0 then null else n.unkeyed_record_count::double / n.raw_total_record_count end as unkeyed_coverage_proportion,
    {{ wilson_interval('n.unkeyed_record_count', 'n.raw_total_record_count', 'lower') }} as unkeyed_coverage_wilson_95_lower,
    {{ wilson_interval('n.unkeyed_record_count', 'n.raw_total_record_count', 'upper') }} as unkeyed_coverage_wilson_95_upper
from releases r cross join categories c
left join counts n using (as_of_date, release_revision, revision_fingerprint)
left join delinquent d on d.as_of_date=r.as_of_date and d.release_revision=r.release_revision
 and d.revision_fingerprint=r.revision_fingerprint and d.published_delinquent_category=c.category
