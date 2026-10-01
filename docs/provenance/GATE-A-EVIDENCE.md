<!-- calico-provenance-v1:start -->
**Historical record — contains superseded figures and guidance.**

Imported 2026-10-01. This records the predecessor Mitos investigation and Windows workspace; historical paths describe that workspace, not this repository.

The body below preserves every original byte outside the recorded local-path regions. The D-04 redaction record links the original, redacted body and bannered successor. Historical rules apply only to their original context; the following supersedes pairs and current authority govern the monitor.

| Superseded claim | Corrected successor | Evidence or decision |
|---|---|---|
| published delinquent population transition definition (body lines 20, 24, 68, 70, 85, 88): 7,733 | 7,737; delinquency definition (D-006), independent of parser repair | [Authority](../decisions/register.md) |
| 2026-07-15 total rows (body lines 76): 557,065 | 557,067 | [Authority](../evidence/gate-a/spike-002-successor-v1.json) |
| Predecessor project name (body lines 4): Predecessor project name | California Charity Registry Monitor (D-001) | [Authority](../decisions/register.md) |
| Release-count readiness (body lines 162): Release-count readiness | Five simultaneous estimability conditions; no formal survival analysis in v1 (D-010) | [Authority](../decisions/register.md) |

Redaction lineage: [five-location D-04 record](../redactions/phase-09-provenance-paths-v1.json).

Current authority: [Gate A correction index](../evidence/gate-a/correction-index-v1.json), [recomputed spike evidence](../evidence/gate-a/spike-002-successor-v1.json), and [controlling decisions](../decisions/register.md).

<!-- calico-provenance-v1:end -->
# Gate A evidence record — July reacquisition and panel reconstruction

**Date:** 2026-08-23
**Project:** Calico — California Charity Observatory
**Controlling document:** `_migration/mitos/.planning/CODEX-DBT-FINAL-DECISIONS.md`
**Scope:** Gate A "evidence and landing" — reacquisition, hash gate, canonical reparse, corrected successor evidence. No dbt project, DuckDB database, repository scaffolding, or GSD artifact was created.

> **Planning pointer — 2026-08-23.** This evidence record remains unchanged as the factual Gate A
> benchmark. The active build contract is now root
> `.planning/CODEX-DBT-FINAL-DECISIONS.md`, which incorporates these corrections and the subsequently
> adopted SQL-first implementation architecture. The `_migration/mitos/` copy is migration history.

---

## 1. Headline results

| Question | Answer |
|---|---|
| Does the July 15 release reacquire and hash-match? | **Yes — all four objects, exact match.** |
| Is the `7,733` transition claim reproducible? | **No. The corrected figure is 7,737.** The difference is fully explained (§4). |
| Was the parser defect the cause of the difference? | **No.** 7,737 under both the canonical and the defective parser. |
| How many releases are now admitted? | **Three** — 2026-07-15, 2026-08-05, 2026-08-19. |

The controlling document's precondition for the lead finding — "if the July source files are reacquired, hash-matched, and the corrected pipeline reproduces the result" — is now satisfied on reacquisition and hash-matching, and **falsified on the specific number**. `7,733` is superseded by `7,737`. The claim survives; the figure changes.

---

## 2. Reacquisition

The prior record (`ia-release-manifest.json`, 2026-08-21) noted that `web.archive.org` was unreachable from both the cloud container and the desktop VM, so verification had been done in-browser.

**That is no longer true of the cloud container.** Reachability as of 2026-08-23:

| Environment | web.archive.org | oag.ca.gov |
|---|---|---|
| Anthropic cloud container | reachable (HTTP 200) | reachable (HTTP 200) |
| Desktop workspace VM | blocked — HTTP 403 from proxy after CONNECT | blocked — same |

All four July objects were fetched from the recorded Wayback `id_` URLs and hash-gated.

| List | Bytes | SHA-256 | Match |
|---|---|---|---|
| `charities-may-operate` | 49,187,517 | `d08bad94…f1c7` | **PASS** |
| `charities-not-operating` | 44,301,189 | `a98220de…0efa` | **PASS** |
| `charities-undetermined-status` | 37,515,401 | `b30f9d9d…1363` | **PASS** |
| `charities-may-not-operate` | 31,659,925 | `759e067d…7d8b` | **PASS** |

Hashes were re-verified after transfer and decompression onto local disk. The prior in-browser record reproduced exactly — `release_record_total` 557,067, `keyed_rows_across_release` 247,441, `delinquent_total` 5,476, and every `charities-may-not-operate` status count.

---

## 3. Admitted panel

Two further releases were located and admitted. **2026-08-05** was already held locally. **2026-08-19** was captured live from `oag.ca.gov`, which was still serving it — it would have been lost when the September release replaced it.

| Release | Source | Records | Keyed | Delinquent | Admission |
|---|---|---|---|---|---|
| 2026-07-15 | Internet Archive, hash-gated | 557,067 | 247,441 | 5,476 | **accepted** rev 1 |
| 2026-08-05 | held locally | 557,291 | 248,077 | 13,169 | **accepted** rev 1 |
| 2026-08-19 | live capture 2026-08-23 | 557,211 | 248,215 | 13,071 | **accepted** rev 1 |

All three pass every §4 admission check: four expected lists present, transfer complete, parser succeeds, headers and column order exact, 11 fields per record with non-blank date, one shared As-of Date across all four files, all registration numbers classified, no duplicates within or across files, provenance recorded. Zero replacement characters and clean CP1252 decode throughout; line reconciliation holds in all twelve files.

`ca_may_not_operate.csv` in `_data_cache` is a **byte-identical duplicate** of `charities-may-not-operate.csv` (August 5), not a second version. It is a filename artifact.

---

## 4. The 7,733 → 7,737 correction

Under the canonical contract, `Current - Reporting Incomplete → delinquent` between 2026-07-15 and 2026-08-05 is **7,737**, not 7,733.

**The parser is not the cause.** Both parsers give 7,737:

| Parser | July records | Rows lost to quote-fusion | CRI → delinquent |
|---|---|---|---|
| RFC-4180 default (the defect) | 557,065 | 2 | 7,737 |
| `QUOTE_NONE` canonical | 557,067 | 0 | **7,737** |

This confirms the controlling document's own hypothesis in §3 — "the observed delinquency transitions may survive because the fused rows were in another list." Both fused rows sit in `charities-undetermined-status`, which contains no delinquent records at all.

**The actual cause is the delinquency definition.** The original figure counted only the status `Delinquent`, excluding `Delinquent - Late Fees Due`:

| Definition | Count |
|---|---|
| `Delinquent` only (original) | **7,733** |
| `Delinquent` + `Delinquent - Late Fees Due` (canonical, §5) | **7,737** |

The four entities are `053483`, `116996`, `CT0249177`, `CT0268360`. §5 defines the delinquent state as exactly both statuses, so **7,737 is correct and 7,733 is superseded.**

This is a correction to the number, not a retraction of the finding. Record it as a correction notice with an explicit `supersedes` pointer rather than a silent rewrite.

---

## 5. Reconstructed flows

### Pair 2026-07-15 → 2026-08-05 (21 days)

| Measure | Value |
|---|---|
| Matched entities | 247,436 |
| Starting delinquent | 5,476 |
| Entries | 7,750 |
| Observed exits | 65 |
| Still delinquent | 5,411 |
| Not observed at t+1 | 0 |
| Delinquent on new keys | 8 |
| **Observed exit rate** | **65 / 5,476 = 1.19%** |

Reconciles exactly: 5,411 + 7,750 + 8 = 13,169 = August 5 delinquent total.

Entry sources: `Current - Reporting Incomplete` 7,737, `Suspended` 8, `Current - Awaiting Reporting` 4, `Exempt` 1.
Exit destinations: `Current` 53, `Current - In Process` 5, `Current - Reporting Incomplete` 3, `Suspended` 3, `Dissolution Waiver Issued` 1.

### Pair 2026-08-05 → 2026-08-19 (14 days)

| Measure | Value |
|---|---|
| Matched entities | 248,073 |
| Starting delinquent | 13,169 |
| Entries | **2** |
| Observed exits | 104 |
| Still delinquent | 13,065 |
| Not observed at t+1 | 0 |
| **Observed exit rate** | **104 / 13,169 = 0.79%** |

### Longer horizons

- **35-day cohort (Jul 15 → Aug 19):** 5,476 starting delinquent, 123 observed outside delinquency = **2.25%**.
- **August 5 entry cohort tracked 14 days:** 7,750 entrants, all 7,750 observed at Aug 19, 45 exited = **0.58%**.

### What the second pair establishes

Entries collapse from **7,750 to 2** across consecutive pairs. The August 5 movement was a **discrete bulk publication event, not a steady flow** — and the second pair supplies the counterfactual baseline the single-pair analysis never had. This materially strengthens the descriptive claim.

It does not license a causal one. The §2 prohibitions stand: this shows what the published files changed, not internal cause, processing time, or workflow. "Not observed" still never means cured.

---

## 6. Contract inputs harvested

- **Status vocabulary:** 33 distinct `Registry Status` values, stable across all three releases. Delinquency is confined entirely to `charities-may-not-operate`.
- **Registration number formats:** only **three** shapes observed — bare digits, `CT`+digits, `EX`+digits. `PORTFOLIO.md` refers to four formats of which the code recognized two. The fourth does not appear in any admitted release. Build the D6 admission test from the union across admitted releases and treat a fourth shape as an open question, not a known quantity.
- **RFC-4180 undercount is not uniform:** it appears in exactly one file per release (`charities-undetermined-status`, 2 records). Do not assume it affects every file.

---

## 7. Where things are

Raw registry rows are outside the repository, per §9.

```
[LOCAL_PATH_REDACTED]
  capture_release.py                        interim capture tool
  charities-*.csv                           August 5 release (loose, 4 files)
  ca_may_not_operate.csv                    byte-identical duplicate of the above
  irs_revocation.zip                        out of v1 scope (D2/D5)
  registry-archive\
    2026-07-15\   charities-*.csv (+ .gz)   reacquired, hash-gated
    2026-08-19\   charities-*.csv (+ .gz)   live capture
```

**Housekeeping:** the August 5 files sit loose at the root while July and August 19 are in dated folders. Move them to `registry-archive\2026-08-05\` so all three releases share one layout. The `.gz` copies are redundant once the `.csv` files are verified and can be deleted — the device bridge cannot delete files, so this is a manual step.

---

## 8. Interim capture tool

`_data_cache\capture_release.py` — standalone Python 3, stdlib only, runs on Windows. **Not Gate C production code**; it exists so no release is lost while the dbt project is built.

Behavior: cheap range-request pre-check reads the live As-of Date before pulling ~160 MB; full admission validation; writes to a dated folder with a manifest; refuses to overwrite an existing release; exit codes `0` accepted / `1` rejected / `2` no_new_release / `3` error.

Verified against live data and fixtures:

| Case | Result |
|---|---|
| Live Aug 19 capture | ACCEPTED — 557,211 / 248,215 / 13,071, matching independent analysis |
| Re-run against held release | NO NEW RELEASE in 0.9s, no full download |
| Wrong header order | REJECTED |
| Wrong arity | REJECTED |
| Mismatched As-of Date within file | REJECTED |
| Blank As-of Date | REJECTED |
| Duplicate registration number | REJECTED |
| Unknown registration shape | REJECTED |
| Empty file | REJECTED |
| **Unescaped quote inside Name** | **ACCEPTED** — the D7/D8 regression test |

**Next release: Wednesday 2026-09-02.** Run `python capture_release.py --dest <path>\registry-archive` from `_data_cache`. It must run on the Windows host — the desktop VM has no egress.

---

## 9. What this does not establish

1. **No dbt/DuckDB implementation exists.** These figures come from direct verification scripts, not the modelled pipeline. Gate B must reproduce them; treat any disagreement as a Gate B defect, not a correction to this record.
2. **No survival analysis.** Three releases spanning 35 days. §6's deferral of Turnbull, restricted mean duration, and standardized 30-day risk stands.
3. **Two pairs is not a rate.** The 1.19% and 0.79% exit rates are interval observations between exact dates, not annualized rates.
4. **Nothing here is published.** Gate E approval is a separate founder action.
5. **Private object storage still does not exist.** Founder action #2 is unstarted and is required before Gate C. Three releases now sit on one laptop.
6. **Spike 001 and 002 evidence is still stale.** This record supersedes the July portion of `ia-release-manifest.json` only.
