# A written walkthrough of the California Charity Registry Monitor

The monitor follows what California's public charity registry reports across accepted releases.
This walkthrough connects one finding to its SQL, explains a source-reading defect, and sets out
what the observations can support. Each section links to committed evidence that a reader can
inspect directly.

## One finding: an observed status transition

Between **2026-07-15 and 2026-08-05**, **7,737** matched organizations moved from
**Current - Reporting Incomplete** into the **published delinquent population**. The earlier
**7,733** figure is retained as superseded in the correction record. The corrected count describes
published observations; it does not establish why an organization's reported status changed.

The final accepted panel contains three releases: **2026-07-15**, **2026-08-05**, and
**2026-08-19**. Same-date revisions remain revisions of a release, rather than additional time
points. Each accepted identity carries its release revision, source fingerprint, and parser
contract version in the [pinned public inputs](evidence/public-readme-inputs-v1.json).

Cohort persistence follows the organizations in a starting cohort across actual release endpoints.
It keeps three outcomes separate: still observed in the published delinquent population,
observed outside that population, and not observed. The endpoint dates, actual gap, starting
cohort denominator, and observation flags travel with the governed output. The
[aggregate persistence visual](../powerbi/Calico.Report/definition/pages/cohort_persistence/visuals/persistence_support/visual.json)
presents that output; the interpretation does not require a new calculation in this document.

Evidence: [claim and metric definitions](../README.md#metric-definitions),
[transition correction lineage](evidence/gate-a/correction-index-v1.json), and
[corrected spike 002 successor](evidence/gate-a/spike-002-successor-v1.json).

## One SQL transformation: match full keys and preserve missing endpoints

The adjacent-release transition model first matches starting and ending observations on the
complete State Charity registration key and both full release identities. Exact-key matching
avoids merging organizations through substring matches. Binding both endpoint identities keeps
revisions from becoming extra time points.

Its anti-join retains a starting observation whose endpoint is absent. That absence is recorded
separately and never filled with an invented status. This distinction is what lets downstream
cohort output separate observed transitions from loss of observation.

The following is the exact 18-line selection from
[int_entity_transitions.sql](../dbt/models/intermediate/int_entity_transitions.sql), source lines
114–122 followed by 153–161. The two ranges belong to separate parts of the full model; this
selection is an excerpt rather than a runnable standalone query.

```sql
    from start_observations as s
    inner join end_observations as e
        on s.from_as_of_date = e.from_as_of_date
       and s.from_release_revision = e.from_release_revision
       and s.from_revision_fingerprint = e.from_revision_fingerprint
       and s.to_as_of_date = e.to_as_of_date
       and s.to_release_revision = e.to_release_revision
       and s.to_revision_fingerprint = e.to_revision_fingerprint
       and s.state_charity_registration_number = e.state_charity_registration_number
    from start_observations as s
    anti join end_observations as e
        on s.from_as_of_date = e.from_as_of_date
       and s.from_release_revision = e.from_release_revision
       and s.from_revision_fingerprint = e.from_revision_fingerprint
       and s.to_as_of_date = e.to_as_of_date
       and s.to_release_revision = e.to_release_revision
       and s.to_revision_fingerprint = e.to_revision_fingerprint
       and s.state_charity_registration_number = e.state_charity_registration_number
```

The [SQL excerpt record](evidence/sql-excerpts-v1.json) binds the selection to its source:

- Source SHA-256: `fc7531e2aa71fe6480a8d5d6828931b2a0bf4eca57df788338894a73426ba9c9`.
- Selected-byte SHA-256: `0e907c3e2e742d7eb1d782d1142013e75fb703c947a705314d5289e181c77804`.

DuckDB and dbt SQL own the analytical calculations. Python admits and records the source bytes;
Power BI presents the governed result. The [fixture-derived lineage](evidence/dbt-lineage-v1.json)
and [README excerpts](../README.md#annotated-sql-excerpts) expose those responsibilities and
representative transformations.

## One source defect: quotes fused records under default parsing

The investigation found that **default quote handling** interpreted unmatched quote characters
as CSV structure and fused **two records** per affected release. The earlier July 15 total of
**557,065** is preserved as superseded by the corrected **557,067** total.

The source-reader contract is **CP1252 with QUOTE_NONE**. Quotes are ordinary characters, and the
files contain **no embedded record newlines**. The predecessor recommendation for a
**newline-aware** reader is **inverted**: following it reproduces the fusion. The corrected reader
preserves actual record boundaries and lets the admission checks examine each record.

| Historical claim | Corrected successor | Evidence |
|---|---|---|
| July 15 release total: 557,065 | July 15 release total: 557,067 | Spike 001 successor |
| Adjacent-release finding: 7,733 | Adjacent-release finding: 7,737 | Spike 002 successor |

The [spike 001 successor](evidence/gate-a/spike-001-successor-v1.json) records the corrected total
and retracts the embedded-newline explanation. The [correction index](evidence/gate-a/correction-index-v1.json)
links predecessor and successor hashes. The [source path](../README.md#source-path) explains the
current contract. Corrections remain additive so a reader can follow how the evidence changed.

## One deliberate non-claim: disappearance is not cure

This is an **outside-in** monitor of the published registry population: **disappearance is not
cure**. Loss of observation is not proof that an organization cured a reported status. The
observations do not measure Attorney General workload, staffing, processing time, or enforcement,
and do not establish intent or cause. The monitor offers no organization score, ranking or
partner recommendation.

The commercial investigation ended in retirement; no organization was interviewed about
willingness to pay. The [README's judgment story](../README.md#build-measure-retire) explains the
build, measure, and retire decision. The [retained provenance](provenance/index-v1.json) makes the
historical investigation and its corrected successors inspectable.

Report updates use a documented manual Power BI Refresh now step. The report is not fully automatic.
Native Service manual refresh has been observed; a successful schedule is not proven. The
[refresh runbook](powerbi-refresh-runbook.md) states the operational boundary.

A failed capture does not erase accepted release identity. The
[pinned publication evidence](evidence/public-readme-inputs-v1.json) retains the accepted panel
separately from the latest rejected attempt. The report URL and final owner approval remain
**Phase 10** actions; this walkthrough does not imply final publication or portfolio-ready status.
