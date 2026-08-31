{{
    config(
        materialized='table'
    )
}}

-- Authoritative observation-bounded delinquency spell grain (D-08 through
-- D-11, D-21 representative model). One row per contiguous *observed*
-- island in the published delinquent population -- built entirely from
-- `int_entity_observation_sequence`'s complete ordered observation
-- sequence via cumulative-sum gaps-and-islands, never from a re-filtered
-- or re-windowed copy of it and never by joining the raw source-reported
-- current-status/renewal dates.
--
-- Every bound is strictly an observation bound (D-017), never an invented
-- event time: `onset_left`/`exit_right` are only ever populated from an
-- immediately adjacent observed neighbor row -- a preceding or following
-- promoted date exactly one ordinal away. A missing accepted release
-- always breaks continuity (D-11): reappearance after a gap always starts
-- a new left-censored spell, and re-entry after a confirmed observed exit
-- also always starts a new spell -- neither is ever bridged into the
-- surrounding island.
--
-- Named CTE stages mirror `int_entity_observation_sequence`'s own
-- documented shape: filter to the delinquent rows already computed there,
-- flag island starts, number islands cumulatively per key, aggregate each
-- island's bounds, then classify exactly one closed, mutually exclusive
-- `terminal_state` -- `observed_exit`, `lost_to_observation`, or
-- `right_censored` (D-10) -- by comparing the island's last observed
-- ordinal against its immediate next neighbor and the global panel
-- maximum ordinal. `assert_delinquency_spell_invariants.sql` independently
-- recomputes every island, bound, and terminal state directly from
-- `int_keyed_snapshots`/`int_promoted_date_spine`.

with panel_extent as (

    select count(*) as panel_max_observation_ordinal
    from {{ ref('int_promoted_date_spine') }}

),

delinquent_observations as (

    select *
    from {{ ref('int_entity_observation_sequence') }}
    where is_delinquent

),

island_start_flags as (

    select

        *,
        case
            when prior_observation_ordinal is null then true
            when not prior_is_delinquent then true
            when prior_observation_ordinal != observation_ordinal - 1 then true
            else false
        end as is_island_start

    from delinquent_observations

),

island_numbering as (

    select

        *,
        sum(case when is_island_start then 1 else 0 end) over (
            partition by state_charity_registration_number
            order by observation_ordinal
            rows between unbounded preceding and current row
        ) as spell_number

    from island_start_flags

),

island_bounds as (

    select

        state_charity_registration_number,
        spell_number,
        min(observation_ordinal) as first_observation_ordinal,
        max(observation_ordinal) as last_observation_ordinal,
        min(as_of_date) as onset_right,
        max(as_of_date) as exit_left

    from island_numbering
    group by state_charity_registration_number, spell_number

),

first_rows as (

    select

        state_charity_registration_number,
        spell_number,
        prior_observation_ordinal,
        prior_as_of_date,
        prior_is_delinquent

    from island_numbering
    where is_island_start

),

last_rows_ranked as (

    select

        *,
        row_number() over (
            partition by state_charity_registration_number, spell_number
            order by observation_ordinal desc
        ) as reverse_row_number

    from island_numbering

),

last_rows as (

    select

        state_charity_registration_number,
        spell_number,
        observation_ordinal as last_observation_ordinal,
        next_observation_ordinal,
        next_as_of_date,
        next_is_delinquent

    from last_rows_ranked
    where reverse_row_number = 1

),

spell_terminal_state as (

    select

        last_rows.state_charity_registration_number,
        last_rows.spell_number,
        last_rows.next_as_of_date,
        case
            when last_rows.next_observation_ordinal = last_rows.last_observation_ordinal + 1
                    and not last_rows.next_is_delinquent
                then 'observed_exit'
            when last_rows.last_observation_ordinal = panel_extent.panel_max_observation_ordinal
                then 'right_censored'
            else 'lost_to_observation'
        end as terminal_state

    from last_rows
    cross join panel_extent

)

select

    bounds.state_charity_registration_number,
    bounds.spell_number,

    -- D-09: an observed entry has an exact preceding observation outside
    -- delinquency exactly one ordinal earlier; only then is `onset_left`
    -- populated. First-seen or post-gap starts stay left-censored.
    case
        when first_rows.prior_observation_ordinal = bounds.first_observation_ordinal - 1
                and not first_rows.prior_is_delinquent
            then first_rows.prior_as_of_date
        else null
    end as onset_left,

    bounds.onset_right,
    bounds.exit_left,

    -- D-10: only an immediate later observed non-delinquent row supplies
    -- `exit_right`; loss and right censoring both keep it null.
    case
        when terminal.terminal_state = 'observed_exit' then terminal.next_as_of_date
        else null
    end as exit_right,

    case
        when first_rows.prior_observation_ordinal = bounds.first_observation_ordinal - 1
                and not first_rows.prior_is_delinquent
            then false
        else true
    end as is_left_censored,

    terminal.terminal_state = 'right_censored' as is_right_censored,
    terminal.terminal_state = 'lost_to_observation' as is_lost_to_observation,

    terminal.terminal_state

from island_bounds as bounds
inner join first_rows
    on first_rows.state_charity_registration_number = bounds.state_charity_registration_number
   and first_rows.spell_number = bounds.spell_number
inner join spell_terminal_state as terminal
    on terminal.state_charity_registration_number = bounds.state_charity_registration_number
   and terminal.spell_number = bounds.spell_number
