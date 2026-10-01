<!-- calico-provenance-v1:start -->
**Historical record — contains superseded figures and guidance.**

Imported 2026-10-01. This records the predecessor Mitos investigation and Windows workspace; historical paths describe that workspace, not this repository.

The original body below is preserved byte for byte. Historical rules apply only to their original context; the following supersedes pairs and current authority govern the monitor.

| Superseded claim | Corrected successor | Evidence or decision |
|---|---|---|
| 2026-07-15 rows: 557,065 | 557,067 | [Authority](../../../evidence/gate-a/spike-002-successor-v1.json) |
| 2026-07-15 keyless_rows: 309,624 | 309,626 | [Authority](../../../evidence/gate-a/spike-002-successor-v1.json) |
| 2026-08-05 rows: 557,289 | 557,291 | [Authority](../../../evidence/gate-a/spike-002-successor-v1.json) |
| 2026-08-05 keyless_rows: 309,212 | 309,214 | [Authority](../../../evidence/gate-a/spike-002-successor-v1.json) |
| Public Registry Operations Monitor name | California Charity Registry Monitor (D-001) | [Authority](../../../decisions/register.md) |
| Aggregate-only publication | Bounded named organization history alongside aggregates (D-007) | [Authority](../../../decisions/register.md) |
| Power BI with an Evidence fallback | One Power BI implementation; documented manual refresh fallback (D-008) | [Authority](../../../decisions/register.md) |
| Release-count readiness | Five simultaneous estimability conditions; no formal survival analysis in v1 (D-010) | [Authority](../../../decisions/register.md) |
| Archive census as a prerequisite | Archive census is outside v1 (D-012) | [Authority](../../../decisions/register.md) |
| Newline-aware default CSV interpretation | CP1252 with QUOTE_NONE; there are no embedded record newlines (D-003) | [Authority](../../../decisions/register.md) |
| Strict-cure diagnostic denominators | All observed exits are the diagnostic target (D-020) | [Authority](../../../../contracts/metric-denominators-v1.json) |

Current authority: [Gate A correction index](../../../evidence/gate-a/correction-index-v1.json), [recomputed spike evidence](../../../evidence/gate-a/spike-002-successor-v1.json), and [controlling decisions](../../../decisions/register.md).

<!-- calico-provenance-v1:end -->
---
spike: 002
name: entity-change-validation
type: standard
validates: "Given the complete 2026-07-15 and 2026-08-05 registry releases, when rows are matched by full nonblank State Charity Reg#, then every status movement and delinquency transition can be counted exactly without treating disappearance as cure."
verdict: VALIDATED
related: [001, 003, 005]
tags: [dbt, registry, temporal-modeling, provenance, operations-monitoring]
---

# Spike 002: Exact entity changes, 2026-07-15 to 2026-08-05

## What This Validates

The existing entity key and transition definitions work on the newly recovered July 15 release. The comparison uses the full, trimmed, nonblank `State Charity Reg#`; rows without that key remain in release coverage counts but are not silently matched by Employer Identification Number.

## Method

- Retrieved the four July 15 files from pinned Internet Archive replay timestamps and parsed them with Python's `csv.DictReader`, CP1252 encoding, and newline-aware CSV handling.
- Parsed the four locally archived August 5 gzip files the same way.
- Required one `As-of Date` across each complete four-file release.
- Collapsed the four list files into one keyed entity map. There were no duplicate or conflicting registration numbers in either release.
- Defined delinquency as exactly `Delinquent` or `Delinquent - Late Fees Due`.
- Defined a strict exit as a transition to `Current`; broader exits include every destination outside those two delinquent statuses. Disappearance remains its own outcome.
- Canonical membership hashes use sorted registration numbers, each followed by a line-feed character, UTF-8 encoded, then SHA-256 hashed. They make set membership reproducible without publishing the identifiers.

The machine-readable evidence is in [entity-changes.json](./entity-changes.json).

## Results

### Release identity and coverage

| Measure | 2026-07-15 | 2026-08-05 |
|---|---:|---:|
| Total CSV records | 557,065 | 557,289 |
| Keyed entities | 247,441 | 248,077 |
| Rows without a registration number | 309,624 | 309,212 |
| Duplicate/conflicting registration numbers | 0 | 0 |

Across releases, 247,436 keyed entities were present in both; 641 were added and 5 disappeared. Of the common entities, 15,757 changed status and 231,679 did not.

### Delinquency movement

| Measure | Exact count |
|---|---:|
| Delinquent at 2026-07-15 | 5,476 |
| Strict transitions to `Current` | 53 |
| All exits from delinquency | 65 |
| Disappearances among the starting delinquent cohort | 0 |
| Newly delinquent | 7,758 |
| Net change | +7,693 |

The dominant movement was 7,733 entities from `Current - Reporting Incomplete` to `Delinquent`. That one transition explains 99.7% of newly delinquent entities and shows why the August 5 cohort cannot support a steady-state annual cure rate.

Starting-delinquent outcomes reconcile exactly to 5,476: 5,334 remained `Delinquent`, 77 remained or moved to `Delinquent - Late Fees Due`, 53 became `Current`, 5 became `Current - In Process`, 3 became `Current - Reporting Incomplete`, 3 became `Suspended`, and 1 received a dissolution waiver.

### `Last Renewal` diagnostic in this gap

- 182 keyed entities cleared a populated `Last Renewal` field registry-wide.
- 46 of those were delinquent on July 15.
- 40 of those 46 became `Current`, so precision conditional on starting delinquent was **87.0%**.
- All 40 strict cures that started with a populated field cleared it, so conditional sensitivity was **100%** in this gap.
- Across all 53 strict cures, sensitivity was **75.5%**.
- Registry-wide precision was only **22.0%**.

This is useful evidence that the field transition is a cohort-conditional diagnostic, not a registry-wide cure definition. The numbers also vary from the August 5 to August 19 gap, so none should be promoted as a timeless constant.

## Operational flag demonstrated by this comparison

The exact comparison supplies a concrete acceptance test for the Public Registry Operations Monitor. A normal automated run should emit a **bulk status movement** flag containing the two release identities, gap length, affected starting population, origin status, destination status, count, percentage, and threshold version.

The July 15 to August 5 run would flag both:

- 7,733 entities moving from `Current - Reporting Incomplete` to `Delinquent`; and
- net delinquent growth of 7,693, from 5,476 to 13,169.

The historical backfill should calibrate versioned thresholds. This one unusual gap proves the flag's usefulness but is not enough to define a universal cutoff. The monitor surfaces the anomaly for investigation; it does not infer the cause or prescribe an enforcement response.

## Investigation Trail

1. The Internet Archive index endpoint returned errors, but the Wayback timegate resolved a July request to exact July 31 capture timestamps whose origin `Last-Modified` headers were July 15.
2. All four downloaded files agreed on `As-of Date = 2026/07/15`, had the expected 11-column header, and totaled 557,065 records.
3. The exact comparison reproduced the previously described net delinquent increase of 7,693, but decomposed it into 7,758 entrants and 65 exits.
4. Membership hashes were added so a future implementation can verify the exact entity sets without placing named organizations in this repository.

## Verdict

**VALIDATED.** The recovered release is internally coherent, the entity-level diff is reproducible, and the July sweep is quantitatively large enough that August's delinquent cohort must be treated as a distinct entry cohort rather than a steady-state population. It also provides a real event the later operations monitor must flag automatically.
