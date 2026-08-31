-- Singular test: succeeds only when it returns zero rows.
--
-- Independently proves `int_release_flags`'s grain, vocabulary closure,
-- identity-shape discipline, and non-scoring wording (D-14/T-04-04E),
-- without trusting the model's own union branches:
--
-- 1. Grain uniqueness: no (rule_id, rule_version, scope, full release
--    identity, full pair identity) combination repeats.
-- 2. Closed `rule_class` vocabulary: exactly 'deterministic' or
--    'heuristic_review'.
-- 3. Closed `result` vocabulary: exactly 'pass', 'review', or
--    'not_applicable'.
-- 4. Closed `scope` vocabulary and identity-shape discipline: exactly
--    'release' or 'release_pair', and the two identity shapes never
--    overlap on the same row -- a 'release' row always carries a full
--    release identity and a null pair identity; a 'release_pair' row
--    always carries a full pair identity and a null release identity.
-- 5. Non-scoring wording: rule_id, parameter_version, and result never
--    contain a prohibited anomaly/risk/quality/score/ranking/recommendation
--    token -- release flags are review facts, never a characterization.

with flags as (

    select * from {{ ref('int_release_flags') }}

),

grain_duplicate_failures as (

    select 'duplicate_flag_grain:' || rule_id || ':' || rule_version as failure_reason
    from flags
    group by
        rule_id, rule_version, scope,
        as_of_date, release_revision, revision_fingerprint,
        pair_from_as_of_date, pair_from_release_revision, pair_from_revision_fingerprint,
        pair_to_as_of_date, pair_to_release_revision, pair_to_revision_fingerprint
    having count(*) > 1

),

closed_vocabulary_failures as (

    select 'unclosed_rule_class:' || rule_id as failure_reason
    from flags
    where rule_class not in ('deterministic', 'heuristic_review')

    union all

    select 'unclosed_result:' || rule_id as failure_reason
    from flags
    where result not in ('pass', 'review', 'not_applicable')

    union all

    select 'unclosed_scope:' || rule_id as failure_reason
    from flags
    where scope not in ('release', 'release_pair')

),

scope_identity_shape_failures as (

    select 'release_scope_has_pair_identity:' || rule_id as failure_reason
    from flags
    where scope = 'release'
      and (pair_from_as_of_date is not null or pair_to_as_of_date is not null)

    union all

    select 'release_scope_missing_release_identity:' || rule_id as failure_reason
    from flags
    where scope = 'release' and as_of_date is null

    union all

    select 'release_pair_scope_has_release_identity:' || rule_id as failure_reason
    from flags
    where scope = 'release_pair' and as_of_date is not null

    union all

    select 'release_pair_scope_missing_pair_identity:' || rule_id as failure_reason
    from flags
    where scope = 'release_pair' and pair_from_as_of_date is null

),

prohibited_wording_failures as (

    select 'prohibited_wording:' || rule_id as failure_reason
    from flags
    where lower(rule_id) like '%score%'
       or lower(rule_id) like '%risk%'
       or lower(rule_id) like '%anomaly%'
       or lower(rule_id) like '%rank%'
       or lower(rule_id) like '%recommend%'
       or lower(parameter_version) like '%score%'
       or lower(parameter_version) like '%risk%'
       or lower(parameter_version) like '%anomaly%'
       or lower(parameter_version) like '%rank%'
       or lower(parameter_version) like '%recommend%'
       or lower(result) like '%score%'
       or lower(result) like '%risk%'
       or lower(result) like '%anomaly%'
       or lower(result) like '%rank%'
       or lower(result) like '%recommend%'

)

select failure_reason from grain_duplicate_failures
union all
select failure_reason from closed_vocabulary_failures
union all
select failure_reason from scope_identity_shape_failures
union all
select failure_reason from prohibited_wording_failures
