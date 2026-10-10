# Spike-era requirements audit

Generated from the exact predecessor requirement enumeration.
Recorded: 2026-10-10. Eleven clauses remain in force, three are superseded, and founder action #5 alone is deferred.

## automated first/third-Wednesday capture

Disposition: `satisfied`. Source retirement ended new releases; dispatched fixture calendar cases and actual schedule observations remain distinct; the approved six residuals apply.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| ci_run | [https://github.com/mrnouiouat/calico/actions/runs/36779395262/attempts/1](https://github.com/mrnouiouat/calico/actions/runs/36779395262/attempts/1) | Actual schedule outside capture window; 22 historical ordinary log absolute_local_path locations; no accepted claim; recorded_at: 2026-10-10; conclusion: success; head_sha: 9297d1db4e381f3da7ae03ba7e9ed57812d12a9b; event: schedule; evidence_class: actual_schedule_observation |
| ci_run | [https://github.com/mrnouiouat/calico/actions/runs/36812428040/attempts/1](https://github.com/mrnouiouat/calico/actions/runs/36812428040/attempts/1) | Live source rejected; 66 historical ordinary log absolute_local_path locations; no clean-log claim; recorded_at: 2026-10-10; conclusion: success; head_sha: 9297d1db4e381f3da7ae03ba7e9ed57812d12a9b; event: workflow_dispatch; evidence_class: live_source_observation |
| ci_run | [https://github.com/mrnouiouat/calico/actions/runs/37693376162/attempts/1](https://github.com/mrnouiouat/calico/actions/runs/37693376162/attempts/1) | Actual schedule rejected retired source; 66 historical ordinary log absolute_local_path locations; no clean-log claim; recorded_at: 2026-10-10; conclusion: success; head_sha: ae8a612b0e0be8b47b1a15d366b1a41eb3433677; event: schedule; evidence_class: actual_schedule_observation |
| ci_run | [https://github.com/mrnouiouat/calico/actions/runs/37985365302/attempts/1](https://github.com/mrnouiouat/calico/actions/runs/37985365302/attempts/1) | Recorded fixture-hosted replay: 32 dbt models and 228 tests passed on its historical head; recorded_at: 2026-10-09; conclusion: success; head_sha: 3bd3e43bb9cc0f7222559c2466c64488fe787685; event: workflow_dispatch; evidence_class: fixture_hosted_replay |
| file_sha256 | [.github/workflows/capture-current.yml](../../.github/workflows/capture-current.yml) | Wednesday schedule plus shared calendar gate and manual capture fallback; sha256: 4e92dfbbe1051cbfa634011baf3722be961975d0486ee6d2a6f99520afed7faf |
| file_sha256 | [calico_capture/calendar.py](../../calico_capture/calendar.py) | Exact UTC first/third-Wednesday authority: allow days 1/7/15/21; refuse days 8/22; sha256: 9160c9bdbff305ad4bd4228e51a4f91dcec409b3bca0e6e49ef7af891ba537f4 |
| file_sha256 | [docs/decisions/condition-4-hosted-outcomes.md](../../docs/decisions/condition-4-hosted-outcomes.md) | Approved 2026-10-10 six-residual amendment; historical log limits are disclosed without a privacy waiver; sha256: 444ade2ad968bb570d66ec5ac775fa96666212758ca2879dc52e7cc893dda635 |
| manifest | [docs/evidence/gate-e/hosted-replay-v1.json](../../docs/evidence/gate-e/hosted-replay-v1.json) | Actual Jobs API conclusions, reconstruction digest equality, fixture SQL and bytes-derived privacy audits; isolated atomic publication; sha256: 7a5abc056144409c7efd29eafcff6dffa7babf7d05169f0487e788bb824136f6 |
| test_id | [tests.capture.test_schedule_contract.CalendarBoundaryDateTests.test_exact_wednesday_boundaries](https://github.com/mrnouiouat/calico/blob/main/tests/capture/test_schedule_contract.py) | All six calendar boundaries are independently tested |

## four-file atomic admission

Disposition: `satisfied`. Requirement-derived clause.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| manifest | [docs/evidence/gate-e/hosted-replay-v1.json](../../docs/evidence/gate-e/hosted-replay-v1.json) | Actual Jobs API conclusions, reconstruction digest equality, fixture SQL and bytes-derived privacy audits; isolated atomic publication; sha256: 7a5abc056144409c7efd29eafcff6dffa7babf7d05169f0487e788bb824136f6 |
| test_id | [tests.landing.test_admission.RejectionMatrixTests.test_blank_date_rejected_with_ordered_date_reason](https://github.com/mrnouiouat/calico/blob/main/tests/landing/test_admission.py) | Known structural rejection preserves the prior admitted release |
| test_id | [tests.landing.test_admission.RejectionMatrixTests.test_duplicate_key_across_lists_rejected_with_duplicate_category](https://github.com/mrnouiouat/calico/blob/main/tests/landing/test_admission.py) | Known structural rejection preserves the prior admitted release |
| test_id | [tests.landing.test_admission.RejectionMatrixTests.test_duplicate_key_within_list_rejected_with_duplicate_category](https://github.com/mrnouiouat/calico/blob/main/tests/landing/test_admission.py) | Known structural rejection preserves the prior admitted release |
| test_id | [tests.landing.test_admission.RejectionMatrixTests.test_invalid_same_date_revision_rejected_preserves_prior_promotion](https://github.com/mrnouiouat/calico/blob/main/tests/landing/test_admission.py) | Known structural rejection preserves the prior admitted release |
| test_id | [tests.landing.test_admission.RejectionMatrixTests.test_mismatched_date_rejected_with_ordered_date_reason](https://github.com/mrnouiouat/calico/blob/main/tests/landing/test_admission.py) | Known structural rejection preserves the prior admitted release |
| test_id | [tests.landing.test_admission.RejectionMatrixTests.test_missing_logical_file_rejected_as_invalid_mapping_pointer_unchanged](https://github.com/mrnouiouat/calico/blob/main/tests/landing/test_admission.py) | Incomplete four-file admission preserves the prior release |
| test_id | [tests.landing.test_admission.RejectionMatrixTests.test_truncated_payload_rejected_with_deterministic_transfer_code](https://github.com/mrnouiouat/calico/blob/main/tests/landing/test_admission.py) | Known structural rejection preserves the prior admitted release |
| test_id | [tests.landing.test_admission.RejectionMatrixTests.test_unknown_registration_family_rejected_blank_keys_stay_accepted](https://github.com/mrnouiouat/calico/blob/main/tests/landing/test_admission.py) | Known structural rejection preserves the prior admitted release |
| test_id | [tests.landing.test_admission.RejectionMatrixTests.test_wrong_arity_rejected_with_no_raw_row_in_output](https://github.com/mrnouiouat/calico/blob/main/tests/landing/test_admission.py) | Structural failures reject without printing raw rows |
| test_id | [tests.landing.test_admission.RejectionMatrixTests.test_wrong_header_rejected_with_safe_logical_location_only](https://github.com/mrnouiouat/calico/blob/main/tests/landing/test_admission.py) | Known structural rejection preserves the prior admitted release |

## accepted/no_new_release/rejected run recording

Disposition: `satisfied`. Requirement-derived clause.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| ci_run | [https://github.com/mrnouiouat/calico/actions/runs/36779395262/attempts/1](https://github.com/mrnouiouat/calico/actions/runs/36779395262/attempts/1) | Actual schedule outside capture window; 22 historical ordinary log absolute_local_path locations; no accepted claim; recorded_at: 2026-10-10; conclusion: success; head_sha: 9297d1db4e381f3da7ae03ba7e9ed57812d12a9b; event: schedule; evidence_class: actual_schedule_observation |
| ci_run | [https://github.com/mrnouiouat/calico/actions/runs/36812428040/attempts/1](https://github.com/mrnouiouat/calico/actions/runs/36812428040/attempts/1) | Live source rejected; 66 historical ordinary log absolute_local_path locations; no clean-log claim; recorded_at: 2026-10-10; conclusion: success; head_sha: 9297d1db4e381f3da7ae03ba7e9ed57812d12a9b; event: workflow_dispatch; evidence_class: live_source_observation |
| ci_run | [https://github.com/mrnouiouat/calico/actions/runs/37693376162/attempts/1](https://github.com/mrnouiouat/calico/actions/runs/37693376162/attempts/1) | Actual schedule rejected retired source; 66 historical ordinary log absolute_local_path locations; no clean-log claim; recorded_at: 2026-10-10; conclusion: success; head_sha: ae8a612b0e0be8b47b1a15d366b1a41eb3433677; event: schedule; evidence_class: actual_schedule_observation |
| ci_run | [https://github.com/mrnouiouat/calico/actions/runs/37985365302/attempts/1](https://github.com/mrnouiouat/calico/actions/runs/37985365302/attempts/1) | Recorded fixture-hosted replay: 32 dbt models and 228 tests passed on its historical head; recorded_at: 2026-10-09; conclusion: success; head_sha: 3bd3e43bb9cc0f7222559c2466c64488fe787685; event: workflow_dispatch; evidence_class: fixture_hosted_replay |
| file_sha256 | [contracts/capture-status-v3.schema.json](../../contracts/capture-status-v3.schema.json) | Closed accepted/no_new_release/rejected run recording; sha256: a1d0bcbb2aec5f0a0d4103932e8b84d9b191de0bed92c894f00c0a4c32fa603d |
| file_sha256 | [docs/decisions/condition-4-hosted-outcomes.md](../../docs/decisions/condition-4-hosted-outcomes.md) | Approved 2026-10-10 six-residual amendment; historical log limits are disclosed without a privacy waiver; sha256: 444ade2ad968bb570d66ec5ac775fa96666212758ca2879dc52e7cc893dda635 |
| manifest | [docs/evidence/gate-e/hosted-replay-v1.json](../../docs/evidence/gate-e/hosted-replay-v1.json) | Actual Jobs API conclusions, reconstruction digest equality, fixture SQL and bytes-derived privacy audits; isolated atomic publication; sha256: 7a5abc056144409c7efd29eafcff6dffa7babf7d05169f0487e788bb824136f6 |

## visible release-integrity and bulk-movement flags

Disposition: `satisfied`. Requirement-derived clause.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| file_sha256 | [dbt/models/intermediate/int_release_flags.sql](../../dbt/models/intermediate/int_release_flags.sql) | Governed SQL derives integrity and visible bulk-movement flags; sha256: 2a9fd517072d290e346f6626ba35910ee3af637df4d90efa76b78362e72fccd6 |
| file_sha256 | [dbt/models/marts/mart_release_quality.sql](../../dbt/models/marts/mart_release_quality.sql) | Published release-quality mart exposes the governed flag results; sha256: 1e438a705e34d57631c3142299a8575d7dbb4d2442207bed5dd74d7cae0f5e4a |
| file_sha256 | [docs/walkthrough.md](../../docs/walkthrough.md) | Visible release-integrity and bulk movement descriptions; no inferred cause; sha256: 728dbd41b719eb6a67b678d877398bc165fdaf760896cb45640a74491ff3764f |

## scheduled automation is not the sole preservation mechanism

Disposition: `satisfied`. Requirement-derived clause.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| file_sha256 | [docs/capture-runbook.md](../../docs/capture-runbook.md) | Manual preservation fallback remains mandatory after scheduled cutover; sha256: d9314ed8a4ad6e574eb01de93a92dcba4449a8292ea6ad3808e9ae67dda570bb |
| test_id | [tests.capture.test_archive.SynchronizeTransactionTests.test_byte_identical_replay_is_an_idempotent_no_op](https://github.com/mrnouiouat/calico/blob/main/tests/capture/test_archive.py) | Immutable private preservation has an independent idempotent mechanism |

## historical release identity includes source URL, archive timestamp, as-of date, list, revision, byte count, row count, and SHA-256

Disposition: `satisfied`. Requirement-derived clause.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| file_sha256 | [docs/evidence/gate-a/spike-001-successor-v1.json](../../docs/evidence/gate-a/spike-001-successor-v1.json) | Corrected historical parsed counts supersede retained predecessor counts; sha256: c9f7a1b3fef9a586e7f60e930f2f12d32cd3bc30d07b82de9406947f5d64d2cf |
| file_sha256 | [docs/evidence/gate-b/real-input-catalog-v1.json](../../docs/evidence/gate-b/real-input-catalog-v1.json) | Three accepted date/revision identities bind their private revision manifests through exact hashes; sha256: cb4544408249160bc24ec4f3f56ae2575fd924405e3a4aa3127bc2d571ff6040 |
| file_sha256 | [docs/provenance/spikes/001-archive-sample-validation/archive-sample-manifest.json.md](../../docs/provenance/spikes/001-archive-sample-validation/archive-sample-manifest.json.md) | Recorded historical original URLs, archive timestamps, source dates/lists, byte counts and hashes; predecessor row counts carry visible additive corrections; sha256: 1605b57c82095b97acdfbd619d96f2e5ef7cc727b73eb451962cc7f3ebd77119 |
| test_id | [tests.landing.test_admission.AcceptedRevisionTests.test_baseline_candidate_admits_revision_one_with_full_provenance](https://github.com/mrnouiouat/calico/blob/main/tests/landing/test_admission.py) | Admission persists exact date/revision, four source hashes, bytes and reconciled row counts |

## full nonblank State Charity Reg# is the longitudinal key with keyless rows visible as coverage and EIN never a fallback identity

Disposition: `satisfied`. Requirement-derived clause.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| test_id | [tests.dbt_longitudinal.test_transitions.KeyedSnapshotSqlShapeTests.test_keyed_snapshots_uses_exact_eligible_predicate](https://github.com/mrnouiouat/calico/blob/main/tests/dbt_longitudinal/test_transitions.py) | Full registration keys; no excluded identifier fallback |
| test_id | [tests.dbt_longitudinal.test_transitions.KeyedSnapshotSqlShapeTests.test_unkeyed_coverage_groups_the_row_level_relation](https://github.com/mrnouiouat/calico/blob/main/tests/dbt_longitudinal/test_transitions.py) | Keyless records remain visible coverage |

## snapshots immutable, transitions and interval-censored spells derived

Disposition: `satisfied`. Requirement-derived clause.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| test_id | [tests.capture.test_archive.SynchronizeTransactionTests.test_different_bytes_at_existing_key_is_a_deterministic_collision](https://github.com/mrnouiouat/calico/blob/main/tests/capture/test_archive.py) | Immutable source snapshots cannot overwrite an existing key |
| test_id | [tests.dbt_longitudinal.test_spells.DelinquencySpellsSqlShapeTests.test_delinquency_spells_never_coalesces_a_missing_bound](https://github.com/mrnouiouat/calico/blob/main/tests/dbt_longitudinal/test_spells.py) | Derived interval-censored spells retain absent bounds |

## disappearance is not cure

Disposition: `satisfied`. Requirement-derived clause.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| file_sha256 | [docs/walkthrough.md](../../docs/walkthrough.md) | Disappearance is not cure; sha256: 728dbd41b719eb6a67b678d877398bc165fdaf760896cb45640a74491ff3764f |
| test_id | [tests.dbt_longitudinal.test_transitions.EntityTransitionsSqlShapeTests.test_entity_transitions_never_coalesces_missing_status](https://github.com/mrnouiouat/calico/blob/main/tests/dbt_longitudinal/test_transitions.py) | Missing status stays distinct from observed exit |

## duration metrics retain left, right, and interval censoring and never annualize a raw gap proportion

Disposition: `satisfied`. Requirement-derived clause.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| file_sha256 | [contracts/metric-denominators-v1.json](../../contracts/metric-denominators-v1.json) | Date-pair denominators and non-annualized gap proportions; sha256: f99c0f2ee45cef25f069b4c00e18d702a2f2224fd9dbcc49994e61034d0ab237 |
| test_id | [tests.dbt_longitudinal.test_spells.DelinquencySpellsSqlShapeTests.test_delinquency_spells_never_coalesces_a_missing_bound](https://github.com/mrnouiouat/calico/blob/main/tests/dbt_longitudinal/test_spells.py) | Left/right/interval censoring remains explicit |

## the exclusion list (stakeholder interviews, internal organization-level tooling, investigation queues, email notifications, predictions, causal explanations, per-organization recommendations) holds

Disposition: `satisfied`. Requirement-derived clause.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| file_sha256 | [docs/provenance/spikes/005-project-recommendation/README.md#usefulness-boundary](../../docs/provenance/spikes/005-project-recommendation/README.md#usefulness-boundary) | Exact still-in-force exclusion list; other superseded predecessor recommendations do not govern this row; sha256: 62400e2211ed14f7f5187eb071a0cced4cbf5f882320aa1e4267444218189f6c |
| test_id | [tests.docs_public.test_readme.ReadmeContracts.test_complete_readme_has_all_decided_topics_and_separate_refresh](https://github.com/mrnouiouat/calico/blob/main/tests/docs_public/test_readme.py) | Current generated README enforces its bounded topics and limitations |

## published artifacts are aggregate-only and contain no organization identity fields

Disposition: `superseded`. Requirement-derived clause.

Superseding decision: [D-007](../decisions/register.md#d-007).

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| file_sha256 | [docs/decisions/register.md#d-007](../../docs/decisions/register.md#d-007) | D-007 intentionally permits bounded approved named organization history; sha256: 999e1f7f8776a57e4445ac62c1385ab7d3d0d7b826169bd282f6313970734acb |

## one BI implementation ships: Power BI if Publish to web works, Evidence otherwise

Disposition: `superseded`. Requirement-derived clause.

Superseding decision: [D-008](../decisions/register.md#d-008).

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| file_sha256 | [docs/decisions/register.md#d-008](../../docs/decisions/register.md#d-008) | D-008 settles one Power BI implementation; sha256: 999e1f7f8776a57e4445ac62c1385ab7d3d0d7b826169bd282f6313970734acb |

## the archive-census precondition

Disposition: `superseded`. Requirement-derived clause.

Superseding decision: [D-012](../decisions/register.md#d-012).

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| file_sha256 | [docs/decisions/register.md#d-012](../../docs/decisions/register.md#d-012) | D-012 excludes broader archive census from v1; sha256: 999e1f7f8776a57e4445ac62c1385ab7d3d0d7b826169bd282f6313970734acb |

## seeking one external test of whether the finished monitor catches a real release problem — founder action #5

Disposition: `deferred_not_v1`. Founder action #5 alone is deferred_not_v1.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| test_id | [tests.docs_public.test_gate_e.SpikeEraAuditContractTests.test_exact_requirement_clauses_and_superseding_decisions](https://github.com/mrnouiouat/calico/blob/main/tests/docs_public/test_gate_e.py) | Founder action #5 alone remains deferred beyond v1 in the exact enumeration |
