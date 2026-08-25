# CLAUDE.md — California Charity Registry Monitor

This file is the durable contract for how code, SQL, and documentation are written in this
repository. It governs engineering conduct, not product scope or phase status.

---

## Responsibility boundary

- **Python owns landing and admission only.** Downloading, hashing, decoding the source CSV
  contract, structural admission, and provenance. Python never computes a business metric,
  transition, cohort, or spell.
- **DuckDB and dbt Core own every analytical calculation.** Promotion, modeling, transitions,
  cohorts, spells, diagnostics, reconciliation, and publication marts live in SQL, not Python.
- **Power BI is presentation only.** It renders governed model output; it never becomes a second
  calculation layer and never recreates business logic that belongs in dbt.

## Deterministic, no-LLM calculations

Every analytical transform is a deterministic Python or SQL calculation. No LLM call sits between
a source row and a published number. Legal/compliance correctness in the published output is
deterministic, not generated.

## Source-linked claims

Every published number is attributable to a source SHA-256, an as-of date, a release revision,
and a parser-contract version. A claim without a traceable source is not published. Claims are
bounded by what the source can actually support; do not infer or promise beyond it.

## Justified dependencies

New dependencies need a stated reason tied to a current phase requirement. `requirements-dbt.txt`
is the approved list for the analytical toolchain; do not add an unpinned or unjustified package.

## Tests ship with behavior

Every behavior change ships with a test in the same phase. Tests assert observable behavior, not
implementation detail, and never echo a sensitive matched value into their own output.

## Repository hygiene

- `.gitignore` is prevention for untracked files, not proof that history is clean; the privacy
  scanner (`tools/privacy_scan`) is the proof, rerun locally and in CI on the full candidate tree
  and reachable history.
- No raw registry rows, no private database, no generated diff output, and no source PDF are ever
  committed. Ignore rules use identity-free directory prefixes or exact safe paths; never a broad
  data exception that could re-admit a future identity-bearing artifact.
- `scripts/research/**`-style investigative programs, if ever referenced, are read-for-ideas only
  and are never promoted into this production scaffold.
- Corrections are additive: a superseded figure or claim is carried forward as a documented
  correction, never silently rewritten.

## Language: outside-in, public-monitor framing

This project describes the published registry population from the outside; it never characterizes
an organization's intent, cause, risk, or performance, and it is not an internal enforcement tool.

- Use **outside-in** framing throughout: identifiers, dbt model names, column names, comments,
  documentation, and dashboard labels all describe what the public source publishes, not an
  internal workload or judgment about an organization.
- Use the exact phrase **"published delinquent population"** wherever the delinquent cohort is
  named in code, models, columns, comments, docs, or dashboard labels.
- Do not promise a legal or compliance outcome, and do not phrase output as advice. State what the
  source reports and what it does not support; do not tell a reader what to do about it.
- Do not build or publish a trust, risk, fraud, quality, or robustness score; a ranking; or a
  worst-organization leaderboard. The monitor reports published state, not a judgment.

---

*Current scope, phase status, and milestone progress live in `.planning/` in the private
`calico-build` workspace, not in this repository.*
