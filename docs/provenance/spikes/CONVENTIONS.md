<!-- calico-provenance-v1:start -->
**Historical record — contains superseded figures and guidance.**

Imported 2026-10-01. This records the predecessor Mitos investigation and Windows workspace; historical paths describe that workspace, not this repository.

The original body below is preserved byte for byte. Historical rules apply only to their original context; the following supersedes pairs and current authority govern the monitor.

| Superseded claim | Corrected successor | Evidence or decision |
|---|---|---|
| Aggregate-only publication (body lines 17, 34): Aggregate-only publication | Bounded named organization history alongside aggregates (D-007) | [Authority](../../decisions/register.md) |
| Default CSV interpretation (body lines 7): Default CSV interpretation | CP1252 with QUOTE_NONE; no embedded record newlines (D-003) | [Authority](../../decisions/register.md) |

Current authority: [Gate A correction index](../../evidence/gate-a/correction-index-v1.json), [recomputed spike evidence](../../evidence/gate-a/spike-002-successor-v1.json), and [controlling decisions](../../decisions/register.md).

<!-- calico-provenance-v1:end -->
# Spike Conventions

Patterns established by the dbt portfolio validation spike. These are design contracts for later planning, not implementation performed in this repository.

## Data and provenance

- Parse registry CSV as CP1252 with a real CSV reader.
- Commit manifests and aggregate evidence, not named raw registry rows.
- Identify releases by as-of date, archive timestamp, list, revision, byte count, row count, and SHA-256.
- Admit a release only when all four expected list files pass transfer, schema, as-of-date, and parsed-record reconciliation checks.
- Reproduce known archive objects against committed sizes, row counts, and hashes; record those values as new immutable identity fields when a genuinely fresh release first arrives.

## Operations automation

- Run fresh-release capture automatically on the first- and third-Wednesday publication cadence, with bounded retries while the published as-of date remains unchanged.
- Record every capture attempt as `accepted`, `no_new_release`, or `rejected`; never replace the latest accepted release with a delayed or failed set.
- Rebuild aggregates only after atomic admission, then compare the new release with the previous accepted release.
- Reject the complete release for a missing or damaged transfer, schema mismatch, inconsistent as-of date, or cross-list duplicate.
- Admit a structurally valid release with a separate versioned-threshold flag for abnormal row counts, coverage movement, or bulk status transitions.
- Publish flags through build status and the release-reliability page; email notifications, investigation queues, and workflow integrations are outside scope.
- Keep the manual capture procedure as a fallback because scheduled automation is not the sole preservation path.

## Modeling

- Use full nonblank state registration number as the longitudinal key.
- Preserve keyless rows in coverage outputs; do not coalesce registration number and Employer Identification Number.
- Store immutable snapshots and derive transitions plus censoring-aware status spells.
- Treat cross-list duplicates as partition failures and disappearance as an unknown/lost outcome.

## Metrics and publication

- Couple every observed transition proportion to its exact start date, end date, and gap days.
- Store spell boundary intervals and censoring flags; do not impute snapshot dates as event dates in primary metrics.
- Publish organization-level data only as aggregates.
- Use one public BI layer selected by the Power BI availability gate.
- Describe the artifact as an outside-in public operations monitor; do not infer internal workload, cause, productivity, or an action for an individual organization from observed registry status.
