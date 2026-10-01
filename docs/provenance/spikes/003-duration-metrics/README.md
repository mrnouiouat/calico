<!-- calico-provenance-v1:start -->
**Historical record — contains superseded figures and guidance.**

Imported 2026-10-01. This records the predecessor Mitos investigation and Windows workspace; historical paths describe that workspace, not this repository.

The original body below is preserved byte for byte. Historical rules apply only to their original context; the following supersedes pairs and current authority govern the monitor.

| Superseded claim | Corrected successor | Evidence or decision |
|---|---|---|
| Predecessor project name (body lines 58): Predecessor project name | California Charity Registry Monitor (D-001) | [Authority](../../../decisions/register.md) |
| Deferred duration analysis (body lines 8, 17, 47, 49, 50, 64, 71, 76, 86): Deferred duration analysis | No Turnbull survival, restricted mean duration, constant-hazard equivalent or standardized 30-day risk in v1; final panel has three releases spanning 35 days. Separate evaluation requires all five estimability conditions (D-010) | [Authority](../../../decisions/register.md) |
| Historical diagnostic denominator (body lines 50): Historical diagnostic denominator | Historical strict-cure percentages are not current governed metrics; the current last-renewal diagnostic uses all observed exits independently of parser repair | [Authority](../../../../contracts/metric-denominators-v1.json) |

Current authority: [Gate A correction index](../../../evidence/gate-a/correction-index-v1.json), [recomputed spike evidence](../../../evidence/gate-a/spike-002-successor-v1.json), and [controlling decisions](../../../decisions/register.md).

<!-- calico-provenance-v1:end -->
---
spike: 003
name: duration-metrics
type: standard
validates: "Given irregular registry snapshots and incomplete observation of spell boundaries, when persistence is measured, then the published metrics retain interval, left, and right censoring instead of imputing exact event dates."
verdict: VALIDATED
related: [002, 005]
tags: [survival-analysis, interval-censoring, metrics, dbt, operations-monitoring]
---

# Spike 003: Defensible persistence and duration metrics

## What This Validates

The historical panel can support duration analysis, but snapshot dates do not reveal exact status-change dates. A model that treats the first delinquent snapshot as the onset date and the first non-delinquent snapshot as the exit date will systematically turn observation times into invented event times. Irregular gaps make that distortion inconsistent across spells.

The appropriate primary estimator is the nonparametric maximum-likelihood survival distribution for grouped and interval-censored observations described by Bruce Turnbull, not ordinary Kaplan-Meier with imputed snapshot dates: [Turnbull, 1976](https://academic.oup.com/jrsssb/article/38/3/290/7027379).

## Spell contract

Build one spell for each contiguous observed run of the two delinquent statuses. For a spell:

- `onset_left` is the date of the last prior complete release where the entity was observed outside delinquency.
- `onset_right` is the first release where it was observed delinquent. Actual onset lies in `(onset_left, onset_right]`.
- `exit_left` is the last release where it remained delinquent.
- `exit_right` is the first later complete release where it was observed outside delinquency. Actual exit lies in `(exit_left, exit_right]`.
- Missing `onset_left` means the spell is left-censored.
- Missing `exit_right` at the last complete release means it is administratively right-censored.
- Disappearance from all lists is not an exit. Mark it as lost after `exit_left`, report it separately, and right-censor the observed spell at its last known delinquent release.
- Re-entry after an observed exit starts a new spell rather than extending the old one.

For a spell with both boundaries observed, the defensible duration interval is:

```text
lower bound = exit_left - onset_right
upper bound = exit_right - onset_left
```

A one-snapshot spell can therefore have a zero-day lower bound and a long upper bound. That uncertainty is real and should remain visible.

## Publishable metrics

| Metric | Definition | Why it is defensible |
|---|---|---|
| Release prevalence | Delinquent keyed entities divided by all keyed entities at each complete release | A point-in-time fact; irregular spacing does not alter the denominator |
| Observed gap exit risk | Starting-delinquent entities observed outside delinquency at the next release divided by starting-delinquent entities, always labeled with both dates and gap days | Directly observed without inventing event time; never annualize it |
| Definite duration share | Share of spells whose lower bound is at least 180, 365, or 730 days | A conservative descriptive statement that survives interval uncertainty |
| Possible duration share | Share of spells whose upper bound reaches the same thresholds | Shows the uncertainty envelope beside the conservative bound |
| Interval-censored survival | Turnbull estimate of `S(180)`, `S(365)`, and `S(730)` for incident spells, with entity-level bootstrap confidence intervals | Uses the observation intervals directly and retains ongoing spells as right-censored |
| Restricted mean duration | Area under the interval-censored survival curve through a fixed horizon such as 730 days | Remains defined when median duration is not identified or not reached |
| Censoring profile | Counts and shares of incident, left-censored, right-censored, both-censored, and disappeared spells | Makes the limits of the duration estimates auditable |
| Interval-width profile | Median, 90th percentile, and maximum onset/exit interval widths | Shows how archive irregularity affects precision |

Report persistence estimates for **incident spells**—those with an observed prior non-delinquent state—as the primary duration analysis. Show spells already delinquent at their first appearance as a separate prevalent/left-censored cohort rather than blending them into onset-based estimates.

## Operational use

For the Public Registry Operations Monitor, these metrics describe backlog persistence rather than internal staff performance. The report should show whether the observed delinquent population is accumulating, turning over, or dominated by long-running cohorts, while preserving the uncertainty created by irregular snapshots.

The public data cannot identify internal processing time, staffing demand, the reason an organization remains delinquent, or which action an official should take. The monitor should make those limits explicit rather than relabeling observed status duration as agency workload.

## Secondary rate, only with an explicit assumption

If stakeholders require a comparable per-day number for a single gap, publish a **constant-hazard equivalent**, not `exit proportion / days`:

```text
lambda = -ln(1 - exits / starting_at_risk) / gap_days
standardized h-day risk = 1 - exp(-lambda * h)
```

This is a model-based translation that assumes a constant hazard within the gap. It must be labeled as such and accompanied by the directly observed gap risk. Do not average per-gap rates without weighting or call the result an observed daily rate.

## Required sensitivity checks

1. Fit incident spells only, then repeat with left-censored spells included using their censoring bounds.
2. Compare Turnbull results with early-boundary, midpoint, and late-boundary imputations; disagreement is a precision warning, not a choice of the most attractive estimate.
3. Recompute with disappearances as right-censored and as a separate competing outcome.
4. Stratify the July 2026 bulk-sweep cohort from pre-existing delinquency; never use the former to estimate a historical steady-state cure rate without showing the latter.
5. Bootstrap by entity, not by row or spell, so repeated spells from one entity stay together.
6. Exclude partial or internally inconsistent releases before spell construction.

## Investigation Trail

1. Dividing the August 5 to August 19 cure proportion by 14 was rejected because it treats all events as if exposure were known and constant.
2. Ordinary Kaplan-Meier on first-observed dates was rejected because both onset and exit are interval-censored.
3. A simple pair of lower/upper duration bands was retained alongside the Turnbull estimate because it is easy to explain in a public operations report and does not hide uncertainty behind a statistical method.
4. Disappearance was kept outside the cure definition because the source lists are not a complete census of every entity at every release.

## Verdict

**VALIDATED.** The operations monitor supports defensible duration analysis if the project stores interval bounds and censoring flags as first-class fields. It does not support exact delinquency-age claims, naïve per-release trends, annualization of a two-snapshot transition percentage, or claims about internal agency productivity.
