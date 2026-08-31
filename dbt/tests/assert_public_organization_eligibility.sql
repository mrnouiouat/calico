-- Singular test: succeeds only when it returns zero rows.
--
-- Independently reconciles int_public_organization_eligibility and
-- dim_public_organizations against int_keyed_snapshots and the private
-- runtime_input.public_eligibility_classifications source directly --
-- never trusting either model's own left join to have classified every
-- key correctly (D-18, T-04-05A/T-04-05B):
--
-- 1. Completeness/uniqueness: every distinct exact key observed in
--    int_keyed_snapshots appears in int_public_organization_eligibility
--    exactly once -- never zero times (a silently dropped key) and never
--    more than once (a duplicated key).
-- 2. Classification correctness: independently re-joining every distinct
--    key directly to the private classification source and normalizing a
--    missing match to 'unclassified' produces the exact same
--    eligibility_classification int_public_organization_eligibility
--    reports for every key.
-- 3. Positive containment: every key present in dim_public_organizations
--    recomputes to exactly 'eligible' -- an 'ambiguous_natural_person' or
--    'unclassified' key never reaches the published relation.
-- 4. Negative containment: no key whose recomputed classification is
--    'ambiguous_natural_person' or 'unclassified' appears in
--    dim_public_organizations at all.
--
-- Every failure surfaces only a safe registration-key identifier -- never
-- an organization name, city, or any other excluded/sensitive value.

with distinct_keys as (

    select distinct state_charity_registration_number
    from {{ ref('int_keyed_snapshots') }}

),

recomputed_classifications as (

    select
        distinct_keys.state_charity_registration_number,
        coalesce(source_classifications.classification, 'unclassified') as expected_classification
    from distinct_keys
    left join {{ source('runtime_input', 'public_eligibility_classifications') }} as source_classifications
        on distinct_keys.state_charity_registration_number = source_classifications.registration_number

),

model_classifications as (

    select
        state_charity_registration_number,
        eligibility_classification
    from {{ ref('int_public_organization_eligibility') }}

),

completeness_failures as (

    select
        'completeness_mismatch:' || coalesce(recomputed.state_charity_registration_number, model.state_charity_registration_number)
            as failure_reason
    from recomputed_classifications as recomputed
    full outer join model_classifications as model
        on recomputed.state_charity_registration_number = model.state_charity_registration_number
    where recomputed.state_charity_registration_number is null
       or model.state_charity_registration_number is null

),

duplicate_key_failures as (

    select 'duplicate_eligibility_key:' || state_charity_registration_number as failure_reason
    from model_classifications
    group by state_charity_registration_number
    having count(*) > 1

),

classification_mismatch_failures as (

    select
        'classification_mismatch:' || recomputed.state_charity_registration_number as failure_reason
    from recomputed_classifications as recomputed
    inner join model_classifications as model
        on recomputed.state_charity_registration_number = model.state_charity_registration_number
    where recomputed.expected_classification <> model.eligibility_classification

),

published_but_not_eligible_failures as (

    select
        'published_key_not_eligible:' || published.state_charity_registration_number as failure_reason
    from {{ ref('dim_public_organizations') }} as published
    inner join recomputed_classifications as recomputed
        on published.state_charity_registration_number = recomputed.state_charity_registration_number
    where recomputed.expected_classification <> 'eligible'

),

published_key_missing_recomputation_failures as (

    select
        'published_key_has_no_recomputed_classification:' || published.state_charity_registration_number
            as failure_reason
    from {{ ref('dim_public_organizations') }} as published
    left join recomputed_classifications as recomputed
        on published.state_charity_registration_number = recomputed.state_charity_registration_number
    where recomputed.state_charity_registration_number is null

),

ineligible_key_published_failures as (

    select
        'ineligible_key_published:' || recomputed.state_charity_registration_number as failure_reason
    from recomputed_classifications as recomputed
    inner join {{ ref('dim_public_organizations') }} as published
        on recomputed.state_charity_registration_number = published.state_charity_registration_number
    where recomputed.expected_classification in ('ambiguous_natural_person', 'unclassified')

)

select failure_reason from completeness_failures
union all
select failure_reason from duplicate_key_failures
union all
select failure_reason from classification_mismatch_failures
union all
select failure_reason from published_but_not_eligible_failures
union all
select failure_reason from published_key_missing_recomputation_failures
union all
select failure_reason from ineligible_key_published_failures
