# Hosted fixture replay evidence

Generated from the closed public replay projection. Audit entries commit to actual scanned byte lengths and SHA-256 values.

Run: [GitHub attempt 1](https://github.com/mrnouiouat/calico/actions/runs/37985365302/attempts/1); head: [deployment commit](https://github.com/mrnouiouat/calico/commit/3bd3e43bb9cc0f7222559c2466c64488fe787685).
Event: `workflow_dispatch`; conclusion: `success`; input profile: `fixture`.

Checkpoint state was reconstructed across fresh jobs; matching input and provenance digests bind preparation to publication. Capture status timestamps are separate from analytical outputs. Controlled calendar/failure/cancel cases are host-evaluated negatives.

Fixture acceptance is not live-source acceptance. Dispatched calendar inputs are not actual cron execution. Dated live-source and dated actual-schedule observations require their own evidence; neither is observed by this replay.

| Evidence class | Observation |
| --- | --- |
| fixture_hosted_replay | measured |
| live_source_observation | not_observed_by_replay |
| actual_schedule_observation | not_observed_by_replay |
| real_restore_republish_observation | preserved |

| Job | Jobs API ID | Conclusion |
| --- | --- | --- |
| audit-safe-evidence | 114006628140 | success |
| calendar-matrix | 114005908440 | success |
| prepare-accepted | 114005908871 | success |
| prepare-rejected | 114005908854 | success |
| prepare-repeat | 114005908812 | success |
| publish-accepted | 114006257716 | success |
| publish-calendar-refused | 114006001617 | skipped |
| publish-cancelled | 114005999117 | skipped |
| publish-failure | 114005998626 | skipped |
| publish-rejected | 114006254093 | skipped |
| publish-repeat | 114006192858 | skipped |
| route-accepted | 114006214623 | success |
| route-calendar-refused | 114005950854 | success |
| route-cancelled | 114005950776 | success |
| route-failure | 114005950662 | success |
| route-rejected | 114006204866 | success |
| route-repeat | 114006142826 | success |

| Route scenario | Should publish | Route | Worker |
| --- | --- | --- | --- |
| accepted | true | success | success |
| repeat | false | success | skipped |
| rejected | false | success | skipped |
| calendar-refused | false | success | skipped |
| failure | false | success | skipped |
| cancelled | false | success | skipped |

## prepare-accepted

Evidence: `staged_prepare`; reconstructed: `false`.
Actual SQL: 32 models / 228 tests.
Input digest: `ee6199ae5e0bf47159267cd558a5706cbbd1917f3c1f0c3a72f73e9e60b1a19c`; provenance digest: `65aecd4f64b72ef3c283197434f08eef5cedd69452bc655888732a7d25a202ea`.
Analytical digest: `30f172fac804a63486175120afc7f4e82f551254ddc2270c788822e7029823c3`; baseline: `8f2149bbcf1b45956ce744b4e3d3816d6e3737d383dfdf3abbfdfecf1d804ada`.

| Outcome | Reason | Builds | Publications | Status digest |
| --- | --- | --- | --- | --- |
| accepted | none | 1 | 0 | 02a764b399260ff50614e110cbc39bcc15349413f4b888beaf796210c793c407 |

| Export | Rows | SHA-256 |
| --- | --- | --- |
| dim_public_organizations | 8 | 00d8f943aeacb6c96befa776d05ee524456c21f582b93d393f6c76e884114e11 |
| fct_public_status_observations | 24 | acc9b8f49fb0f0274e48ad9fa92310924aa288e8cd193ce04358cada1d9e1cc9 |
| mart_adjacent_pair_metrics | 2 | 40201574a18d58ffd5d0c294c39f864a016dd54ba059daf886af15de06d0d481 |
| mart_claim_support | 1 | 723c7a098b4540448d63d8593e3090d4839a49b5ed033bc52a483bd6b163f0af |
| mart_last_renewal_diagnostic | 6 | a1cd4cd7dacf17abbdf88e26dee6d35b316cb141ac68d6ede15850044ff8db4c |
| mart_publication_status | 1 | 0b6e7974cfc4def67fa33bfde0a0ce7383a2cf3e7348853bb922a5f2ee39612f |
| mart_registry_population_coverage | 12 | 477a49b1c6b0500dcb6cf4f4ba340583e16c056c2507707c4f719a6e5711b7db |
| mart_release_quality | 3 | 3c786f64c41a5fa88494188cf43ab5016b95b12d6dfbdfa99e5ae1e72ecf230d |
| mart_release_snapshot_metrics | 6 | cecb46d8ce07b1d155dec173613e80e80765d5730c0238533c377ca3525d9086 |
| mart_source_reported_status_age | 4 | 4dd2f750b20b7d1c0f92818e2e75f80d463cf13fbca9b26b63696087d586e3a9 |
| mart_spell_censoring_summary | 1 | 29411133e43a28d9d51315396260bb15b7f05676a44811cc106fa3d1979c452e |
| mart_starting_cohort_persistence | 2 | 75f9d205a30d8f87a04d47f50bc19a5b6e474701ebe45e343c43dfba59937fd8 |

| Audited surface | Bytes | SHA-256 | Findings |
| --- | --- | --- | --- |
| publication | 37471 | 84d7774cd83c144a431ad47c3527a67fd3a9f9c25f4a3ebceb750ee6ee7cf8ee | 0 |
| status | 366 | 02a764b399260ff50614e110cbc39bcc15349413f4b888beaf796210c793c407 | 0 |
| stderr | 0 | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | 0 |
| stdout | 367 | e50dd3755ca91708dbe82a02ae0bca35ec00fcc4830848b27c1019099129ce4f | 0 |
| summary | 83 | 076c686f59ca20a76180a6e74659325f3cd10f9fed91df8937fdb3bdbea5638d | 0 |

## prepare-repeat

Evidence: `staged_prepare`; reconstructed: `false`.
Actual SQL: 32 models / 228 tests.
Input digest: `ee6199ae5e0bf47159267cd558a5706cbbd1917f3c1f0c3a72f73e9e60b1a19c`; provenance digest: `65aecd4f64b72ef3c283197434f08eef5cedd69452bc655888732a7d25a202ea`.
Analytical digest: `8e975da40f9d308f31b9495efc6f5acf290fb96656aaae3f695e235bd645e659`; baseline: `8e975da40f9d308f31b9495efc6f5acf290fb96656aaae3f695e235bd645e659`.

| Outcome | Reason | Builds | Publications | Status digest |
| --- | --- | --- | --- | --- |
| no_new_release | source_not_advanced | 0 | 0 | 565fba2047605ea841ef96a9dc444e818f0a680c0eec91bebfd42e1c97df3b60 |

| Export | Rows | SHA-256 |
| --- | --- | --- |
| dim_public_organizations | 8 | 00d8f943aeacb6c96befa776d05ee524456c21f582b93d393f6c76e884114e11 |
| fct_public_status_observations | 24 | acc9b8f49fb0f0274e48ad9fa92310924aa288e8cd193ce04358cada1d9e1cc9 |
| mart_adjacent_pair_metrics | 2 | 40201574a18d58ffd5d0c294c39f864a016dd54ba059daf886af15de06d0d481 |
| mart_claim_support | 1 | 723c7a098b4540448d63d8593e3090d4839a49b5ed033bc52a483bd6b163f0af |
| mart_last_renewal_diagnostic | 6 | a1cd4cd7dacf17abbdf88e26dee6d35b316cb141ac68d6ede15850044ff8db4c |
| mart_publication_status | 1 | 0b6e7974cfc4def67fa33bfde0a0ce7383a2cf3e7348853bb922a5f2ee39612f |
| mart_registry_population_coverage | 12 | 477a49b1c6b0500dcb6cf4f4ba340583e16c056c2507707c4f719a6e5711b7db |
| mart_release_quality | 3 | 4fd4aca89ba44e141633b7497b7207d8646b026e2b4391e153ff1e20c29064ce |
| mart_release_snapshot_metrics | 6 | cecb46d8ce07b1d155dec173613e80e80765d5730c0238533c377ca3525d9086 |
| mart_source_reported_status_age | 4 | 4dd2f750b20b7d1c0f92818e2e75f80d463cf13fbca9b26b63696087d586e3a9 |
| mart_spell_censoring_summary | 1 | 29411133e43a28d9d51315396260bb15b7f05676a44811cc106fa3d1979c452e |
| mart_starting_cohort_persistence | 2 | 75f9d205a30d8f87a04d47f50bc19a5b6e474701ebe45e343c43dfba59937fd8 |

| Audited surface | Bytes | SHA-256 | Findings |
| --- | --- | --- | --- |
| publication | 42075 | 60cc57f995c6d47b28d87651a95a229051e40533b59594f8feda6bf93b9f8c4a | 0 |
| status | 388 | 565fba2047605ea841ef96a9dc444e818f0a680c0eec91bebfd42e1c97df3b60 | 0 |
| stderr | 0 | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | 0 |
| stdout | 389 | e570369e727d6883faa2cd02870bbbd8c0faa6a4b004028ba3cb38e214dde2f5 | 0 |
| summary | 81 | d2881cd70dbb8240885c7e86a47cfd9e4b3fed3d6d1eb6ec52d5fd355642f49d | 0 |

## prepare-rejected

Evidence: `staged_prepare`; reconstructed: `false`.
Actual SQL: 32 models / 228 tests.
Input digest: `ee6199ae5e0bf47159267cd558a5706cbbd1917f3c1f0c3a72f73e9e60b1a19c`; provenance digest: `65aecd4f64b72ef3c283197434f08eef5cedd69452bc655888732a7d25a202ea`.
Analytical digest: `8e975da40f9d308f31b9495efc6f5acf290fb96656aaae3f695e235bd645e659`; baseline: `8e975da40f9d308f31b9495efc6f5acf290fb96656aaae3f695e235bd645e659`.

| Outcome | Reason | Builds | Publications | Status digest |
| --- | --- | --- | --- | --- |
| rejected | source_contract_mismatch | 0 | 0 | 99d775a25cd689323be6585c936970683efd5c912a4b6880afbbe12ced60eeea |

| Export | Rows | SHA-256 |
| --- | --- | --- |
| dim_public_organizations | 8 | 00d8f943aeacb6c96befa776d05ee524456c21f582b93d393f6c76e884114e11 |
| fct_public_status_observations | 24 | acc9b8f49fb0f0274e48ad9fa92310924aa288e8cd193ce04358cada1d9e1cc9 |
| mart_adjacent_pair_metrics | 2 | 40201574a18d58ffd5d0c294c39f864a016dd54ba059daf886af15de06d0d481 |
| mart_claim_support | 1 | 723c7a098b4540448d63d8593e3090d4839a49b5ed033bc52a483bd6b163f0af |
| mart_last_renewal_diagnostic | 6 | a1cd4cd7dacf17abbdf88e26dee6d35b316cb141ac68d6ede15850044ff8db4c |
| mart_publication_status | 1 | 0b6e7974cfc4def67fa33bfde0a0ce7383a2cf3e7348853bb922a5f2ee39612f |
| mart_registry_population_coverage | 12 | 477a49b1c6b0500dcb6cf4f4ba340583e16c056c2507707c4f719a6e5711b7db |
| mart_release_quality | 3 | 4fd4aca89ba44e141633b7497b7207d8646b026e2b4391e153ff1e20c29064ce |
| mart_release_snapshot_metrics | 6 | cecb46d8ce07b1d155dec173613e80e80765d5730c0238533c377ca3525d9086 |
| mart_source_reported_status_age | 4 | 4dd2f750b20b7d1c0f92818e2e75f80d463cf13fbca9b26b63696087d586e3a9 |
| mart_spell_censoring_summary | 1 | 29411133e43a28d9d51315396260bb15b7f05676a44811cc106fa3d1979c452e |
| mart_starting_cohort_persistence | 2 | 75f9d205a30d8f87a04d47f50bc19a5b6e474701ebe45e343c43dfba59937fd8 |

| Audited surface | Bytes | SHA-256 | Findings |
| --- | --- | --- | --- |
| publication | 42075 | 60cc57f995c6d47b28d87651a95a229051e40533b59594f8feda6bf93b9f8c4a | 0 |
| status | 381 | 99d775a25cd689323be6585c936970683efd5c912a4b6880afbbe12ced60eeea | 0 |
| stderr | 0 | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | 0 |
| stdout | 382 | 5c99bae9bcffbec55fb2993237c3ef5f2164660c618559f8110b151713322f66 | 0 |
| summary | 83 | c4d8a0897be6d66aa8119a904740e64c6df02eed7ef7f828e1152bd076c0121c | 0 |

## publish-accepted

Evidence: `fresh_job_reconstruction`; reconstructed: `true`.
Actual SQL: 32 models / 228 tests.
Input digest: `ee6199ae5e0bf47159267cd558a5706cbbd1917f3c1f0c3a72f73e9e60b1a19c`; provenance digest: `65aecd4f64b72ef3c283197434f08eef5cedd69452bc655888732a7d25a202ea`.
Analytical digest: `30f172fac804a63486175120afc7f4e82f551254ddc2270c788822e7029823c3`; baseline: `8f2149bbcf1b45956ce744b4e3d3816d6e3737d383dfdf3abbfdfecf1d804ada`.

| Outcome | Reason | Builds | Publications | Status digest |
| --- | --- | --- | --- | --- |
| accepted | none | 1 | 1 | 02a764b399260ff50614e110cbc39bcc15349413f4b888beaf796210c793c407 |

| Export | Rows | SHA-256 |
| --- | --- | --- |
| dim_public_organizations | 8 | 00d8f943aeacb6c96befa776d05ee524456c21f582b93d393f6c76e884114e11 |
| fct_public_status_observations | 24 | acc9b8f49fb0f0274e48ad9fa92310924aa288e8cd193ce04358cada1d9e1cc9 |
| mart_adjacent_pair_metrics | 2 | 40201574a18d58ffd5d0c294c39f864a016dd54ba059daf886af15de06d0d481 |
| mart_claim_support | 1 | 723c7a098b4540448d63d8593e3090d4839a49b5ed033bc52a483bd6b163f0af |
| mart_last_renewal_diagnostic | 6 | a1cd4cd7dacf17abbdf88e26dee6d35b316cb141ac68d6ede15850044ff8db4c |
| mart_publication_status | 1 | 0b6e7974cfc4def67fa33bfde0a0ce7383a2cf3e7348853bb922a5f2ee39612f |
| mart_registry_population_coverage | 12 | 477a49b1c6b0500dcb6cf4f4ba340583e16c056c2507707c4f719a6e5711b7db |
| mart_release_quality | 3 | 3c786f64c41a5fa88494188cf43ab5016b95b12d6dfbdfa99e5ae1e72ecf230d |
| mart_release_snapshot_metrics | 6 | cecb46d8ce07b1d155dec173613e80e80765d5730c0238533c377ca3525d9086 |
| mart_source_reported_status_age | 4 | 4dd2f750b20b7d1c0f92818e2e75f80d463cf13fbca9b26b63696087d586e3a9 |
| mart_spell_censoring_summary | 1 | 29411133e43a28d9d51315396260bb15b7f05676a44811cc106fa3d1979c452e |
| mart_starting_cohort_persistence | 2 | 75f9d205a30d8f87a04d47f50bc19a5b6e474701ebe45e343c43dfba59937fd8 |

| Audited surface | Bytes | SHA-256 | Findings |
| --- | --- | --- | --- |
| publication | 42071 | fe8ffc794a4d2718da7f91bbd02039858df7efe11a34b6b1c44c70e4f0529918 | 0 |
| status | 366 | 02a764b399260ff50614e110cbc39bcc15349413f4b888beaf796210c793c407 | 0 |
| stderr | 0 | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | 0 |
| stdout | 367 | e50dd3755ca91708dbe82a02ae0bca35ec00fcc4830848b27c1019099129ce4f | 0 |
| summary | 93 | 6081cd631d22657b1352df65d03c53ee3aa06d8d47e7c012b9b6154b32517fc5 | 0 |

## Log audit commitments

| Executed job | Bytes | SHA-256 | Findings |
| --- | --- | --- | --- |
| audit-safe-evidence | 113780 | 2b08a1d4315fd9cf8e1d70634a34c5bd93fe51403ecaa73598912b1543cab369 | 0 |
| calendar-matrix | 17883 | b62373376e1cdf468258ad2741849efa48715d13df4e6b6cf11f7bd497311b67 | 0 |
| prepare-accepted | 52761 | 43d86c9bf21557ec840732cee854559afc74221bb2b3d473a6aa4f6c55f588bd | 0 |
| prepare-rejected | 52795 | f3b74449785f8c8cfae4c6b325c1ff1f08e0591439b42ce4822599876da373ee | 0 |
| prepare-repeat | 52793 | 70fb64055e6761cea986e3ad70e9b5903e4dc289772385076200fde00581f251 | 0 |
| publish-accepted | 53204 | d6cc3840554535888fd9381a6d0f41b0c47d256de096e3efe2e7a92e7b897df6 | 0 |
| route-accepted | 19338 | ba33cc15604c72146a2cda1a3ea559a9d690207b1062e3fc46df7f76a23fe0ac | 0 |
| route-calendar-refused | 18343 | 49eece9b42dd22d7d5b268f0994bc432b97aeb1176af2a64cec13a10a001cdc2 | 0 |
| route-cancelled | 18342 | f30a9a9ba4590857116d86e1f75a496b9d03348e7f0fef5b5d3ad32b89e00673 | 0 |
| route-failure | 18327 | 2a26a3063c4c33e39705e3ac563ab712797d065c7b2fbc1d6a30f6f4caa3d5f2 | 0 |
| route-rejected | 19088 | 074407520bddd170702f889cd7ca3826aaf170dc2c62d85ab91b4381460e7e7b | 0 |
| route-repeat | 19100 | 30f2d774b48936d72d0d5cb5816fc258cd375a5200e84fca59d156f0c7e68c4b | 0 |

## Preserved boundaries

Live boundary equality: `true`; artifacts: `0`; owned-root cleanup: `true`; result: `pass`.

Published-data commit: `45cd920e3087a3f14d6bb196d15c49abc980f665`.
Published-data tree: `e1d5e1bcd6114c406cac09fbc9a7651391e307d1`.
Published manifest raw-content SHA-256: `da4f4a3385f674adc4aea66044ad977a8f8dea86fdc10a7ca5607f918755e7ed`.
Private archive inventory: not observed by this replay; no archive digest is asserted. The separate historical restore/republish proof below retains its own scope.

| Protected ref | Observed SHA |
| --- | --- |
| refs/heads/main | 3bd3e43bb9cc0f7222559c2466c64488fe787685 |
| refs/heads/phase-8-handoff | e0394522956350415fa3757dc64f62c56ced22e4 |
| refs/heads/published-data | 45cd920e3087a3f14d6bb196d15c49abc980f665 |

Live published export hashes cover raw file bytes; counts cover data records.

| Live export | Rows | Raw-content SHA-256 |
| --- | --- | --- |
| dim_public_organizations | 247875 | 21bb5d6b729edad0afc0c79c972d0fb29b4c735da17fb381cefb2fac02bc5821 |
| fct_public_status_observations | 743625 | 717023d6f7e681973e548c1a72550e8051d62fe70e756431d818b77ad352e720 |
| mart_adjacent_pair_metrics | 2 | f6665a2c6baf9a9dcce6c75e220131725b90620a02f6cef1c7b064306165a0b3 |
| mart_claim_support | 1 | a4a500ac6a07f6392ef894321aa4a118ef96186f37c3de2cb99c15238c85573a |
| mart_last_renewal_diagnostic | 6 | ffe043db69ca25aaf0bb94ff88cef1fadf72f3903cdc76bf065007ba9b993bef |
| mart_publication_status | 1 | 4d736c57feabf2e5b97cce101277f2e8350a29c17ee198946990343d6681922f |
| mart_registry_population_coverage | 171 | 1e72361e75ee768ad09c74088ac647ce02e4109c2825add4da4bfee25ae3589c |
| mart_release_quality | 3 | 814afd784edbd32aee8ed776f68d91bfff4a9f55baf4283c23a010d1f8fb8654 |
| mart_release_snapshot_metrics | 6 | f82c0b3f427d044991487b83f2af4b6b45ce822791d0e97f271aae725d0d9176 |
| mart_source_reported_status_age | 13729 | 64fb3622e4ed6fb0873e48b584311ceb1061ba5bc9445e6957e5469097f84bb9 |
| mart_spell_censoring_summary | 2 | a9ac16af7e737d488bbddb429375fcc0ff686c11e8dfdd198c49581d71a9f6c2 |
| mart_starting_cohort_persistence | 2 | ae97bb09735f485f46a841bb08024dc69487e99d48d6980c6f4d7c585e2c6740 |

| Independent live control | Raw-content SHA-256 |
| --- | --- |
| authorization-probe-status.json | e9de45de82cd2b90456420c10b27e0cae75fa13cdb15d9292f25be998bb9b6b7 |
| capture-status.json | 0da6640265465f3c291ad1877bb678bdf97350938635373b6215bfc9d6fe6f73 |

## Separate historical real restore/republish

This immutable historical real proof is independent of the fixture-hosted replay above.
Run: [historical observation](https://github.com/mrnouiouat/calico/actions/runs/37726333085); head: [historical commit](https://github.com/mrnouiouat/calico/commit/ae8a612b0e0be8b47b1a15d366b1a41eb3433677).
Published-data commit: `45cd920e3087a3f14d6bb196d15c49abc980f665`; manifest SHA-256: `da4f4a3385f674adc4aea66044ad977a8f8dea86fdc10a7ca5607f918755e7ed`.
