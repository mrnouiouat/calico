<!-- calico-provenance-v1:start -->
**Historical record — contains superseded figures and guidance.**

Imported 2026-10-01. This records the predecessor Mitos investigation and Windows workspace; historical paths describe that workspace, not this repository.

The original body below is preserved byte for byte. Historical rules apply only to their original context; the following supersedes pairs and current authority govern the monitor.

| Superseded claim | Corrected successor | Evidence or decision |
|---|---|---|
| Predecessor project name (body lines 5, 11, 15, 167): Predecessor project name | California Charity Registry Monitor (D-001) | [Authority](../../../decisions/register.md) |
| Aggregate-only publication (body lines 49, 76, 105, 125): Aggregate-only publication | Bounded named organization history alongside aggregates (D-007) | [Authority](../../../decisions/register.md) |
| Conditional BI choice (body lines 113, 123, 148): Conditional BI choice | One Power BI implementation; documented manual refresh fallback (D-008) | [Authority](../../../decisions/register.md) |
| Archive census prerequisite (body lines 154): Archive census prerequisite | Archive census is outside v1 (D-012) | [Authority](../../../decisions/register.md) |
| Default CSV interpretation (body lines 29): Default CSV interpretation | CP1252 with QUOTE_NONE; no embedded record newlines (D-003) | [Authority](../../../decisions/register.md) |
| Deferred duration analysis (body lines 64, 84, 130): Deferred duration analysis | No Turnbull survival, restricted mean duration, constant-hazard equivalent or standardized 30-day risk in v1; final panel has three releases spanning 35 days. Separate evaluation requires all five estimability conditions (D-010) | [Authority](../../../decisions/register.md) |
| Historical diagnostic denominator (body lines 94): Historical diagnostic denominator | Historical strict-cure percentages are not current governed metrics; the current last-renewal diagnostic uses all observed exits independently of parser repair | [Authority](../../../../contracts/metric-denominators-v1.json) |

Current authority: [Gate A correction index](../../../evidence/gate-a/correction-index-v1.json), [recomputed spike evidence](../../../evidence/gate-a/spike-002-successor-v1.json), and [controlling decisions](../../../decisions/register.md).

<!-- calico-provenance-v1:end -->
---
spike: 005
name: project-recommendation
type: standard
validates: "Given the verified archive sample, exact entity diff, censoring-aware metric contract, and audited job evidence, when scope is optimized for a one-to-two-week portfolio build, then a Public Registry Operations Monitor with automated capture and anomaly flags can be recommended without speculative complexity."
verdict: VALIDATED
related: [001, 002, 003, 004]
tags: [dbt, portfolio, architecture, bi, operations-monitoring]
---

# Spike 005: Public Registry Operations Monitor recommendation

## Decision

Build a **Public Registry Operations Monitor** answering:

> Is the public registry's delinquent backlog growing or shrinking, which cohorts remain in that status, and can each published release be trusted?

This is an outside-in public-data prototype, not an internal Attorney General operations system. It monitors observable registry states, cohort persistence, release integrity, and unusual bulk movements. It does not claim to measure staffing, internal processing time, causes, or enforcement performance.

The exact July comparison supplies a consequential operational signal—the observed delinquent population grew from 5,476 to 13,169 in one release gap—while the damaged archive capture proves that release reliability belongs inside the product rather than in engineering fine print.

## Dataset viability

**Viable, with an admission gate.** The bounded archive sample proves:

- a stable 11-column logical schema across 2019 XLSX, 2022 CSV, and 2026 CSV;
- a complete four-file July 2026 release that produces an exact entity diff;
- structural embedded-newline behavior that requires a real CSV parser; and
- a damaged 2024 archive capture that would pass a superficial HTTP-status check.

It does not prove that roughly 37 complete releases exist. Do not put that count in the README or résumé until the later build enumerates every candidate and applies the four-file admission contract. The project remains useful even if fewer sets survive, but duration precision and the number of reportable horizons depend on the accepted release count.

## Recommended repository structure for the later build

```text
public-registry-operations-monitor/
├── capture/                 # Python fetch, parse, admission, and manifest layer
├── .github/workflows/
│   ├── capture-current.yml  # scheduled first/third-Wednesday capture and flags
│   └── build-test.yml       # fixture build, dbt tests, and privacy checks
├── data/
│   ├── fixtures/            # synthetic, fast, identity-free CI fixture
│   └── manifests/           # pinned URLs, hashes, row counts, release verdicts
├── dbt_project/
│   ├── models/staging/      # trimmed source rows and normalized identifiers
│   ├── models/intermediate/ # accepted releases, keyed snapshots, IRS reductions
│   ├── models/facts/        # status transitions and interval-censored spells
│   └── models/marts/        # aggregate report inputs only
├── bi/                      # one BI implementation, chosen by the gate below
├── docs/                    # metric dictionary, limits, architecture, lineage
└── tests/                   # ingestion and privacy/leak checks
```

### Model grains

- Immutable source row: one row per archived list file, release identity, and source record.
- Keyed status snapshot: one nonblank full registration number per accepted release.
- Coverage mart: keyless rows by release, list, and status; never silently discarded.
- Transition fact: one keyed entity per adjacent accepted release pair.
- Status spell fact: one contiguous observed delinquency spell with onset/exit bounds and censoring flags.
- Capture-run fact: one attempted scheduled or manual capture with `accepted`, `no_new_release`, or `rejected` status and explicit reasons.
- Operational-flag fact: one deterministic release-integrity or movement flag with rule version, threshold, observed value, and release identities.
- Aggregate report marts: backlog prevalence, entries, exits, net movement, sweep-cohort movement, duration bounds/survival, censoring profile, and release quality.
- IRS models, if time remains: one correctly reduced revocation status per Employer Identification Number and one recent-filing rollup. Keep the two historical funnels separate unless rebuilt on the same release and denominator.

Reject rather than prioritize cross-list duplicate registration numbers. The current Python script has incidental overwrite order, but both validated releases had zero collisions and the four files are supposed to partition the registry.

## Automated current-release capture and flags

Automated capture is a core feature, not optional polish:

1. A scheduled workflow runs on the first and third Wednesday, with bounded retries while the source still exposes the previous accepted as-of date.
2. It downloads all four current files into temporary storage and verifies complete transfer, exact parsing, schema, one shared as-of date, row counts, hashes, and cross-list partition integrity.
3. An unchanged as-of date is recorded as `no_new_release` and retried rather than called corruption. A failed structural check rejects the entire set, preserves the previous accepted release as current, and records visible failure reasons.
4. An accepted set is compared with the prior accepted set and rebuilds aggregate marts and the public report.
5. The run surfaces flags for damaged or partial files, schema drift, abnormal row-count or coverage movement, and unusual bulk status transitions.
6. Temporary named rows are not committed publicly. The existing manual capture procedure remains the fallback because scheduled automation must not be the only preservation path.

Structural checks are deterministic. A statistically unusual but structurally valid release is admitted and flagged, not called damaged. Statistical movement thresholds must be versioned and calibrated from accepted historical releases; the monitor reports the rule and observed value rather than presenting every change as an emergency. Email alerts, downloadable investigation queues, and integrations into agency workflows are out of scope.

## Metrics

Use the contract in [Spike 003](../003-duration-metrics/README.md): observed gap risk tied to exact dates, lower/upper duration bounds, Turnbull interval-censored survival at fixed horizons, restricted mean duration, censoring shares, and interval-width diagnostics.

Do not publish:

- a per-release rate without the gap length;
- a naïvely annualized two-snapshot proportion;
- an exact delinquency start date derived from first observation or `Date Status Set`;
- disappearance as cure; or
- `Last Renewal` clearing as the event definition.

The `Last Renewal` transition remains a useful secondary diagnostic with three numbers: precision conditional on starting delinquent, sensitivity among eligible cures whose field began populated, and sensitivity across all strict cures. The July gap produced 87.0%, 100%, and 75.5%; the August gap produced 95.8%, 93.9%, and 50.5%. That variation is itself the reason to retain the metric as a diagnostic rather than a rule.

## Stack

| Layer | Recommendation | Reason |
|---|---|---|
| Capture and landing | Python standard library plus the minimum XLSX reader | Scheduled fetches, CP1252, quoted newlines, transfer validation, XLSX preambles, and provenance are ingestion concerns, not dbt SQL concerns |
| Analytical database | DuckDB | Local, credential-free, reproducible, and ample for this monitor; describe it honestly as an analytical database, not cloud-warehouse experience |
| Transformation | dbt Core with dbt-duckdb | Demonstrates model grain, dependencies, tests, documentation, and lineage on real temporal data |
| Automation | GitHub Actions on schedule, pushes, and pull requests | Scheduled capture and visible failures are core; fixture builds and tests protect changes; the schedule is redundant rather than the sole preservation path |
| Raw preservation | Pinned Internet Archive fetch plus private backup of accepted fresh releases and existing laptop-only releases | Reproducibility does not require public redistribution by the project owner; storage-provider choice is operational, not a portfolio feature |
| Publication | Aggregate-only semantic/report marts | Power BI Publish to web can expose underlying model data, not merely displayed visuals |

The official dbt-duckdb release page showed **1.10.1** as current during this audit, not the previously claimed 1.11.0: [duckdb/dbt-duckdb releases](https://github.com/duckdb/dbt-duckdb/releases). Pin compatible versions during the build after a smoke test; do not carry the incorrect version claim forward.

Exclude Snowflake, a separate orchestrator, a semantic layer, dbt Cloud, streaming, reverse extract-transform-load, the PDF corpus, named AB 488 scan rows, and `data/mitos.db`. The direct-posting audit does not justify their cost, and none is needed to answer the operational questions.

## Primary BI tool

**Choose Power BI as the primary BI tool if and only if the user's Publish to web test succeeds. If it fails, choose Evidence as the single fallback; do not build both.**

Why Power BI first:

- it appeared in two of the five readable disclosed analyst-family postings;
- the founder already used it in prior work, reducing delivery risk; and
- it is a recognizable BI artifact, while the supplied job evidence contains no auditable Evidence-demand sample.

Why the gate is non-negotiable: Microsoft states that a tenant administrator must enable Publish to web, the feature is disabled by default in security guidance, and viewers can access detail-level data in the model even when the report does not display it: [Microsoft Publish to web guidance](https://learn.microsoft.com/en-us/power-bi/collaborate-share/service-publish-to-web).

If the tenant blocks it, Evidence is a defensible public fallback because its official documentation describes SQL/Markdown reports and deployment as a static site: [Evidence documentation](https://docs.evidence.dev/). In that branch, omit the Power BI build rather than losing time maintaining two visual layers.

Only aggregate marts may enter either public artifact. No name, registration number, Employer Identification Number, address, or row-level lookup table belongs in the public report model.

## Finished monitor

1. **Registry operations:** current accepted release, observed delinquent backlog, entries, exits, net movement, the July 2026 sweep, and bulk-movement flags on actual release dates.
2. **Cohort persistence:** definite/possible duration bands, interval-censored persistence at 180/365/730 days, the incident-versus-left-censored split, and the IRS activity segment only if the core is complete.
3. **Release reliability:** capture-run history, accepted/rejected releases, damaged-file and schema flags, trackable coverage, censoring profile, interval widths, source hashes, and `Last Renewal` diagnostic metrics.

The landing page should lead with backlog movement and the July sweep, while a prominent badge identifies the latest accepted release and any more recent rejected attempt. The limitations section is part of the product, not fine print.

## Usefulness boundary

The build deliberately excludes stakeholder interviews, internal organization-level tooling, investigation queues, email notifications, predictions, causal explanations, and recommendations about individual organizations. Those would materially change the scope or require authority and data the public project does not have.

## Post-build usefulness validation

After the monitor is complete, its practical usefulness should be tested without adding new product scope: show it to an Attorney General employee, nonprofit association, charity-policy researcher, or public-data practitioner and document whether it caught a real release defect, surfaced an unexplained movement, or answered an operational question they previously handled manually.

## Hiring-signal interpretation

The direct employer audit supports only directional conclusions. SQL appeared most often among readable disclosed analyst roles; communication and implementation appeared in every readable solutions record. Therefore:

- dbt and DuckDB earn inclusion because they make the temporal model, tests, and lineage demonstrable—not because job-posting prevalence was proven;
- Power BI earns first choice from limited direct evidence plus existing familiarity—not a claimed Texas market lead; and
- the README and walkthrough must explain stakeholder question, metric choices, damaged-release handling, and the decision not to annualize. That communication is at least as important as the tool diagram for the target roles.

## Later build sequence

1. Run the Power BI tenant test before dashboard work.
2. Enumerate archive candidates and produce the complete admitted/rejected release manifest; fetch only after the census is defined.
3. Build and test capture, release atomicity, and rejected-run recording.
4. Build trimmed staging, keyed snapshots, coverage, and the scheduled current-release workflow.
5. Build transitions, interval-censored spells, operational flags, and the core aggregate marts.
6. Publish the three-page monitor in the gated BI tool.
7. Add IRS reductions and the separate funnels only if the core is reconciled and time remains.
8. Finish with a walkthrough, metric dictionary, limitations, and evidence-backed résumé bullets.
9. After completion, seek one external operational-usefulness test and record the outcome separately from the build claims.

The project is portfolio-ready after step 6. Scheduled capture and flags replace generic reporting work rather than creating a second product; the expected schedule remains roughly one to two focused weeks, with approximately half a day to one day of additional workflow wiring. Steps 7–9 deepen or validate it; extra infrastructure does not.

## Verdict

**VALIDATED, with two explicit gates.** The Public Registry Operations Monitor is the recommended direction. It preserves the validated data architecture while adding operational usefulness through scheduled capture, atomic admission, and visible integrity and movement flags. The later build must first establish the complete accepted-release inventory, and Power BI remains primary only if Publish to web is available. The only manual task before planning is that tenant test.
