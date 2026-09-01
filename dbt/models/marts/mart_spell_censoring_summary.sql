{{ config(materialized='view') }}

-- Grain: one row per authoritative closed spell terminal state.
select terminal_state, count(*) as spell_count
from {{ ref('int_delinquency_spells') }}
group by terminal_state
