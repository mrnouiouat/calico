-- Singular test: succeeds only when it returns zero rows.
--
-- Independently recomputes `int_entity_transitions`'s closed transition
-- classification from its own raw observation/delinquency flags (D-06),
-- without ever trusting the model's own `case` to have covered every
-- branch or kept the two sides properly null:
--
-- 1. Reclassification: recomputing the exact same eight-branch closed
--    `case` from `observed_at_start`/`observed_at_end`/`start_is_delinquent`/
--    `end_is_delinquent` yields the identical `transition_class` value for
--    every row.
-- 2. Closed vocabulary / non-null: `transition_class` is never null and is
--    always one of the eight closed values (belt-and-suspenders alongside
--    the `accepted_values` schema test).
-- 3. Missing-side nullability: whenever a side is not observed, every
--    field describing that side (status, source list, delinquency flag,
--    both diagnostic dates and their unparseable flags) is null -- absence
--    is never coalesced into a stray leftover value.
-- 4. Observed-side non-null structural fields: whenever a side is
--    observed, its status, source list, and delinquency flag are always
--    non-null -- guaranteed by keyed-snapshot eligibility, independently
--    re-verified here.
--
-- Every failure surfaces only a safe pair-date identifier or fixed
-- category -- never a registration number or organization name.

with reclassified as (

    select
        from_as_of_date,
        to_as_of_date,
        transition_class,
        case
            when observed_at_start and observed_at_end
                    and not start_is_delinquent and end_is_delinquent
                then 'delinquency_entry_observed'
            when not observed_at_start and observed_at_end and end_is_delinquent
                then 'delinquent_newly_observed'
            when observed_at_start and observed_at_end
                    and start_is_delinquent and not end_is_delinquent
                then 'delinquency_exit_observed'
            when observed_at_start and observed_at_end
                    and start_is_delinquent and end_is_delinquent
                then 'delinquency_still_observed'
            when observed_at_start and not observed_at_end and start_is_delinquent
                then 'delinquent_lost_to_observation'
            when observed_at_start and observed_at_end
                then 'observed_status_movement_other'
            when not observed_at_start and observed_at_end
                then 'newly_observed_other'
            when observed_at_start and not observed_at_end
                then 'not_observed_after_other'
        end as recomputed_class
    from {{ ref('int_entity_transitions') }}

),

reclassification_failures as (

    select
        'transition_class_recomputation_mismatch:' || from_as_of_date || ':' || to_as_of_date as failure_reason
    from reclassified
    where transition_class is distinct from recomputed_class

),

closed_vocabulary_failures as (

    select
        'unclosed_transition_class:' || coalesce(transition_class, 'null') as failure_reason
    from {{ ref('int_entity_transitions') }}
    where transition_class is null
       or transition_class not in (
            'delinquency_entry_observed',
            'delinquent_newly_observed',
            'delinquency_exit_observed',
            'delinquency_still_observed',
            'delinquent_lost_to_observation',
            'observed_status_movement_other',
            'newly_observed_other',
            'not_observed_after_other'
       )

),

missing_start_nullability_failures as (

    select 'missing_start_side_not_null:' || from_as_of_date || ':' || to_as_of_date as failure_reason
    from {{ ref('int_entity_transitions') }}
    where not observed_at_start
      and (
          start_status is not null
          or start_source_list is not null
          or start_is_delinquent is not null
          or start_source_reported_last_renewal_date is not null
          or start_source_reported_last_renewal_date_nonblank_unparseable is not null
          or start_source_reported_current_status_date is not null
          or start_source_reported_current_status_date_nonblank_unparseable is not null
      )

),

missing_end_nullability_failures as (

    select 'missing_end_side_not_null:' || from_as_of_date || ':' || to_as_of_date as failure_reason
    from {{ ref('int_entity_transitions') }}
    where not observed_at_end
      and (
          end_status is not null
          or end_source_list is not null
          or end_is_delinquent is not null
          or end_source_reported_last_renewal_date is not null
          or end_source_reported_last_renewal_date_nonblank_unparseable is not null
          or end_source_reported_current_status_date is not null
          or end_source_reported_current_status_date_nonblank_unparseable is not null
      )

),

observed_start_structural_failures as (

    select 'observed_start_side_null_structural_field:' || from_as_of_date as failure_reason
    from {{ ref('int_entity_transitions') }}
    where observed_at_start
      and (start_status is null or start_source_list is null or start_is_delinquent is null)

),

observed_end_structural_failures as (

    select 'observed_end_side_null_structural_field:' || to_as_of_date as failure_reason
    from {{ ref('int_entity_transitions') }}
    where observed_at_end
      and (end_status is null or end_source_list is null or end_is_delinquent is null)

)

select failure_reason from reclassification_failures
union all
select failure_reason from closed_vocabulary_failures
union all
select failure_reason from missing_start_nullability_failures
union all
select failure_reason from missing_end_nullability_failures
union all
select failure_reason from observed_start_structural_failures
union all
select failure_reason from observed_end_structural_failures
