# CA AG Registry Search — Migration State, August 2026

**Status:** Active finding. Observed 2026-08-18. Blocks Phase 15.
**Supersedes:** the `## Verdict` section of `.planning/phases/15-cure-reconstruction/15-DEEPLINK-PROBE.md`.

---

> ## CORRECTION NOTICE (redacted successor)
>
> This document is a redacted, additive successor to a private original investigation record.
> It preserves the original investigation method and corrected findings. All Federal Employer
> Identification Numbers (FEIN/EIN) have been replaced with `[FEIN_REDACTED]`; organization
> names, exact `State Charity Reg#` values, and the official Registry Search Tool verification
> URL are retained unchanged as allowed published fields.
>
> **Read §8a and §8b before relying on any claim in §§1–7 or §9.** §8a (CONFIRMED 2026-08-19)
> supersedes §4 and §7's conclusion that no public surface returns current registrants. §8b
> (2026-08-19, from the AG directly) further corrects §8a's "live, not a stale snapshot" claim
> to "authoritative as of the last publish." Both are the governing conclusions for this
> document.
>
> The machine-readable redaction and supersession record for this artifact is at
> `docs/redactions/ag-registry-migration-2026-08.json`.

---

## 1. The finding in one line

> ⚠️ **Correction — see §8a and §8b.** §8a (CONFIRMED 2026-08-19) and §8b (2026-08-19) are the
> governing corrections for this document; read both before relying on any claim in this section.

**As of 2026-08-18, the currently-registered California charity population is not retrievable
from either public AG search surface.** The legacy tool has been drained of charity records;
the replacement portal has received a partial migration that contains no live registrations.
The AG's published bulk CSVs are, for the moment, the only public source for that population.

This is a data-availability finding, not a tooling defect. Both search surfaces function
correctly; they return nothing because the records are absent.

---

## 2. How it surfaced

> ⚠️ **Correction — see §8a and §8b.** §8a (CONFIRMED 2026-08-19) and §8b (2026-08-19) are the
> governing corrections for this document; read both before relying on any claim in this section.

Four unambiguously real, currently-registered organizations were drawn from the AG's own
`charities-may-operate` bulk file (as-of 2026-08-05, sha256-pinned in
`data/registry-archive/manifest.json`), all with Registry Status `Current`:

| Organization | Reg # as published | FEIN |
|---|---|---|
| Glide Foundation | `CT0276293` | [FEIN_REDACTED] |
| Sierra Club Foundation | `002363` | [FEIN_REDACTED] |
| Los Angeles Regional Food Bank | `020719` | [FEIN_REDACTED] |
| California State Parks Foundation | `011757` | [FEIN_REDACTED] |

Searching each by registration number (both renderings), by FEIN, and by organization name,
on both the legacy tool and the replacement portal, returned no correct record — in any
combination. One query returned a *wrong* organization, which turned out to be the key clue
(§5).

---

## 3. What was ruled out

> ⚠️ **Correction — see §8a and §8b.** §8a (CONFIRMED 2026-08-19) and §8b (2026-08-19) are the
> governing corrections for this document; read both before relying on any claim in this section.

Three mundane explanations were eliminated before concluding the records are absent.

| Hypothesis | Verdict | Evidence |
|---|---|---|
| A search mode / registrant type must be selected | **Ruled out** | `t_web_lookup__profession_name` (Program Type) defaults to `""` = All, which is a valid working selection. Values: `Charity`, `Fundraising Platform`, `Fundraising Professional`, `Raffle`. |
| The registration-number format needs normalizing | **Ruled out** | The tools do substring matching, not exact match. Zero-padding and `CT`-stripping were never the obstacle. |
| The identifier spaces diverge between bulk files and search tools | **Ruled out** | Numbering is identical across both systems. The records are missing, not renumbered. |
| A portal row filter hides migrated records | **Ruled out** | The portal's `converted != true` filter is inert: `count(entity where converted=true)` returns **0**. |

---

## 4. What is actually there

> ⚠️ **Correction — see §8a and §8b.** §8a (CONFIRMED 2026-08-19) and §8b (2026-08-19) are the
> governing corrections for this document; read both before relying on any claim in this section.

### Legacy tool — `https://rct.doj.ca.gov/Verification/Web/Search.aspx`

ASP.NET WebForms, POST to self, results held in `ASP.NET_SessionId`, 302 → `SearchResults.aspx`.
The form works. Its index has been drained.

- **~1,450 records remain, every one of them `CGT`** (corporate/individual trustee program),
  mostly individual trustee names.
- Numbering is `NN-NNNN` (`04-809`, `06-1437`) — never `CT#######`, never bare 6-digit.
- Statuses only `Registered` / `Expired`. FEIN column universally blank.
- **Zero charity or fundraiser records.**
- Detail links are `Details.aspx?result=<GUID>` where the GUID is minted per search-result-set
  **inside the session** — not a stable per-record URL.

The page states its own scope: it covers organizations "registered before October 2025."

### Replacement portal — `https://ca-rcf.evokeplatform.com/app/publicPortal/verification`

Named "Early Release Registry Search Tool." Vendor stack is React + Webpack Module Federation
over a LoopBack 4 backend, with a hostname check for `.cedar.mylicenseone.com` — the **same
vendor as the legacy MyLicense system**, i.e. this is a vendor-managed MyLicense → MyLicenseOne
migration.

| Measure | Value |
|---|---|
| `entity` records | 204,902 |
| `registrations` records | 49,517 |
| registrations with status `Registered` | **1,720** |
| registrations with status `Not Registered` | 47,787 |
| registrations numbered `CT04xxxxx` (native, post-Oct-2025) | 1,730 |
| **legacy-numbered (`CT0[0-3]…`) registrations with status `Registered`** | **0** |
| `entity` with `converted = true` | 0 |
| entity status `Dissolved` | 100,953 |

Every one of the 1,720 `Registered` rows is a natively-created post-October-2025 registrant.
**No legacy registration has survived the migration with an active status.**

---

## 5. Why one query returned a wrong organization

> ⚠️ **Correction — see §8a and §8b.** §8a (CONFIRMED 2026-08-19) and §8b (2026-08-19) are the
> governing corrections for this document; read both before relying on any claim in this section.

Searching `020719` (LA Regional Food Bank, per the bulk file) returned **Lake Matthews All
Sports Club**. Two independent facts explain it, and together they characterize the whole
migration:

1. **Matching is substring, not exact.** `020719` is a substring of `CT0207196`. Confirmed
   independently: `AMERICAN` matches `AMERICAN CANCER SOCIETY`, and the portal's own help text
   states it ("searching for *KIDS FOR* would find *KIDS FOR CATS*").
2. **Lake Matthews is present because it is dead.** It is in the portal as
   `entityStatus: "Dissolved"`, dissolution date 2014-07-30, registration `CT0207196` with
   status `Not Registered`.

**The migration loaded the dead records and not the live ones.** A search that happens to hit a
dissolved organization succeeds; a search for a current registrant finds nothing.

> ⚠️ **Operational hazard that outlives this migration.** Because matching is substring-based, a
> too-short registration number does not fail — it silently returns a *different* organization.
> Any lookup workflow must carry the full identifier **and** the expected organization name, and
> must record the name the tool returned, so a mismatch is caught at capture time rather than at
> scoring. A false match would otherwise produce a plausible-looking dossier for the wrong entity.

---

## 6. The replacement portal's API

> ⚠️ **Correction — see §8a and §8b.** §8a (CONFIRMED 2026-08-19) and §8b (2026-08-19) are the
> governing corrections for this document; read both before relying on any claim in this section.

Unauthenticated, GET-addressable, returns JSON, LoopBack filter syntax. Base
`https://ca-rcf.evokeplatform.com/api`, routed by prefix: `/objects|/instances|/reports|/forms`
→ `/api/data/...`; `/apps|/pages|/widgets` → `/api/webContent/...`.

| Endpoint | Returns |
|---|---|
| `GET /api/webContent/apps/publicPortal` | Full app definition (~141 KB) |
| `GET /api/data/objects?filter={"fields":["id","name"]}` | 126 objects |
| `GET /api/data/objects/entity/instances?filter={...}` | JSON array |
| `GET /api/data/objects/entity/instances/{id}` | Single record |
| `GET /api/data/objects/<obj>/instances/count?where={...}` | `{"count":N}` |

- Objects include `entity`, `registrations`, `irs990`, `formCt1`, `document`, `raffle`,
  `complaint` — **filing history, not just status.**
- `regexp` and exact match work; `like` does not.
- `legacyEntityData` returns 403. `entity`, `registrations`, `charitableOrganization` are open.
- **Per-record URLs are stable.** `entity.id` for migrated records is the legacy numeric
  `entityId`. Portal pages: `/app/publicPortal/entityDetails/{id}`,
  `/app/publicPortal/registrationDetails/{id}`.

This answers D-03 for the successor system: **stable per-organization URLs exist — on the new
portal, not the old one.** The Phase 15 probe's BRANCH-QUERY verdict is correct for
`rct.doj.ca.gov` and does not describe the system that replaces it.

---

## 7. Consequences

> ⚠️ **Correction — see §8a and §8b.** §8a (CONFIRMED 2026-08-19) and §8b (2026-08-19) are the
> governing corrections for this document; read both before relying on any claim in this section.

**For Phase 15.** The phase is parked. Its method — take an organization off the delinquency
list, look it up in the Registry Search Tool, read its per-year filing statuses — has no
working first hop today. Plan 15-04's manual lookup session would produce twelve empty
captures. The calendar window in ROADMAP.md and REQUIREMENTS.md ("run before 2026-08-20")
is **inverted by this finding**: the tool is unusable now and becomes usable only after the
cutover. See §8.

**For the archive.** The bulk CSVs are now the sole public source for the currently-registered
population — not the convenient source, the only one. This raises the stakes on the capture
track (CAPTURE-03's missing remote backup; the 08-19, 09-02, 09-16 and October releases). A
missed release was already unrecoverable; it is now unrecoverable *and* unsubstitutable.

**For positioning.** An organization that tries to verify its own delinquency on either state
tool today finds nothing. That breaks any "go look it up yourself" verification step in the
pitch. It also means a bulk-derived view is currently **more complete than the state's own
live surfaces** — a real, if temporary, informational advantage.

---

## 8. What to re-check after the cutover

The published outage window is **2026-08-20 → 2026-08-24**. The evidence that this is the
cutover which loads the live population: the app is named "Early Release," the legacy site
scopes itself to pre-October-2025 registrants, and the portal's help text still directs users
back to the legacy tool when a record is missing — a circular dead end no agency would ship
as a steady state.

**Single decisive re-probe:**

```
GET https://ca-rcf.evokeplatform.com/api/data/objects/registrations/instances/count?where={"registrationStatus":"Registered"}
```

- Still ~1,720 → migration has not landed. Phase 15 stays parked.
- Six figures → migration landed. Re-probe per-record URL stability, then unpark Phase 15.

### Re-probe result — 2026-08-19 (one day *before* the cutover)

Run early, so this measures the pre-cutover state, not the post-cutover one §8 asks about.

| Measure | 2026-08-18 | 2026-08-19 |
|---|---|---|
| `registrations` with status `Registered` | 1,720 | **1,724** |
| `registrations` total | 49,517 | 49,521 |
| `entity` total | 204,902 | 204,914 |

**Migration had not landed as of 2026-08-19.** +4 `Registered` in a day is ordinary new-registrant
intake, not a bulk load. All 26 cure-case registration numbers resolved to **nothing** on the
portal — 0 hits by `registrationNumber`, 0 by `fein`. A positive control (`fein=[FEIN_REDACTED]` →
CMEA SOUTHERN BORDER SECTION) confirmed the queries were well-formed, so the records are absent
rather than the query wrong.

⚠️ **Field-name correction to §6.** The `entity` object keys are `entityName`, `legalName`,
`fein`, `entityId` — **not** `name`. A `where` clause on `name` returns `[]` rather than erroring,
so a wrong field name looks exactly like an absent record. Always run a positive control.

---

## 8a. CONTRADICTION — the legacy tool returned charity records on 2026-08-19

**Status: CONFIRMED 2026-08-19. This section supersedes §4 and §7 on the legacy tool.** Those
sections are left standing as the record of what was observed on 08-18, but their conclusion —
that no public surface returns current registrants — is false as of 2026-08-19.

Founder ran five registration numbers by hand through both public tools on 2026-08-19:

| # | Reg # | Organization | Existing tool | Early Release |
|---|---|---|---|---|
| 1 | `CT0400000` | J GROUP FOUNDATION (native post-Oct-2025) | ✅ found | ✅ found |
| 2 | `CT0296459` | Family Interacting Together Love | ✅ found | ❌ absent |
| 3 | `CT0181854` | GREY GABLES FOUNDATION CHARITABLE TRUST | ✅ found | ❌ absent |
| 4 | `001386` | EDWARDS FDN | ✅ found | ❌ absent |
| 5 | `005423` | WORLD MEDICS INC. | ✅ found | ❌ absent |

**§4's core claim — "~1,450 records remain, every one of them `CGT`… zero charity or fundraiser
records" — is falsified by direct observation.** Four legacy-numbered charity records returned,
including two bare-6 (`001386`, `005423`) and two `CT0…` — exactly the shapes §4 said were never
present.

**What this changes:**

1. **The legacy tool is a working lookup surface.** §7's claim that "the bulk CSVs are now the
   sole public source for the currently-registered population" is wrong as of 2026-08-19.
2. **Phase 15's first hop exists.** The phase was parked because its method had no working
   lookup. It does. The unpark test in §8 asks the wrong question — it tests whether the *new*
   portal is ready, when the phase only needs *a* working surface.
3. **Migration direction confirmed, and it is incomplete.** #1 (native to the new system) is in
   both; #2–#5 are in legacy only. Records flow legacy → new and the bulk has not moved.
4. **The existing tool serves filing history, not just status** — its own scope text promises
   "copies of annual registration renewal forms (Form RRF-1), IRS Forms 990, raffle reports and
   fundraising reports." That is precisely Phase 15's input.

**Not yet explained:** whether the legacy index was repopulated between 08-18 and 08-19, or the
08-18 probe was flawed. The 08-18 session reported the form working and returning only `CGT`
records, which is not a failure mode that a wrong query produces. Both possibilities remain open.

**Verification status — CONFIRMED 2026-08-19.** Two cases were re-run manually with the returned
organization names recorded per §5's hazard rule. **Names matched.** Independently, all five test
numbers were checked for substring ambiguity against the 248,215 registration numbers in the
2026-08-19 archive: **each is unique**, so none of the hits can be a `020719 → CT0207196`-style
false match on the published universe. (The tool's index may hold records the published lists omit,
so this is strong evidence rather than proof.)

**The legacy tool is live, not a stale snapshot.** `005423` WORLD MEDICS INC. cured between the
08-05 and 08-19 releases (`Delinquent - Late Fees Due → Current`) and the legacy tool returned it
as **`Current`** — matching the 08-19 bulk file. This is the load-bearing observation: it shows the
legacy tool reflects status changes as they happen, consistent with its own scope text ("Information
is retrieved from the database in real-time but data and statuses may change intraday as filings are
processed"). A tool that merely still held old rows would have shown the stale status.

**Consequence: the existing tool is the live authoritative surface for the registered population,
and Phase 15's first hop is trustworthy.** It also gives 11 CCR §316(b),(d) an operational surface —
the published list blocks, the Search Tool restores, and the Search Tool is working.

Only the *cause* remains open: whether the legacy index was repopulated between 08-18 and 08-19, or
the 08-18 probe was flawed. That question no longer gates any decision.

**Method note.** The programmatic ASP.NET postback replay attempted on 2026-08-19 failed with the
tool's generic error page and was abandoned rather than pursued — automated retrieval from the
legacy tool remains out of scope pending the CURE-03 terms-of-use review. All legacy-tool evidence
here is manual use of the public search form.

---

## 9. Confidence and limits

> ⚠️ **Correction — see §8a and §8b.** §8a (CONFIRMED 2026-08-19) and §8b (2026-08-19) are the
> governing corrections for this document; read both before relying on any claim in this section.

> ⚠️ **Superseded in part on 2026-08-19 — see §8a.** The unqualified negative finding below held
> for the portal on re-probe but was **falsified for the legacy tool** by manual observation. Read
> §8a before relying on anything in this section.

The negative finding is stated without qualification: **today, neither tool returns current
registrants**, and the form, the identifier format, the search mode, and the portal row filter
were each ruled out before concluding it.

The *explanation* — mid-migration, load in progress — is well-supported but remains a
**single-point-in-time observation taken two days before a scheduled outage.** No durable
conclusion about the AG's data architecture should be built on it. Re-verify after 2026-08-24.

**Method note.** Findings come from bounded read-only inspection: 15 requests to
`rct.doj.ca.gov` and roughly 25 read-only metadata and count queries to the portal. No cohort
iteration, no bulk retrieval, no fetcher or scraper written or committed.

---

## 8b. CORRECTION — the legacy tool is authoritative-as-of-last-publish, not real-time

**2026-08-19, from the AG directly (Delinquency Program, in response to the CAPTURE-05 enquiry).**
Cures are reflected in the Registry Search Tool **only at the next semi-monthly publish**, not when
the filing is processed.

This **retires §8a's claim that "the legacy tool is live, not a stale snapshot."** That claim rested
on one observation: an organization cleared on 2026-08-12 showed as `Current` when checked on
2026-08-19. But **2026-08-19 was itself a publish date**, so the observation is equally consistent
with a semi-monthly refresh and never distinguished the two. The reasoning was wrong even though
the underlying data was right.

The tool's own scope text — *"Information is retrieved from the database in real-time but data and
statuses may change intraday as filings are processed"* — conflicts with what the AG stated. Treat
the AG's statement as controlling and the scope text as describing the database read, not the
freshness of the status field.

**What is unaffected:** everything §8a was actually used for. The tool returns real records for
real legacy-numbered charities, names matched, and the 12-case capture succeeded. Phase 15's
unparking stands. "Authoritative as of the last publish" is entirely sufficient for reconstruction.

**What changes:** any claim about *timing*. A cured organization is invisible as good standing for
up to ~15 days after the state has cleared it, because platforms check the tool. See STATE.md on
11 CCR §316(b),(d) — the prior conclusion that the Search Tool restores faster than the publication
calendar is inverted.
