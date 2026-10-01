<!-- calico-provenance-v1:start -->
**Historical record — contains superseded figures and guidance.**

Imported 2026-10-01. This records the predecessor Mitos investigation and Windows workspace; historical paths describe that workspace, not this repository.

The body below preserves every original byte outside the recorded local-path regions. The D-04 redaction record links the original, redacted body and bannered successor. Historical rules apply only to their original context; the following supersedes pairs and current authority govern the monitor.

| Superseded claim | Corrected successor | Evidence or decision |
|---|---|---|

Redaction lineage: [five-location D-04 record](../redactions/phase-09-provenance-paths-v1.json).

Current authority: [Gate A correction index](../evidence/gate-a/correction-index-v1.json), [recomputed spike evidence](../evidence/gate-a/spike-002-successor-v1.json), and [controlling decisions](../decisions/register.md).

<!-- calico-provenance-v1:end -->
# Mitos → Calico migration record

**Source:** `[LOCAL_PATH_REDACTED]`
**Destination:** `[LOCAL_PATH_REDACTED]`
**Migrated:** 2026-08-23 · **Scope-corrected:** 2026-08-23

**State at scope correction: 24 files.** All byte-for-byte identical to their Mitos sources. At that
time, nothing was in the Calico root but `_migration/`, this report, `GATE-A-EVIDENCE.md`, and
`AGENTS.md`.

> **Post-migration planning note — 2026-08-23.** Active working copies of the three build-contract
> documents and the schema baseline now live under root `.planning/`. The active contract adds the
> Gate A corrections and SQL-first implementation architecture adopted after this migration record.
> Use root `.planning/CODEX-DBT-FINAL-DECISIONS.md` for project planning. The copies under
> `_migration/mitos/` remain an immutable, byte-identical record of what was imported from Mitos.

---

## 1. Scope correction

The original migration copied **62 files**, following an allowlist that included the Mitos legal engine, the retired commercial thesis research, and the Phase 15 cure-reconstruction corpus. That allowlist was too broad for what Calico is.

**Calico is a public-data longitudinal monitor of the California charity registry** — Python landing and admission, DuckDB, dbt, Power BI. It is not a compliance product, it has no legal engine, and IRS segmentation and AB 488 enforcement are out of v1 scope by §8 and dispositions D2/D5 of the controlling memo.

**38 files were removed** on that basis and moved to `calico\_to_delete\`, which preserves their relative paths. Every one of them still exists unmodified in `Mitos-MVP`, so nothing is lost — delete the folder whenever you're satisfied. (The device bridge cannot delete files, which is why they were moved rather than removed.)

---

## 2. What was kept, and why

### Build contract — 3

| File | Why |
|---|---|
| `.planning/CODEX-DBT-FINAL-DECISIONS.md` | The Calico spec. Precedence authority over every other document here. |
| `.planning/DBT-BRIEF.md` | Build input — data inventory, column schemas, known defects. |
| `.planning/PORTFOLIO.md` | The durable "why", build order, target roles, and what counts as decoration. |

### Registry and parser evidence — 4

| File | Why |
|---|---|
| `.planning/research/ag-schema-baseline.json` | Per-list schema baseline. Direct input to the parser contract. |
| `.planning/research/CA-REGISTRY-FACTS.md` | Verified registry facts — RRF-1 deadlines, statewide scope. |
| `.planning/research/DURATION-METRICS-SPEC.md` | Persistence and duration metrics under irregular snapshots — the §6 metric contract. |
| `.planning/research/CURE-RATE-2026-08.md` | Observed cure rate and the `Last Renewal` cure signature — the §6 diagnostic. |

### Validation spikes — 8

`CONVENTIONS.md`, `MANIFEST.md`, and spikes 001 (Internet Archive sample validation, with its manifest), 002 (exact entity changes 2026-07-15 → 2026-08-05, with `entity-changes.json`), 003 (duration metrics and the spell contract), 005 (the monitor recommendation — the decision that created this project).

Spike 002's `entity-changes.json` is the original transition evidence. `GATE-A-EVIDENCE.md` supersedes its figures.

### Source and capture documentation — 3

| File | Why |
|---|---|
| `docs/addressable-list-data-sources.md` | Field inventory and storage schema for the four AG lists. |
| `docs/ag-registry-migration-2026-08.md` | Registry portal migration, search behaviour, and the official verification path the Gate D lookup page needs. |
| `docs/runbook-ag-capture.md` | Manual capture runbook — the §8 fallback. |

### Provenance manifests — 2

`data/registry-archive/manifest.json` and `ia-release-manifest.json`. Row counts and derived results in both are stale; `GATE-A-EVIDENCE.md` supersedes the July portion.

### Predecessor programs — 4

`scripts/research/archive_ag_release.py`, `diff_ag_releases.py`, `verify_ag_parse.py`, `verify_ag_schema.py`.

**Not production code.** Read them for ideas; do not import them. Their known parser, replacement-character, identifier-catalog, local-storage and named-output defects are deliberately unfixed. The working replacement is `_data_cache\capture_release.py`.

---

## 3. What was removed

| Group | Count | Reason |
|---|---|---|
| Legal engine — `ENGINE-CONTRACT.md`, `engine-contract.json`, three `mitos-adapters/schemas/*.json`, `Completed-Research.md`, the archived CA registration-law doc | 7 | Mitos's CT-1 determination engine. Closed, and out of v1 entirely. |
| Retired commercial thesis — `AB488-ENFORCEMENT-SCAN.md`, `MULTI-STATE-PLATFORM-GATES.md`, `FORM-TYPE-VS-DELINQUENCY.md`, `FEATURES.md`, `PITFALLS.md`, `ARCHITECTURE.md`, `STACK.md`, `SUMMARY.md` | 8 | AB 488 enforcement, IRS segmentation, and addressable-list measurement. Out of v1 by §8, D2, D5. `ARCHITECTURE.md` and `STACK.md` describe the Mitos repo and are superseded by §8. |
| Phase 15 cure reconstruction | 7 | Document-level case research supporting the retirement decision. Evidence for the findings write-up, not for the monitor. |
| Product strategy — `idea-consolidation.md`, `research-paper-direction.md` | 2 | Retired product thinking, and the spec for a separate portfolio artifact. |
| dbt scoping history — `DBT-FINAL-DECISIONS.md`, `DBT-SCOPING-*`, `CODEX-DBT-SCOPING-REVIEW.md`, `CLAUDE-DBT-SCOPING-*`, `CODEX-CLAUDE-DBT-VALIDATION-FEEDBACK.md` | 7 | Superseded by `CODEX-DBT-FINAL-DECISIONS.md`. Process history, not build input. |
| Non-registry scripts — `cohort_probe.py`, `form_type_vs_delinquency.py`, `scan_delinquent_solicitation.py`, `scan_platform_coverage.py`, `select_cure_cases.py`, and the two `tests/research/` files | 7 | AB 488, IRS, and Phase 15 tooling. `cohort_probe.py` is self-described as a throwaway probe. |

### Judgment calls worth knowing about

Three of these are defensible either way. All are one `mv` from coming back:

1. **The dbt scoping history (7 files).** Topically about this project, and it is the clearest surviving record of the design reasoning — which is the judgment story the portfolio is meant to sell. Cut because §12 of the controlling memo already summarises what those documents got wrong, and none of it is build input.
2. **`research-paper-direction.md`.** `PORTFOLIO.md` build-order item 2 names it as the spec for the findings write-up. That is a separate deliverable from the monitor, so it does not belong in this repo — but you will want it when you start that piece.
3. **Phase 15 and the AB 488 scan.** These are the evidence behind two of the five negative results. Essential to the findings write-up, irrelevant to building the monitor.

---

## 4. Verification

| Check | Result |
|---|---|
| Kept files matching Mitos source (SHA-256) | 24 / 24 |
| Files moved to `_to_delete/` | 38, paths preserved |
| Mitos source modified | No — read-only throughout |
| Calico root: `.planning`, `PROJECT.md`, `STATE.md`, `ROADMAP.md`, `REQUIREMENTS.md`, `.git`, `.env`, `pyproject.toml`, `dbt_project.yml` | None present |
| Secret scan — API keys, tokens, private keys, signed URLs, `.env` | 0 hits |
| Absolute local paths (`[LOCAL_PATH_REDACTED]`) | 0 hits |
| Binaries, PDFs, CSVs, databases, caches | 0 — the tree is 18 `.md`, 4 `.py`, 2 `.json` |

**Dangling references.** Nine kept files cite removed ones — for example `PORTFOLIO.md` points at `AB488-ENFORCEMENT-SCAN.md` for the enforcement evidence, and `DBT-BRIEF.md` cites `15-CURE-PATTERNS.md` for the 591-organization floor. These are historical citations; every target still exists in `Mitos-MVP`. Nothing needed to build Calico is broken. One is actually correct as-is: `ag-registry-migration-2026-08.md` supersedes the removed `15-DEEPLINK-PROBE.md`, so keeping the superseding document and dropping the superseded one is the right outcome.

**Unredacted content.** Redaction was waived — the Phase 15 files were the main carrier of organization names and registration numbers, and they are now out of the tree. `docs/ag-registry-migration-2026-08.md` remains and still contains real organization names and organization-specific document links. Redact at the point of any public use.

---

## 5. Not migrated, by design

Raw registry rows, `data/registry-archive/{ag,cure-captures,cure-cases,diffs,irs}`, `mitos.db`, `.env*`, `docs/Details`, PDFs, screenshots, spike 004 and its job-posting CSV, the FastAPI application, Mitos core/storage/transactional/admin packages, the frontend, Railway and Vercel configuration, and dependency files.

The eight raw August registry objects live in `[LOCAL_PATH_REDACTED]` alongside the reacquired July release. All three admitted releases were verified byte-for-byte against the Mitos copies; see `GATE-A-EVIDENCE.md`.
