# Evidence-linked walkthrough

Owner recording script: **180 seconds total**, four segments of 45 seconds. Read only the
narration aloud. Shot instructions and checklists are preparation, not spoken content. Rehearse
with transitions included; shorten pauses or retake if the actual recording exceeds 180 seconds.
The duration contract validates the planned timeline; it cannot measure an unrecorded video.

Use the committed product revision supplied with the handoff. Prepare the existing Windows
Power BI Desktop report before recording; this script does not ask for a new capture,
publication, model edit or refresh. Browser shots use committed public files, with the title bar,
address bar and other browser chrome outside the crop. Desktop shots use the aggregate report
canvas only. A shot may be held while its narration continues.

## finding — 45 seconds

**Narration:**

This is the California Charity Registry Monitor. Between July 15 and August 5, 7,737 matched organizations moved from Current - Reporting Incomplete into the published delinquent population. The older 7,733 figure is superseded by that corrected finding. These are published observations, not an explanation of internal cause. The final panel contains three accepted releases. The aggregate persistence view follows a starting cohort across actual release endpoints: still observed in the published delinquent population, observed outside it, and not observed stay separate. Those quantities come from tested SQL, without hand-calculation in the video.

**Shots, in order:**

```json
[
  {
    "application": "Browser, rendered committed Markdown",
    "page": "README, Metric definitions: generated claim and its Evidence lines",
    "source": "README.md",
    "evidence": ["docs/evidence/public-readme-inputs-v1.json", "docs/evidence/gate-a/correction-index-v1.json"],
    "bounds": "Only the generated claim paragraph and its contract/version/hash evidence lines; keep both accepted endpoint dates readable",
    "hide": "Browser chrome, local paths, account identifiers, notifications, other tabs/windows, raw rows and all private originals"
  },
  {
    "application": "Power BI Desktop, existing committed report",
    "page": "Cohort persistence, persistence_support aggregate table",
    "source": "powerbi/Calico.Report/definition/pages/cohort_persistence/visuals/persistence_support/visual.json",
    "evidence": ["docs/evidence/public-readme-inputs-v1.json"],
    "bounds": "Report canvas table rectangle x=24, y=312, width=1552, height=304; retain endpoint dates, actual gap, cohort denominator and observed/not-observed labels; scroll only within this aggregate table",
    "hide": "Desktop title bar, file location, model/data panes, tenant/account chrome, notifications, organization lookup page, exact-key selection and personal windows"
  }
]
```

Evidence: [generated claim and accepted identities](../README.md#metric-definitions),
[pinned public inputs](evidence/public-readme-inputs-v1.json),
[correction lineage](evidence/gate-a/correction-index-v1.json), and the
[committed aggregate persistence visual](../powerbi/Calico.Report/definition/pages/cohort_persistence/visuals/persistence_support/visual.json).
The report displays governed output; do not calculate or narrate a new persistence number.
The correction index is a provenance anchor, not a full-screen recording source.

## sql — 45 seconds

**Narration:**

Here is the exact-key SQL transformation behind adjacent-release transitions. The first excerpt joins starting and ending observations on the complete registration key and both full release identities. Revisions cannot become extra time points, and substring matches cannot merge organizations. The anti-join keeps a starting observation whose endpoint is absent. That absence is recorded separately, never filled with an invented status. DuckDB and dbt SQL own these calculations; Python admits the source bytes and Power BI presents the governed result.

**Shots, in order:**

```json
[
  {
    "application": "Browser, rendered committed Markdown",
    "page": "README, Annotated SQL excerpts: int_entity_transitions only",
    "source": "README.md",
    "evidence": ["docs/evidence/sql-excerpts-v1.json"],
    "bounds": "Only the int_entity_transitions annotation, model link, source/selected hashes and complete 18-line selected code block; highlight full-key inner join then anti join",
    "hide": "Browser chrome, account identifiers, other tabs/windows, terminals, compiled artifacts, data previews and raw registry rows"
  }
]
```

Evidence: [exact extracted transition selection](evidence/sql-excerpts-v1.json) and
[rendered excerpt](../README.md#annotated-sql-excerpts). The excerpt selects model lines
114–122 and 153–161 in source order; it is an excerpt, not a runnable standalone query.
Do not substitute a reconstructed query or a data-result screen.

## parser — 45 seconds

**Narration:**

The investigation also found a source-reading defect. Under default quote handling, unmatched quote characters fused two records per affected release. The older total of 557,065 is superseded by 557,067 in the corrected July 15 release. The contract is CP1252 plus QUOTE_NONE: quotes are ordinary characters, and the files contain no embedded record newlines. The predecessor recommendation for a newline-aware reader is inverted; it reproduces the fusion rather than repairing it. The correction preserves the older claim through a linked successor instead of silently rewriting history.

Evidence: [correction index and predecessor/successor hash chain](evidence/gate-a/correction-index-v1.json),
[corrected July 15 total and retracted explanation](evidence/gate-a/spike-001-successor-v1.json),
and [current source-reader contract](../README.md#source-path).

**Shots, in order:**

```json
[
  {
    "application": "Browser, rendered committed Markdown",
    "page": "Walkthrough, parser narration and evidence links",
    "source": "docs/walkthrough.md",
    "evidence": ["docs/evidence/gate-a/correction-index-v1.json", "docs/evidence/gate-a/spike-001-successor-v1.json"],
    "bounds": "Only the parser narration paragraph showing 557,065 superseded by 557,067 and CP1252 plus QUOTE_NONE; include the two evidence-link labels below it",
    "hide": "Browser chrome, other tabs/windows, all historical bodies, correction-index private_path fields, local paths and raw source files"
  },
  {
    "application": "Browser, committed successor JSON",
    "page": "Spike 001 successor, release_total and embedded_newline_explanation_retracted",
    "source": "docs/evidence/gate-a/spike-001-successor-v1.json",
    "evidence": ["docs/evidence/gate-a/correction-index-v1.json"],
    "bounds": "Only as_of_date, release_revision, release_total=557067, embedded_newline_explanation_retracted=true and status=corrected fields",
    "hide": "Browser chrome, other tabs/windows, raw inputs, historical originals, local paths and any nonselected JSON fields"
  }
]
```

Keep the correction index off-screen: its intentional historical references are provenance,
not a shot of the excluded workspace. No source row is needed to explain the defect.

## non-claim — 45 seconds

**Narration:**

This is an outside-in monitor of the published registry population: disappearance is not cure. The observations do not measure Attorney General workload, staffing, processing time or enforcement, and they do not establish intent or cause. They offer no organization score, ranking or partner recommendation. The commercial investigation ended in retirement; no organization was interviewed about willingness to pay. Report updates use a documented manual Power BI Refresh now step. The report is not fully automatic. A failed capture does not erase accepted release identity. The report URL and final owner approval remain Phase 10 actions.

**Shots, in order:**

```json
[
  {
    "application": "Browser, rendered committed Markdown",
    "page": "README, Deliberate non-claims followed by the manual refresh disclosure",
    "source": "README.md",
    "evidence": ["docs/powerbi-refresh-runbook.md", "docs/evidence/public-readme-inputs-v1.json"],
    "bounds": "Only the Deliberate non-claims paragraph, then the full two-sentence manual Refresh now disclosure in Accepted release and latest capture attempt; keep accepted identity distinct from capture outcome",
    "hide": "Browser chrome, other tabs/windows, private files, tenant identifiers, credentials, organization-specific history and personal windows"
  }
]
```

Evidence: [deliberate non-claims](../README.md#deliberate-non-claims),
[manual refresh runbook](powerbi-refresh-runbook.md), and
[accepted identity and capture outcome](evidence/public-readme-inputs-v1.json).
Native Service manual refresh has been observed; a successful schedule is not proven.
Do not create a Publish to web URL or imply final acceptance while recording this script.

## Before recording

- Use the exact committed product revision in the handoff and the existing owner workstation.
  Prepare the governed aggregate report in Windows Desktop, without modifying or deploying it.
  Verify visible endpoints and denominators agree with the accepted panel; if values differ,
  stop and resolve the evidence mismatch before recording.
- For the privacy sweep, close private planning and originals, terminals showing local paths or
  secrets, unrelated personal windows, account settings and notification banners. Disable
  notifications. Select a single application region; never capture the whole desktop.
- Keep every organization dossier and the organization lookup page closed. Clear any saved
  organization selection before switching to the aggregate persistence page. No raw rows,
  source PDFs, excluded fields, credentials or unpublished documents may enter a frame.
- Rehearse all four segments with shot changes inside the planned duration. Use the rendered
  README and exact selected excerpt, not a terminal or editor with a file-path sidebar.

## After recording

- Measure actual duration: **180 seconds or less**, including transitions and any title/end card.
  Retake if it runs longer. Confirm finding, SQL, parser defect and deliberate non-claim are all
  audible and their evidence regions readable.
- Complete a privacy review of **every frame**, including opening, closing, transitions and
  accidental app switches. Check crops, browser chrome, account/tenant details, local paths,
  notifications and reflections of personal windows. Retake any unsafe shot.
- Keep the recording binary outside Git. Do not add a video, screenshot, transcript of raw
  records or private evidence. Only a verified external URL is committed later.

## Host and verify anonymously

- The owner hosts the video outside Git: an unlisted YouTube video or a GitHub Release asset
  for this product repository. A release asset is uploaded media, never a tracked Git file.
- Open the actual hosted link in a logged-out browser and a fresh incognito window. Confirm
  anonymous viewing works without credentials, account selection, permission requests or a
  private sharing token. Verify the playable/uploaded copy, not a local preview.
- Provide plan 09-09 with the external URL, measured duration, four-topic confirmation, explicit
  privacy review and anonymous viewing results, host/view date, and product commit used.
  The agent validates that handoff before adding the README link. A script alone is not proof
  of recording; report publication and final owner approval remain Phase 10.
