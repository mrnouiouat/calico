-- Singular test: succeeds only when it returns zero rows.
--
-- Independently reconciles `int_registry_record_dispositions` against
-- `int_promoted_registry_records` (T-03-12, D-11), without ever trusting
-- the disposition model's own `case` to have covered every row:
--
-- 1. Row-identity reconciliation: every promoted record identity (full
--    release key + source_list + source_line_no) appears in the
--    disposition relation exactly once -- never zero times (a silently
--    dropped record) and never more than once (a duplicated record).
-- 2. Duplicate detection: no disposed identity appears more than once.
-- 3. Disposition closure: every disposition value is one of the three
--    closed vocabulary members (belt-and-suspenders alongside the
--    `accepted_values` schema test).
-- 4. Global count reconciliation: the total promoted row count equals the
--    disposition relation's total row count, and equals the sum of
--    eligible + untracked + excluded counts.
-- 5. Partition-level reconciliation: the same equality holds independently
--    for every (as_of_date, release_revision, revision_fingerprint,
--    source_list) partition, so a compensating error in one partition can
--    never hide inside a matching global total.
--
-- Every failure surfaces only a safe source_list/source_line_no/date
-- identifier -- never an excluded source value or a full raw row.

with promoted_identity as (

    select as_of_date, release_revision, revision_fingerprint, source_list, source_line_no
    from {{ ref('int_promoted_registry_records') }}

),

disposition_identity as (

    select as_of_date, release_revision, revision_fingerprint, source_list, source_line_no, disposition
    from {{ ref('int_registry_record_dispositions') }}

),

identity_reconciliation_failures as (

    select
        'identity_reconciliation_mismatch:'
            || coalesce(promoted.source_list, disposed.source_list)
            || ':' || coalesce(promoted.source_line_no, disposed.source_line_no) as failure_reason
    from promoted_identity as promoted
    full outer join disposition_identity as disposed
        on promoted.as_of_date = disposed.as_of_date
       and promoted.release_revision = disposed.release_revision
       and promoted.revision_fingerprint = disposed.revision_fingerprint
       and promoted.source_list = disposed.source_list
       and promoted.source_line_no = disposed.source_line_no
    where promoted.source_list is null
       or disposed.source_list is null

),

duplicate_disposition_failures as (

    select
        'duplicate_disposition:' || source_list || ':' || source_line_no as failure_reason
    from disposition_identity
    group by as_of_date, release_revision, revision_fingerprint, source_list, source_line_no
    having count(*) > 1

),

closed_vocabulary_failures as (

    select 'unclosed_disposition_value:' || disposition as failure_reason
    from disposition_identity
    where disposition not in (
        'eligible_for_keyed_path',
        'untracked_blank_registration',
        'excluded_from_typed_path_blank_status'
    )

),

global_count_failures as (

    select 'global_count_mismatch' as failure_reason
    where (select count(*) from promoted_identity) <> (select count(*) from disposition_identity)
       or (select count(*) from promoted_identity) <> (
            select
                count(*) filter (where disposition = 'eligible_for_keyed_path')
                + count(*) filter (where disposition = 'untracked_blank_registration')
                + count(*) filter (where disposition like 'excluded_from_typed_path_%')
            from disposition_identity
       )

),

partition_counts_promoted as (

    select as_of_date, release_revision, revision_fingerprint, source_list, count(*) as promoted_count
    from promoted_identity
    group by as_of_date, release_revision, revision_fingerprint, source_list

),

partition_counts_disposed as (

    select as_of_date, release_revision, revision_fingerprint, source_list, count(*) as disposed_count
    from disposition_identity
    group by as_of_date, release_revision, revision_fingerprint, source_list

),

partition_count_failures as (

    select
        'partition_count_mismatch:'
            || coalesce(promoted_counts.as_of_date::text, disposed_counts.as_of_date::text)
            || ':' || coalesce(promoted_counts.source_list, disposed_counts.source_list) as failure_reason
    from partition_counts_promoted as promoted_counts
    full outer join partition_counts_disposed as disposed_counts
        on promoted_counts.as_of_date = disposed_counts.as_of_date
       and promoted_counts.release_revision = disposed_counts.release_revision
       and promoted_counts.revision_fingerprint = disposed_counts.revision_fingerprint
       and promoted_counts.source_list = disposed_counts.source_list
    where coalesce(promoted_counts.promoted_count, 0) <> coalesce(disposed_counts.disposed_count, 0)

)

select failure_reason from identity_reconciliation_failures
union all
select failure_reason from duplicate_disposition_failures
union all
select failure_reason from closed_vocabulary_failures
union all
select failure_reason from global_count_failures
union all
select failure_reason from partition_count_failures
