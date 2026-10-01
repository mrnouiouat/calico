<!-- calico-provenance-v1:start -->
**Historical record — contains superseded figures and guidance.**

Imported 2026-10-01. This records the predecessor Mitos investigation and Windows workspace; historical paths describe that workspace, not this repository.

The original body below is preserved byte for byte. Historical rules apply only to their original context; the following supersedes pairs and current authority govern the monitor.

| Superseded claim | Corrected successor | Evidence or decision |
|---|---|---|
| Predecessor project name (body lines 5, 35): Predecessor project name | California Charity Registry Monitor (D-001) | [Authority](../../decisions/register.md) |
| Aggregate-only publication (body lines 12): Aggregate-only publication | Bounded named organization history alongside aggregates (D-007) | [Authority](../../decisions/register.md) |
| Conditional BI choice (body lines 19, 35): Conditional BI choice | One Power BI implementation; documented manual refresh fallback (D-008) | [Authority](../../decisions/register.md) |
| Archive census prerequisite (body lines 35): Archive census prerequisite | Archive census is outside v1 (D-012) | [Authority](../../decisions/register.md) |

Current authority: [Gate A correction index](../../evidence/gate-a/correction-index-v1.json), [recomputed spike evidence](../../evidence/gate-a/spike-002-successor-v1.json), and [controlling decisions](../../decisions/register.md).

<!-- calico-provenance-v1:end -->
# Spike Manifest

## Idea

Validate whether the proposed dbt portfolio project can credibly become a **Public Registry Operations Monitor** without beginning the build. The monitor uses longitudinal California charity-registry data to track observable backlog movement and persistence, automatically validate fresh releases, and surface integrity or bulk-movement flags. It is an outside-in public-data prototype, not an internal Attorney General operations system.

## Requirements

- This spike is scoping only: no new repository, dbt models, dashboard, CI, or historical backfill was created here.
- The later build includes automated first- and third-Wednesday capture, four-file atomic admission, `accepted`/`no_new_release`/`rejected` run recording, and visible release-integrity and bulk-movement flags.
- Scheduled automation is not the sole preservation mechanism; the manual capture procedure remains a fallback.
- Published artifacts are aggregate-only and contain no organization identity fields.
- Historical release identity includes source URL, archive timestamp, as-of date, list, revision, byte count, row count, and SHA-256.
- A release is atomic: one failed or missing list rejects the whole four-file set.
- Full nonblank `State Charity Reg#` is the longitudinal key. Keyless rows remain visible as coverage, and Employer Identification Number is not a fallback identity.
- Snapshots are immutable; transitions and interval-censored spells are derived.
- Disappearance is not cure.
- Duration metrics retain left, right, and interval censoring and never annualize a raw gap proportion.
- One BI implementation ships: Power BI if Publish to web works; Evidence otherwise.
- Stakeholder interviews, internal organization-level tooling, investigation queues, email notifications, predictions, causal explanations, and recommendations about individual organizations are out of scope.
- After completion, seek one external test of whether the finished monitor catches a real release problem or answers an operational question; this validates usefulness without expanding the build.

## Spikes

| # | Name | Type | Validates | Verdict | Tags |
|---|---|---|---|---|---|
| 001 | archive-sample-validation | standard | Representative historical captures parse and expose integrity risks | PARTIAL | internet-archive, provenance |
| 002 | entity-change-validation | standard | Exact July 15 to August 5 movement reconciles by keyed entity | VALIDATED | temporal-modeling, registry |
| 003 | duration-metrics | standard | Irregular snapshots support censoring-aware persistence metrics | VALIDATED | survival-analysis, metrics |
| 004 | job-posting-audit | standard | Disclosed employer evidence can be separated from unverifiable claims | PARTIAL | job-market, evidence-audit |
| 005 | project-recommendation | standard | Verified findings support a bounded operations monitor, automated capture, and one BI decision | VALIDATED | dbt, portfolio, bi, operations-monitoring |

## Overall verdict

**VALIDATED WITH GATES.** The recommended project is the Public Registry Operations Monitor. Proceed to a later build only after the archive census defines the accepted release inventory. Use Power BI only if the user's Publish to web tenant test succeeds; otherwise use Evidence as the single publication layer.
