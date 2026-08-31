-- Singular test: succeeds only when it returns zero rows.
--
-- Independently reconciles fct_public_status_observations against
-- int_public_organization_eligibility, int_promoted_releases, and
-- int_keyed_snapshots directly -- never trusting the model's own cross
-- join/left join to have built the complete eligible-key x promoted-release
-- grid correctly (D-16):
--
-- 1. Grain uniqueness: no (state_charity_registration_number, as_of_date,
--    release_revision, revision_fingerprint) combination appears more than
--    once in fct_public_status_observations.
-- 2. Grid completeness: independently recomputing the full eligible key x
--    promoted release cross join produces the exact same row set the model
--    reports -- no combination is missing and no unexpected combination
--    (e.g. an ineligible key, or a release not actually promoted) appears.
-- 3. Observation-state correctness: for every row, independently checking
--    whether a matching int_keyed_snapshots row exists for that exact
--    key/release agrees with the model's own observation_state -- 'observed'
--    only when a snapshot row exists, 'not_observed' only when it does not.
--
-- Every failure surfaces only a safe registration-key/as_of_date identifier
-- -- never an organization name, city, or any other excluded/sensitive
-- value.

with eligible_keys as (

    select state_charity_registration_number
    from {{ ref('int_public_organization_eligibility') }}
    where eligibility_classification = 'eligible'

),

expected_grid as (

    select
        eligible_keys.state_charity_registration_number,
        releases.as_of_date,
        releases.release_revision,
        releases.revision_fingerprint
    from eligible_keys
    cross join {{ ref('int_promoted_releases') }} as releases

),

model_rows as (

    select
        state_charity_registration_number,
        as_of_date,
        release_revision,
        revision_fingerprint,
        observation_state
    from {{ ref('fct_public_status_observations') }}

),

duplicate_grain_failures as (

    select
        'duplicate_grain:' || state_charity_registration_number || ':' || as_of_date as failure_reason
    from model_rows
    group by state_charity_registration_number, as_of_date, release_revision, revision_fingerprint
    having count(*) > 1

),

grid_completeness_failures as (

    select
        'grid_completeness_mismatch:'
            || coalesce(expected.state_charity_registration_number, model.state_charity_registration_number)
            || ':' || coalesce(expected.as_of_date, model.as_of_date) as failure_reason
    from expected_grid as expected
    full outer join model_rows as model
        on expected.state_charity_registration_number = model.state_charity_registration_number
       and expected.as_of_date = model.as_of_date
       and expected.release_revision = model.release_revision
       and expected.revision_fingerprint = model.revision_fingerprint
    where expected.state_charity_registration_number is null
       or model.state_charity_registration_number is null

),

recomputed_observation_state as (

    select
        model.state_charity_registration_number,
        model.as_of_date,
        model.release_revision,
        model.revision_fingerprint,
        model.observation_state,
        case
            when snapshot.state_charity_registration_number is not null then 'observed'
            else 'not_observed'
        end as expected_observation_state
    from model_rows as model
    left join {{ ref('int_keyed_snapshots') }} as snapshot
        on model.state_charity_registration_number = snapshot.state_charity_registration_number
       and model.as_of_date = snapshot.as_of_date
       and model.release_revision = snapshot.release_revision
       and model.revision_fingerprint = snapshot.revision_fingerprint

),

observation_state_failures as (

    select
        'observation_state_mismatch:' || state_charity_registration_number || ':' || as_of_date as failure_reason
    from recomputed_observation_state
    where observation_state <> expected_observation_state

)

select failure_reason from duplicate_grain_failures
union all
select failure_reason from grid_completeness_failures
union all
select failure_reason from observation_state_failures
