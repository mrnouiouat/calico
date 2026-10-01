<!-- calico-provenance-v1:start -->
**Historical record — contains superseded figures and guidance.**

Imported 2026-10-01. This records the predecessor Mitos investigation and Windows workspace; historical paths describe that workspace, not this repository.

The original body below is preserved byte for byte. Historical rules apply only to their original context; the following supersedes pairs and current authority govern the monitor.

| Superseded claim | Corrected successor | Evidence or decision |
|---|---|---|
| 2026-07-15 charities-undetermined-status parsed rows (parser repair) (body lines 38): 128,475 | 128,477 | [Authority](../../../evidence/gate-a/spike-001-successor-v1.json) |
| 2026-07-15 total rows (parser repair) (body lines 35): 557,065 | 557,067 | [Authority](../../../evidence/gate-a/spike-001-successor-v1.json) |
| 2026-07-15 total rows (parser repair) (body lines 45): 557,065 | 557,067 | [Authority](../../../evidence/gate-a/spike-001-successor-v1.json) |
| Predecessor project name (body lines 64): Predecessor project name | California Charity Registry Monitor (D-001) | [Authority](../../../decisions/register.md) |
| Aggregate-only publication (body lines 70): Aggregate-only publication | Bounded named organization history alongside aggregates (D-007) | [Authority](../../../decisions/register.md) |
| Archive census prerequisite (body lines 77): Archive census prerequisite | Archive census is outside v1 (D-012) | [Authority](../../../decisions/register.md) |
| Default CSV interpretation (body lines 24): Default CSV interpretation | CP1252 with QUOTE_NONE; no embedded record newlines (D-003) | [Authority](../../../decisions/register.md) |

Current authority: [Gate A correction index](../../../evidence/gate-a/correction-index-v1.json), [recomputed spike evidence](../../../evidence/gate-a/spike-002-successor-v1.json), and [controlling decisions](../../../decisions/register.md).

<!-- calico-provenance-v1:end -->
---
spike: 001
name: archive-sample-validation
type: standard
validates: "Given representative Internet Archive captures across the XLSX and CSV eras, when a bounded sample is fetched and parsed, then release availability, schema continuity, capture-integrity risks, and the admission checks needed by an automated operations monitor can be established without a historical backfill."
verdict: PARTIAL
related: [002, 005]
tags: [internet-archive, provenance, data-quality, registry]
---

# Spike 001: Representative Internet Archive validation

## What This Validates

The Internet Archive is a viable source for historical California charity-registry snapshots across the 2019 XLSX era and later CSV era. It is not safe to trust a capture merely because the archive index or replay returns HTTP 200: the sampled November 2024 set contains two truncated bodies and is not a complete release.

The machine-readable audit is [archive-sample-manifest.json](./archive-sample-manifest.json). It records original and timestamp-pinned replay URLs, received bytes, SHA-256, parsed rows, as-of date, and validation status.

## Method

- Located candidate captures through the Internet Archive index.
- Retrieved only the bounded representative sample through timestamp-pinned `id_` replay URLs.
- Calculated SHA-256 over the exact bytes received.
- Parsed CSV using CP1252 and a newline-aware CSV reader.
- Parsed the XLSX workbook read-only and located the header by its `As-of Date` column.
- Required the exact 11-column schema and one uniform as-of date per successfully parsed file.
- For the July 2026 release, required all four list files and reconciled their total rows.

## Results

| Sample | Result | Rows | Important finding |
|---|---|---:|---|
| 2019-02-04 `may operate` XLSX | Valid | 131,932 | The logical 11-column schema is unchanged; the header is on worksheet row 5 after three explanatory rows and one blank row |
| 2022-02-16 `may operate` CSV | Valid | 128,239 | Clean CP1252 CSV with the same logical schema |
| 2026-07-15 complete four-file release | Valid | 557,065 | All four files share one as-of date and schema; hashes and entity diff are reproducible |
| 2024-11-20 four-file release captured 2024-11-26 | Invalid | Not applicable | Two bodies are complete; two terminate early despite HTTP 200 and contain truncated final rows |

The July 2026 `undetermined` file has 128,475 CSV records but 128,477 physical data lines. Two line breaks occur inside quoted records, confirming that line counting is not a valid row-count method.

The 2019 workbook also has 25 otherwise populated records with a blank `Registry Status`. Those are included in its row count and should be retained with a missing-status quality flag.

## Investigation Trail

1. The 2019 and 2022 samples parsed to the same 11 logical columns as 2026, validating a stable cross-era staging contract.
2. The complete July 2026 set reproduced 557,065 rows and one uniform `2026/07/15` as-of value.
3. The November 2024 archive set exposed why transfer-length validation is mandatory: two replay responses advertise complete content lengths but terminate after small prefixes with curl error 18.
4. The damaged set is described as a damaged Internet Archive capture, not a damaged state source release, because the evidence cannot identify whether the failure arose during crawl, archive storage, or replay.

## Admission contract for any candidate release

A release is usable only if all four expected list files:

1. finish transferring and match the response content length;
2. parse every record with exactly 11 fields;
3. share one `As-of Date` across all records and all four lists;
4. for a previously known archived object, match its committed byte count, row count, and SHA-256; for a genuinely fresh release, record those values as its new immutable manifest identity;
5. contain no duplicate registration number across lists; and
6. are identified by as-of date, archive timestamp, list, revision, and hash.

One failed structural check rejects the entire release. There is no severity-based precedence for cross-list duplicates; a duplicate is a partition violation to reject or quarantine. A structurally valid release with an unusual row count may be admitted with a visible anomaly flag rather than mislabeled as corrupt.

## Fresh-release automation contract

The later Public Registry Operations Monitor treats scheduled capture as a core deliverable:

1. Run on the registry's first- and third-Wednesday publication cadence, with bounded retries when the source still exposes the previous as-of date.
2. Fetch all four current files into temporary storage and admit none until the complete set passes the checks above.
3. Preserve the previous accepted release when a new set fails; distinguish `no_new_release` from a structurally `rejected` set and record machine-readable reasons.
4. Compare every newly accepted release with the previous accepted release and surface damaged-file, schema-drift, abnormal-row-count, coverage-change, and bulk-status-movement flags.
5. Publish only manifests, aggregate results, and flags. Never commit the temporary named source rows to the public repository.
6. Keep the existing manual capture procedure as a fallback because scheduled GitHub workflows are not a sufficient sole preservation mechanism.

Email notifications, investigation queues, and other workflow integrations are not required. A failed automation run and the monitor's release-health page are the scoped flag surfaces.

## Verdict

**PARTIAL.** The representative sample validates historical availability, schema continuity, pinned-fetch reproducibility, and the checks required for automated current-release capture. It does **not** validate the claimed total of roughly 37 complete releases because this spike deliberately did not enumerate or backfill the archive. A full candidate-release census remains the first ingestion task in the later build, not a scoping fact.
