-- Singular test: succeeds only when it returns zero rows.
--
-- Independently recomputes every delinquency-spell island, bound, and
-- terminal state directly from `int_keyed_snapshots`/`int_promoted_date_spine`
-- (D-08 through D-11), without ever trusting `int_entity_observation_sequence`
-- or `int_delinquency_spells`'s own `lag`/`lead`, island numbering, or
-- terminal `case` to have stayed correct:
--
-- 1. Row-count reconciliation: `int_delinquency_spells`'s own row count
--    equals this test's independently recomputed island count.
-- 2. Grain uniqueness: no (state_charity_registration_number, spell_number)
--    combination appears more than once.
-- 3. Ordinal continuity: every recomputed island is a contiguous run of
--    global observation ordinals for its exact key -- no gap is ever
--    silently bridged into one spell.
-- 4. Bound ordering: `onset_left` strictly precedes `onset_right` when
--    present, `exit_left` never exceeds a present `exit_right`, and
--    `onset_right` never exceeds `exit_left`.
-- 5. Terminal-state exclusivity: `terminal_state` is always exactly one of
--    the three closed values and each boolean censoring/loss flag agrees
--    with it -- never more than one true, never zero true.
-- 6. Full recomputation match: this test's independently recomputed
--    `onset_left`/`onset_right`/`exit_left`/`exit_right`/`is_left_censored`/
--    `terminal_state` agree exactly with the model's own values for every
--    spell, including the fixture's engineered loss/reappearance
--    (two left-censored spells, never bridged) and exit/re-entry (an
--    observed exit followed by a fresh non-left-censored onset) cases.
-- 7. Event-date misuse: no spell bound ever equals a source-reported
--    diagnostic date (D-07/D-017) carried by that same exact key anywhere
--    in the panel -- a bound is only ever an observation date, never a
--    source-reported renewal/status-set date.
--
-- Every failure surfaces only a safe synthetic registration-key/spell-number
-- coordinate or a fixed category -- never an organization name or any
-- excluded value.

with date_ordinals as (

    select
        as_of_date,
        release_revision,
        revision_fingerprint,
        row_number() over (order by as_of_date) as observation_ordinal
    from {{ ref('int_promoted_date_spine') }}

),

panel_extent as (

    select max(observation_ordinal) as panel_max_observation_ordinal
    from date_ordinals

),

complete_observations as (

    select
        snapshot.state_charity_registration_number,
        snapshot.is_delinquent,
        ordinals.as_of_date,
        ordinals.observation_ordinal
    from {{ ref('int_keyed_snapshots') }} as snapshot
    inner join date_ordinals as ordinals
        on snapshot.as_of_date = ordinals.as_of_date
       and snapshot.release_revision = ordinals.release_revision
       and snapshot.revision_fingerprint = ordinals.revision_fingerprint

),

neighbor_windows as (

    select
        *,
        lag(observation_ordinal) over key_window as prior_observation_ordinal,
        lag(as_of_date) over key_window as prior_as_of_date,
        lag(is_delinquent) over key_window as prior_is_delinquent,
        lead(observation_ordinal) over key_window as next_observation_ordinal,
        lead(as_of_date) over key_window as next_as_of_date,
        lead(is_delinquent) over key_window as next_is_delinquent
    from complete_observations
    window key_window as (
        partition by state_charity_registration_number
        order by observation_ordinal
    )

),

delinquent_observations as (

    select * from neighbor_windows where is_delinquent

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

island_extent as (

    select
        state_charity_registration_number,
        spell_number,
        count(*) as observed_row_count,
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

recomputed_spells as (

    select

        extent.state_charity_registration_number,
        extent.spell_number,
        extent.observed_row_count,
        extent.first_observation_ordinal,
        extent.last_observation_ordinal,
        extent.onset_right,
        extent.exit_left,

        case
            when first_rows.prior_observation_ordinal = extent.first_observation_ordinal - 1
                    and not first_rows.prior_is_delinquent
                then first_rows.prior_as_of_date
            else null
        end as recomputed_onset_left,

        case
            when first_rows.prior_observation_ordinal = extent.first_observation_ordinal - 1
                    and not first_rows.prior_is_delinquent
                then false
            else true
        end as recomputed_is_left_censored,

        case
            when last_rows.next_observation_ordinal = extent.last_observation_ordinal + 1
                    and not last_rows.next_is_delinquent
                then 'observed_exit'
            when extent.last_observation_ordinal = panel_extent.panel_max_observation_ordinal
                then 'right_censored'
            else 'lost_to_observation'
        end as recomputed_terminal_state,

        case
            when last_rows.next_observation_ordinal = extent.last_observation_ordinal + 1
                    and not last_rows.next_is_delinquent
                then last_rows.next_as_of_date
            else null
        end as recomputed_exit_right

    from island_extent as extent
    inner join first_rows
        on first_rows.state_charity_registration_number = extent.state_charity_registration_number
       and first_rows.spell_number = extent.spell_number
    inner join last_rows
        on last_rows.state_charity_registration_number = extent.state_charity_registration_number
       and last_rows.spell_number = extent.spell_number
    cross join panel_extent

),

row_count_failures as (

    select 'spell_row_count_mismatch' as failure_reason
    where (select count(*) from {{ ref('int_delinquency_spells') }})
        <> (select count(*) from recomputed_spells)

),

grain_duplicate_failures as (

    select
        'duplicate_spell_grain:' || state_charity_registration_number || ':' || spell_number as failure_reason
    from {{ ref('int_delinquency_spells') }}
    group by state_charity_registration_number, spell_number
    having count(*) > 1

),

ordinal_continuity_failures as (

    select
        'spell_ordinal_gap:' || state_charity_registration_number || ':' || spell_number as failure_reason
    from island_extent
    where observed_row_count <> (last_observation_ordinal - first_observation_ordinal + 1)

),

bound_ordering_failures as (

    select
        'spell_bound_ordering_violation:' || state_charity_registration_number || ':' || spell_number as failure_reason
    from {{ ref('int_delinquency_spells') }}
    where (onset_left is not null and onset_left >= onset_right)
       or (exit_right is not null and exit_left > exit_right)
       or onset_right > exit_left

),

terminal_state_exclusivity_failures as (

    select
        'terminal_state_flag_mismatch:' || state_charity_registration_number || ':' || spell_number as failure_reason
    from {{ ref('int_delinquency_spells') }}
    where terminal_state not in ('observed_exit', 'right_censored', 'lost_to_observation')
       or (
            case when is_right_censored then 1 else 0 end
            + case when is_lost_to_observation then 1 else 0 end
            + case when terminal_state = 'observed_exit' then 1 else 0 end
          ) <> 1
       or is_right_censored <> (terminal_state = 'right_censored')
       or is_lost_to_observation <> (terminal_state = 'lost_to_observation')

),

recomputation_mismatches as (

    select
        'spell_recomputation_mismatch:' || model.state_charity_registration_number || ':' || model.spell_number as failure_reason
    from {{ ref('int_delinquency_spells') }} as model
    inner join recomputed_spells as recomputed
        on model.state_charity_registration_number = recomputed.state_charity_registration_number
       and model.spell_number = recomputed.spell_number
    where model.onset_left is distinct from recomputed.recomputed_onset_left
       or model.onset_right is distinct from recomputed.onset_right
       or model.exit_left is distinct from recomputed.exit_left
       or model.exit_right is distinct from recomputed.recomputed_exit_right
       or model.is_left_censored is distinct from recomputed.recomputed_is_left_censored
       or model.terminal_state is distinct from recomputed.recomputed_terminal_state

),

key_source_reported_dates as (

    select distinct
        state_charity_registration_number,
        source_reported_last_renewal_date as source_reported_date
    from {{ ref('int_keyed_snapshots') }}
    where source_reported_last_renewal_date is not null

    union

    select distinct
        state_charity_registration_number,
        source_reported_current_status_date as source_reported_date
    from {{ ref('int_keyed_snapshots') }}
    where source_reported_current_status_date is not null

),

event_date_misuse_failures as (

    select
        'spell_bound_equals_source_reported_date:' || spells.state_charity_registration_number || ':' || spells.spell_number as failure_reason
    from {{ ref('int_delinquency_spells') }} as spells
    inner join key_source_reported_dates as reported
        on reported.state_charity_registration_number = spells.state_charity_registration_number
    where reported.source_reported_date in (spells.onset_left, spells.onset_right, spells.exit_left, spells.exit_right)

)

select failure_reason from row_count_failures
union all
select failure_reason from grain_duplicate_failures
union all
select failure_reason from ordinal_continuity_failures
union all
select failure_reason from bound_ordering_failures
union all
select failure_reason from terminal_state_exclusivity_failures
union all
select failure_reason from recomputation_mismatches
union all
select failure_reason from event_date_misuse_failures
