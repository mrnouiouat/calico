-- Exact Gate A oracle reconciliation. Fixture mode executes this same test
-- as an intentional zero-row identity-safe assertion; only the runner-owned
-- verified real mode activates the immutable benchmark rows.

{% if var('calico_verified_mode') == 'real' %}

with expected_snapshots(as_of_date, layer, category, expected_count) as (
    -- as_of_date is a plain string literal, not a `date` literal: every
    -- runtime as_of_date column is VARCHAR from `runner.py`'s
    -- `revision_catalog`/`promotion_catalog` table definitions onward, and
    -- DuckDB's `coalesce()` below requires matching types on both sides
    -- (mixing DATE and VARCHAR raises a Binder Error there, even though
    -- the equality join comparison alone tolerates it).
    values
        ('2026-07-15', 'promoted', 'all', 557067),
        ('2026-08-05', 'promoted', 'all', 557291),
        ('2026-08-19', 'promoted', 'all', 557211),
        ('2026-07-15', 'disposition', 'keyed', 247441),
        ('2026-08-05', 'disposition', 'keyed', 248077),
        ('2026-08-19', 'disposition', 'keyed', 248215),
        ('2026-07-15', 'snapshot', 'delinquent', 5476),
        ('2026-08-05', 'snapshot', 'delinquent', 13169),
        ('2026-08-19', 'snapshot', 'delinquent', 13071)
),
actual_snapshots as (
    select as_of_date, 'promoted' as layer, 'all' as category, count(*) as actual_count
    from {{ ref('int_promoted_registry_records') }} group by as_of_date
    union all
    select as_of_date, 'disposition', 'keyed', count(*)
    from {{ ref('int_registry_record_dispositions') }}
    where disposition = 'eligible_for_keyed_path' group by as_of_date
    union all
    select as_of_date, 'snapshot', 'delinquent', count(*)
    from {{ ref('int_keyed_snapshots') }} where is_delinquent group by as_of_date
),
expected_transitions(from_as_of_date, to_as_of_date, category, expected_count) as (
    -- Same VARCHAR-not-DATE rationale as expected_snapshots above.
    --
    -- Every closed transition_class endpoint-union category that the DAG
    -- actually emits for a pair is asserted here, not only the ones the
    -- evidence record's prose calls out by name (D-11/D-13 "endpoint-union
    -- transition classes"). Pair 1's `exit` count (65) is the evidence
    -- record's own explicit "Observed exits" figure. Pair 2's
    -- `newly_observed_delinquent` count (4) is not itself named in the
    -- evidence record's prose, but is the unique value the record's own
    -- closed-form arithmetic determines: still (13065) + entry (2) +
    -- newly_observed_delinquent (4) = 13071, the documented August 19
    -- delinquent total -- the same reconciliation style the record uses
    -- for pair 1 (5411 + 7750 + 8 = 13169).
    values
        ('2026-07-15', '2026-08-05', 'entry', 7750),
        ('2026-07-15', '2026-08-05', 'still', 5411),
        ('2026-07-15', '2026-08-05', 'exit', 65),
        ('2026-07-15', '2026-08-05', 'newly_observed_delinquent', 8),
        ('2026-07-15', '2026-08-05', 'named_movement', 7737),
        ('2026-08-05', '2026-08-19', 'entry', 2),
        ('2026-08-05', '2026-08-19', 'exit', 104),
        ('2026-08-05', '2026-08-19', 'still', 13065),
        ('2026-08-05', '2026-08-19', 'newly_observed_delinquent', 4)
),
actual_transitions as (
    select from_as_of_date, to_as_of_date,
        case transition_class
            when 'delinquency_entry_observed' then 'entry'
            when 'delinquency_exit_observed' then 'exit'
            when 'delinquency_still_observed' then 'still'
            when 'delinquent_newly_observed' then 'newly_observed_delinquent'
        end as category,
        count(*) as actual_count
    from {{ ref('int_entity_transitions') }}
    where transition_class in ('delinquency_entry_observed','delinquency_exit_observed','delinquency_still_observed','delinquent_newly_observed')
    group by from_as_of_date, to_as_of_date, category
    union all
    select from_as_of_date, to_as_of_date, 'named_movement', count(*)
    from {{ ref('int_entity_transitions') }}
    where observed_at_start and observed_at_end
      and start_status = 'Current - Reporting Incomplete' and end_is_delinquent
    group by from_as_of_date, to_as_of_date
),
snapshot_failures as (
    select coalesce(e.layer, a.layer) as layer, coalesce(e.category, a.category) as category,
           coalesce(e.as_of_date, a.as_of_date) as as_of_date,
           e.expected_count, a.actual_count
    from expected_snapshots e full outer join actual_snapshots a
      on e.as_of_date=a.as_of_date and e.layer=a.layer and e.category=a.category
    where coalesce(e.expected_count,-1) <> coalesce(a.actual_count,-1)
),
transition_failures as (
    select 'transition' as layer, coalesce(e.category,a.category) as category,
           coalesce(e.to_as_of_date,a.to_as_of_date) as as_of_date,
           e.expected_count, a.actual_count
    from expected_transitions e full outer join actual_transitions a
      on e.from_as_of_date=a.from_as_of_date and e.to_as_of_date=a.to_as_of_date and e.category=a.category
    where coalesce(e.expected_count,-1) <> coalesce(a.actual_count,-1)
)
select layer, category, as_of_date, expected_count, actual_count from snapshot_failures
union all
select layer, category, as_of_date, expected_count, actual_count from transition_failures

{% else %}

select 'fixture' as layer, 'identity_free' as category, '1900-01-01' as as_of_date,
       0 as expected_count, 0 as actual_count
where false

{% endif %}
