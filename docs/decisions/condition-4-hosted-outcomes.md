# Condition 4 evidence disposition

Status: `pass_with_disclosed_deviation`.
Derived from recorded observations dated 2026-10-10.
Fixture acceptance is not live-source acceptance. Dispatched calendar cases are not actual scheduled observations.
The historical real restore/republish proves its recorded mechanism; it is not current-head accepted-trigger proof.

Hosted fixture outcomes: accepted, no_new_release, rejected.

Public amendment recorded 2026-10-10.

| Evidence class | Outcome | Residual reason |
| --- | --- | --- |
| live_source_observation | accepted | not_observed |
| live_source_observation | no_new_release | not_observed |
| live_source_observation | privacy_boundary | historical_log_privacy_not_clean |
| actual_schedule_observation | accepted | not_observed |
| actual_schedule_observation | no_new_release | not_observed |
| actual_schedule_observation | privacy_boundary | historical_log_privacy_not_clean |

## Recorded evidence

- fixture_hosted_replay: [recorded attempt](https://github.com/mrnouiouat/calico/actions/runs/37985365302/attempts/1), head `3bd3e43bb9cc0f7222559c2466c64488fe787685`, 2026-10-09, hosted fixture outcomes.
  Validated public evidence SHA-256: `7a5abc056144409c7efd29eafcff6dffa7babf7d05169f0487e788bb824136f6`; actual event `workflow_dispatch`; conclusion `success`.
- live_source_observation: [recorded attempt](https://github.com/mrnouiouat/calico/actions/runs/36812428040/attempts/1), head `9297d1db4e381f3da7ae03ba7e9ed57812d12a9b`, 2026-10-10, rejected.
  Historical log privacy: absolute_local_path_findings, 66 locations; no clean claim or waiver.
  Actual event `workflow_dispatch`; conclusion `success`; outcome basis `recorded_capture_status`; created 2026-10-01T03:51:29Z; completed 2026-10-01T03:57:21Z.
- actual_schedule_observation: [recorded attempt](https://github.com/mrnouiouat/calico/actions/runs/36779395262/attempts/1), head `9297d1db4e381f3da7ae03ba7e9ed57812d12a9b`, 2026-10-10, not_observed.
  Historical log privacy: absolute_local_path_findings, 22 locations; no clean claim or waiver.
  Actual event `schedule`; conclusion `success`; outcome basis `no_capture_status_observed`; created 2026-09-30T21:26:05Z; completed 2026-09-30T21:26:14Z.
- actual_schedule_observation: [recorded attempt](https://github.com/mrnouiouat/calico/actions/runs/37693376162/attempts/1), head `ae8a612b0e0be8b47b1a15d366b1a41eb3433677`, 2026-10-10, rejected.
  Historical log privacy: absolute_local_path_findings, 66 locations; no clean claim or waiver.
  Actual event `schedule`; conclusion `success`; outcome basis `recorded_capture_status`; created 2026-10-07T22:01:00Z; completed 2026-10-07T22:10:09Z.
- real_restore_republish_observation: [recorded attempt](https://github.com/mrnouiouat/calico/actions/runs/37726333085/attempts/1), head `ae8a612b0e0be8b47b1a15d366b1a41eb3433677`, 2026-10-08T04:14:36Z, historical real no_change.
  Historical published commit `45cd920e3087a3f14d6bb196d15c49abc980f665`; manifest SHA-256 `da4f4a3385f674adc4aea66044ad977a8f8dea86fdc10a7ca5607f918755e7ed`; policy SHA-256 `91ab3bf914ef26102d48e44d44446fd02a4048440760bc64e9c8b95ce6f3aada`; transaction `no_change`.

## Additive successor

D-04 offline-only outcome fallback and original 10-07 ordering → D-14 measured hosted replay before residual approval.
