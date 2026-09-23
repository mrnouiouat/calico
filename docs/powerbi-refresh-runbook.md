# Power BI refresh and owner acceptance runbook

This runbook deploys only the inspected, committed Power BI Project (PBIP). The proven Phase 8
branch is manual: the owner published the committed tracer to My workspace with the Web source set
to **Anonymous / Public** and observed a completed native **Refresh now**. The safe record is
`08-TRACER-EVIDENCE.md`, summarized in `08-04-SUMMARY.md`. It showed accepted release 2026-08-19,
revision 1, a rejected latest attempt, and a retired source state. It does not prove a scheduled
refresh. Do not record a pass until it is observed in Desktop or the native Service refresh history.

Every report page must retain this exact disclosure:

> Report updates use a documented manual Power BI Refresh now step. The report is not fully automatic.

## Inspected deployment boundary

The fixed source is `https://raw.githubusercontent.com/mrnouiouat/calico/published-data/` and the
model imports the thirteen governed v3 tables declared by the committed TMDL. The sole relationship
is the single-direction, many-to-one registration-key edge serialized from
`fct_public_status_observations` to `dim_public_organizations` (logically, the organization
dimension filters its observation rows). Registration keys are text. Do not edit the semantic model in the
Service. Make PBIP-only edits locally, save through Desktop when required, inspect the textual diff,
regenerate the inventory, test, scan, and commit before deployment.

From the product repository, run:

```powershell
.\.venv\Scripts\python.exe -m calico_publish generate-inventory --model powerbi/Calico.SemanticModel --output powerbi/semantic-model-inventory-v1.json
.\.venv\Scripts\python.exe -m calico_publish check-inventory --inventory powerbi/semantic-model-inventory-v1.json
git diff --exit-code -- powerbi/semantic-model-inventory-v1.json
```

Open `powerbi/Calico.pbip` in installed Desktop. Record the actual preview/schema versions and any
native serialization changes. If Desktop changes a descriptor, repeat generation, contracts and
privacy scans and commit the exact native textual diff before publishing. Deploy that committed
PBIP to the owner's Service workspace. Do not create a Publish to web embed; Phase 10 and founder
approval govern any public embed. Do not substitute a gateway, local input or Service-side edit.

The inventory is the complete exposed semantic-model surface, including the approved named tables
and fields. The lookup guard is presentation behavior, not access control: the complete approved
named export remains in the inspected semantic model.

## Native refresh configuration and evidence

In Service settings, map the single Web connection to **Anonymous** authentication with privacy
level **Public**. Do not store a token, application secret or gateway credential. Enable refresh
failure email to the owner. Configure the intended daily schedule for **15:30
America/Los_Angeles**, after capture/publication. A scheduled success, failure, cold start, and
manual result must be recorded from the native Service refresh history with date, start/end time,
result and deployed product commit. No REST polling application or secret is part of this project.

Run the following evidence cases without predicting their outcome:

1. Cold start: publish the committed project, refresh, and record the native result and the visible
   accepted identity and attempt fields.
2. Scheduled/automatic: wait for the configured 15:30 run and record the native result. Until that
   succeeds, keep the manual disclosure; never call the report fully automatic.
3. Controlled status change: after a real capture-only status update, refresh and record the
   published-data branch commit, capture attempt timestamp/outcome, and unchanged accepted release
   identity where applicable.
4. Manual fallback: use native **Refresh now**, which shares the same Web connection. Record its
   refresh-history entry and visible values. Failure of both scheduled and manual refresh blocks
   Gate D; it does not license a local-file or gateway substitution.
5. Failure handling: retain the last dated successful values, capture the native failed-history
   entry and confirm the owner failure email. Never invent a current release after an absent or
   malformed source.

The publication and status files advance through separate branch commits. A refresh may observe
them at different instants; mixed branch reads are not an atomic Service snapshot. Record both
observed commit/value identities and surface a mismatch instead of claiming atomicity.

The frozen panel contains exactly three accepted releases: 2026-07-15, 2026-08-05 and 2026-08-19.
Record visible attempt timestamps rather than inferring recency. A local owner-only v3 publication
requires checking the B2 cap first and costs approximately 509 MiB of download. Perform at most the
one authorized complete-v3 publication; hosted republish remains refused. Never put private store
paths or publication credentials in evidence.

## Owner report acceptance

Start from the committed report's empty saved state. Test at fit-to-width and actual Service scale
on all four pages. Confirm the shared banner shows the same accepted date/revision and capture
state, the manual disclosure is present, required tables scroll, keys/names/caveats are readable,
keyboard focus is visible, tab order is sensible, native controls are at least 48 px, and high
contrast does not hide meaning. Native loading remains visible. For empty/no-data/error/partial
states, compare the exact Copywriting Contract in `08-UI-SPEC.md`; `Not observed` stays separate,
missing intervals are not recomputed, and zero denominators display `not available`.

Exercise these direct-picker cases outside visual row context and record the observed detail state:

1. **Zero exact keys:** prompt remains and history is suppressed.
2. **Unique name only:** even one name candidate remains locked.
3. **Duplicate names:** distinct full registration keys remain separate and histories never merge.
4. **One directly selected exact key:** only this case unlocks latest observation and dated history.
5. **Multiple exact keys:** prompt remains and history is suppressed.
6. **Candidate-row context:** clicking a candidate without using the direct exact-key picker remains locked.
7. **Propagated relationship context:** indirect filters remain locked.
8. **Changed name search:** clear the exact key first; a prior key outside the new candidates must lock again.
9. **Initial saved state:** reopen the committed report and confirm no implicit key is selected.

For the unlocked case, confirm the complete full key (including a leading zero and suffix), latest
observed date, latest-release missing warning, and history sorted descending by `as_of_date` then
`release_revision`. Use the Desktop fixture model for synthetic duplicate names, a long organization
name, a long key, and a key missing from the latest release. Screenshots of adversarial examples use
synthetic identities only. Never include excluded fields, account or tenant identifiers,
credentials, private store paths or personal/contact data.

On the aggregate pages, verify exported numerators, denominators, interval dates/gaps, uncertainty
bounds, qualifications, three release-quality diagnostics, prevalent-snapshot wording and censoring
labels without recomputation. Equal accepted dates must display the promoted upstream revision.
Status-only changes must not alter accepted release identity.

## Interpretation, links and correction boundary

The report preserves historical observations indefinitely; the source historically described an
approximately 15 days publication lag, while the replacement official portal represents the
current official record. The report does not establish current legal or compliance status. Verify
against the Registry Search Tool linked from `https://oag.ca.gov/charities/reports` (and the current
portal landing page) and never construct an organization-specific official URL.

Keep the manifest, official verification and **correction channel** links visible. The named-output
exclusion set is live, but its derivation rule is not recorded; do not call it systematic. Route a
suspected source/display issue through the dedicated correction template without posting excluded
identifiers, street addresses, contact details or personal information.

Record every explicit and backstop row from `08-UI-SPEC.md` by element/category with a dated result
and safe screenshot reference. Static contracts establish readiness only; owner interaction and
native Service history supply the acceptance evidence.
