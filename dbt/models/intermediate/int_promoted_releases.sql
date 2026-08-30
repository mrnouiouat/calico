{{
    config(
        materialized='view'
    )
}}

-- Pointer-authoritative promotion with highest-revision fallback (D-06).
-- Exactly one row per `as_of_date`: the pointer target when
-- `runtime_input.promotion_catalog` carries an entry for that date, or
-- otherwise the highest accepted revision. Selects directly from the two
-- fixed `runtime_input` sources -- never through `int_revision_catalog`
-- -- so this model's own standalone selection never depends on any other
-- dbt model having already been built.
--
-- Full-key (as_of_date, release_revision, revision_fingerprint) agreement
-- between a present pointer entry and the revision source is
-- independently enforced by `assert_pointer_or_highest_promotion.sql`;
-- this model only selects the winner, it never validates pointer/catalog
-- consistency itself.

with ranked as (

    select
        r.as_of_date,
        r.release_revision,
        r.revision_fingerprint,
        r.parser_contract_version,
        row_number() over (
            partition by r.as_of_date
            order by (p.release_revision is not null) desc, r.release_revision desc
        ) as promotion_rank
    from {{ source('runtime_input', 'revision_catalog') }} r
    left join {{ source('runtime_input', 'promotion_catalog') }} p
      on r.as_of_date = p.as_of_date
     and r.release_revision = p.release_revision
     and r.revision_fingerprint = p.revision_fingerprint

)

select
    as_of_date,
    release_revision,
    revision_fingerprint,
    parser_contract_version
from ranked
where promotion_rank = 1
