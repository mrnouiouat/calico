# Gate E portfolio evidence

Generated from the strict ten-condition authority.
CI and Service entries are recorded observations; offline validation does not contact those services.
Recorded: 2026-10-10.

## Condition 1

a public fixture build passes dbt build in CI.

Status: `pass`.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| ci_run | [https://github.com/mrnouiouat/calico/actions/runs/37985365302/attempts/1](https://github.com/mrnouiouat/calico/actions/runs/37985365302/attempts/1) | Recorded fixture-hosted replay: 32 dbt models and 228 tests passed on its historical head; recorded_at: 2026-10-09; conclusion: success; head_sha: 3bd3e43bb9cc0f7222559c2466c64488fe787685; event: workflow_dispatch; evidence_class: fixture_hosted_replay |
| file_sha256 | [.github/workflows/dbt-fixture.yml](../../.github/workflows/dbt-fixture.yml) | The normal CI workflow runs the public fixture dbt build and its tests; sha256: d5fc6f8b4a70020d8882d56b1f19258453e86b53f97b29e891f1cea8e84acfe3 |
| manifest | [docs/evidence/gate-e/hosted-replay-v1.json](../../docs/evidence/gate-e/hosted-replay-v1.json) | Actual Jobs API conclusions, reconstruction digest equality, fixture SQL and bytes-derived privacy audits; isolated atomic publication; sha256: 7a5abc056144409c7efd29eafcff6dffa7babf7d05169f0487e788bb824136f6 |

## Condition 2

a local real-data build passes the same models and tests.

Status: `pass`.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| ci_run | [https://github.com/mrnouiouat/calico/actions/runs/37726333085/attempts/1](https://github.com/mrnouiouat/calico/actions/runs/37726333085/attempts/1) | Immutable real B2 restore/build/publication mechanism; exact no_change; published manifest da4f4a3385f674adc4aea66044ad977a8f8dea86fdc10a7ca5607f918755e7ed; policy 91ab3bf914ef26102d48e44d44446fd02a4048440760bc64e9c8b95ce6f3aada; published commit 45cd920e3087a3f14d6bb196d15c49abc980f665; recorded_at: 2026-10-08; conclusion: success; head_sha: ae8a612b0e0be8b47b1a15d366b1a41eb3433677; event: workflow_dispatch; evidence_class: real_restore_republish_observation |
| manifest | [docs/evidence/gate-b/real-build-proof-v3.json](../../docs/evidence/gate-b/real-build-proof-v3.json) | Recorded local real-mode SQL models/tests, exact source binding and zero reconciliation mismatches; sha256: 1fa5d84e634dd00a07e09f54522cc830915c63a5d37cfe2416f39ebea9355c24 |
| test_id | [tests.dbt_metrics.test_reconciliation.RealProofProvenanceTests.test_verify_proof_rejects_fixture_mode_when_real_required](https://github.com/mrnouiouat/calico/blob/main/tests/dbt_metrics/test_reconciliation.py) | Real evidence cannot substitute fixture mode |

## Condition 3

release admission rejects the known structural failure cases.

Status: `pass`.

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

## Condition 4

scheduled and manually dispatched workflows prove accepted, no_new_release, and rejected without exposing raw source records.

Status: `pass_with_disclosed_deviation`.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| ci_run | [https://github.com/mrnouiouat/calico/actions/runs/36779395262/attempts/1](https://github.com/mrnouiouat/calico/actions/runs/36779395262/attempts/1) | Actual schedule outside capture window; 22 historical ordinary log absolute_local_path locations; no accepted claim; recorded_at: 2026-10-10; conclusion: success; head_sha: 9297d1db4e381f3da7ae03ba7e9ed57812d12a9b; event: schedule; evidence_class: actual_schedule_observation |
| ci_run | [https://github.com/mrnouiouat/calico/actions/runs/36812428040/attempts/1](https://github.com/mrnouiouat/calico/actions/runs/36812428040/attempts/1) | Live source rejected; 66 historical ordinary log absolute_local_path locations; no clean-log claim; recorded_at: 2026-10-10; conclusion: success; head_sha: 9297d1db4e381f3da7ae03ba7e9ed57812d12a9b; event: workflow_dispatch; evidence_class: live_source_observation |
| ci_run | [https://github.com/mrnouiouat/calico/actions/runs/37693376162/attempts/1](https://github.com/mrnouiouat/calico/actions/runs/37693376162/attempts/1) | Actual schedule rejected retired source; 66 historical ordinary log absolute_local_path locations; no clean-log claim; recorded_at: 2026-10-10; conclusion: success; head_sha: ae8a612b0e0be8b47b1a15d366b1a41eb3433677; event: schedule; evidence_class: actual_schedule_observation |
| ci_run | [https://github.com/mrnouiouat/calico/actions/runs/37985365302/attempts/1](https://github.com/mrnouiouat/calico/actions/runs/37985365302/attempts/1) | Recorded fixture-hosted replay: 32 dbt models and 228 tests passed on its historical head; recorded_at: 2026-10-09; conclusion: success; head_sha: 3bd3e43bb9cc0f7222559c2466c64488fe787685; event: workflow_dispatch; evidence_class: fixture_hosted_replay |
| file_sha256 | [docs/decisions/condition-4-hosted-outcomes.md](../../docs/decisions/condition-4-hosted-outcomes.md) | Approved 2026-10-10 six-residual amendment; historical log limits are disclosed without a privacy waiver; sha256: 444ade2ad968bb570d66ec5ac775fa96666212758ca2879dc52e7cc893dda635 |
| manifest | [docs/evidence/gate-e/hosted-replay-v1.json](../../docs/evidence/gate-e/hosted-replay-v1.json) | Actual Jobs API conclusions, reconstruction digest equality, fixture SQL and bytes-derived privacy audits; isolated atomic publication; sha256: 7a5abc056144409c7efd29eafcff6dffa7babf7d05169f0487e788bb824136f6 |

Public amendment: [2026-10-10](../../docs/decisions/condition-4-hosted-outcomes.md); SHA-256: `444ade2ad968bb570d66ec5ac775fa96666212758ca2879dc52e7cc893dda635`.

## Condition 5

an accepted release automatically rebuilds DuckDB/dbt and atomically updates public manifests and approved aggregate and named lookup outputs.

Status: `pass`.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| ci_run | [https://github.com/mrnouiouat/calico/actions/runs/37726333085/attempts/1](https://github.com/mrnouiouat/calico/actions/runs/37726333085/attempts/1) | Immutable real B2 restore/build/publication mechanism; exact no_change; published manifest da4f4a3385f674adc4aea66044ad977a8f8dea86fdc10a7ca5607f918755e7ed; policy 91ab3bf914ef26102d48e44d44446fd02a4048440760bc64e9c8b95ce6f3aada; published commit 45cd920e3087a3f14d6bb196d15c49abc980f665; recorded_at: 2026-10-08; conclusion: success; head_sha: ae8a612b0e0be8b47b1a15d366b1a41eb3433677; event: workflow_dispatch; evidence_class: real_restore_republish_observation |
| file_sha256 | [docs/provenance/HOSTED-REPLAY-EVIDENCE.md](../../docs/provenance/HOSTED-REPLAY-EVIDENCE.md) | Accepted publisher succeeded; repeat/rejected/calendar/failure/cancel publishers skipped; isolated transaction and exact unchanged negative boundaries; sha256: 37d44bb3c397f648c25772a569bfec823f2f42ea78890e1975076f4ac455954a |
| manifest | [docs/evidence/gate-e/hosted-replay-v1.json](../../docs/evidence/gate-e/hosted-replay-v1.json) | Actual Jobs API conclusions, reconstruction digest equality, fixture SQL and bytes-derived privacy audits; isolated atomic publication; sha256: 7a5abc056144409c7efd29eafcff6dffa7babf7d05169f0487e788bb824136f6 |

## Condition 6

the Power BI semantic model refreshes from stable published URLs and the public report reflects the new accepted release, with one manual refresh as documented fallback.

Status: `pass`.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| file_sha256 | [docs/powerbi-refresh-runbook.md](../../docs/powerbi-refresh-runbook.md) | Stable published Web sources; existing manual Refresh now fallback; scheduled refresh reliability is not proved; sha256: f74d4eb557b5d32a70f3e5d9d33d01ac954309757dbdde6076f78dcbbf72ff0b |
| owner_attestation | [docs/powerbi-refresh-runbook.md#power-bi-refresh-and-owner-acceptance-runbook](../../docs/powerbi-refresh-runbook.md#power-bi-refresh-and-owner-acceptance-runbook) | Recorded owner Service observation 2026-09-22: native Refresh now completed; accepted 2026-08-19 revision 1; latest attempt rejected; source retired. No new Service lookup or public embed; recorded_at: 2026-09-22; conclusion: observed |

## Condition 7

a publication gate proves only allowlisted aggregate fields and approved organization-history fields enter the public report model.

Status: `pass`.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| file_sha256 | [contracts/publication-exports-v3.json](../../contracts/publication-exports-v3.json) | Approved aggregate and bounded named-history fields; sha256: e0c7a9e4507a771ccf2d4c14434a5fcbdc8a52f81819ebc991dd55acc2795fc5 |
| file_sha256 | [powerbi/semantic-model-inventory-v1.json](../../powerbi/semantic-model-inventory-v1.json) | Complete visible, hidden, calculated, measure and relationship model surface; sha256: 034ddd661972ae685041b4d5dc1998e32c9ecc87e59ac6ef356d4469f22411ff |
| test_id | [tests.publish.test_gate.PublicationGateFixtureTests.test_01_committed_baseline_loads_and_passes](https://github.com/mrnouiouat/calico/blob/main/tests/publish/test_gate.py) | Committed fixture publication passes the field gate |
| test_id | [tests.publish.test_inventory.InventoryTests.test_v3_control_source_and_each_cross_class_relationship](https://github.com/mrnouiouat/calico/blob/main/tests/publish/test_inventory.py) | Semantic-model source and relationships remain within the v3 boundary |

## Condition 8

the README explains question, data grain, lineage, limitations, refresh procedure, and findings.

Status: `pass`.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| file_sha256 | [README.md](../../README.md) | Question, grains, SQL lineage, limitations, refresh procedure and findings; sha256: dc89e86085b3faf8fe46a859f23424597ddb6ddc5df66bdec6772ff97cc22ced |
| test_id | [tests.docs_public.test_readme.ReadmeContracts.test_complete_readme_has_all_decided_topics_and_separate_refresh](https://github.com/mrnouiouat/calico/blob/main/tests/docs_public/test_readme.py) | README content and refresh dates are independently enforced |

## Condition 9

a concise written walkthrough exists.

Status: `pass`.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| file_sha256 | [docs/walkthrough.md](../../docs/walkthrough.md) | Concise written finding, SQL transformation, source defect and deliberate non-claim; sha256: 728dbd41b719eb6a67b678d877398bc165fdaf760896cb45640a74491ff3764f |
| test_id | [tests.docs_public.test_walkthrough.WrittenWalkthroughContracts.test_four_topics_exist_without_recording_or_hosting_prerequisites](https://github.com/mrnouiouat/calico/blob/main/tests/docs_public/test_walkthrough.py) | Written walkthrough has all four decided topics |

## Condition 10

core analytical logic is implemented and tested in DuckDB/dbt SQL with lineage and representative SQL techniques visible in the README, and Python and Power BI do not duplicate those calculations.

Status: `pass`.

| Evidence type | Openable locator | Claim |
| --- | --- | --- |
| file_sha256 | [AGENTS.md](../../AGENTS.md) | Python landing/admission; DuckDB/dbt analytical calculations; Power BI presentation; sha256: 328c00e8ae17ee3c12b359d7b550329de53ca129d5cdfa641bd02717ed3272c3 |
| file_sha256 | [docs/evidence/dbt-lineage-v1.json](../../docs/evidence/dbt-lineage-v1.json) | SQL model lineage; sha256: 521569155ad7eff5cb49fd3b113a0f1fdea1989807738bacc2c8d176592e4eb6 |
| file_sha256 | [docs/evidence/sql-excerpts-v1.json](../../docs/evidence/sql-excerpts-v1.json) | Representative SQL excerpts bound to source hashes; sha256: 6621e780efb051190f78d7a8c6ab4f7b48cb966b515aef4f2cd3b665e5c463c4 |
| test_id | [tests.docs_public.test_walkthrough.WrittenWalkthroughContracts.test_sql_excerpt_is_exact_and_bound_to_source_hashes](https://github.com/mrnouiouat/calico/blob/main/tests/docs_public/test_walkthrough.py) | Walkthrough SQL remains byte-exact and bound to model sources |
