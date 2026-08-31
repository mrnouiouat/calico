{{
    config(
        materialized='view'
    )
}}

-- Authoritative bounded baseline aggregate-report grain (D-15,
-- REQ-model-grains). Grain: one row per accepted release identity, source
-- list, source-reported status, and keyed-versus-unkeyed coverage class.
-- Grouped directly from int_registry_record_dispositions -- the same
-- exhaustive relation assert_registry_record_disposition_reconciliation.sql
-- already independently proves covers every promoted record exactly once --
-- so this aggregate's own sum(record_count) reconciles to the total
-- promoted row count by construction
-- (assert_population_coverage_reconciliation.sql independently re-proves
-- this).
--
-- Deliberately contains no organization key, name, city, or any other
-- drillthrough field: this is the one bounded low-cardinality aggregate
-- Phase 4 owns (D-15). Phase 5 adds its own metric-specific marts rather
-- than overloading or silently redefining this baseline.

select

    as_of_date,
    release_revision,
    revision_fingerprint,
    source_list,
    source_reported_registry_status,

    case
        when disposition = 'eligible_for_keyed_path' then 'keyed'
        else 'unkeyed'
    end as coverage_class,

    count(*) as record_count

from {{ ref('int_registry_record_dispositions') }}

group by
    as_of_date,
    release_revision,
    revision_fingerprint,
    source_list,
    source_reported_registry_status,
    case
        when disposition = 'eligible_for_keyed_path' then 'keyed'
        else 'unkeyed'
    end
