# Build Modes (Gate B)

Calico's dbt project runs behind one stable command surface, `python -m calico_dbt build`, with
exactly two modes. Both modes resolve to the identical dbt DAG, model selection, and test suite
(D-03/D-04) after their respective input preflight; mode selection only changes how the four
canonical registry relations are discovered and verified before dbt ever reads them.

## Fixture mode (public, default)

```
python -m calico_dbt build --mode fixture
```

This is the safe default and the only mode public CI ever runs. It requires no owner-supplied
path, no network access, and no real registry identities: it admits the committed, identity-free
Gate B fixture (`tests/fixtures/dbt_foundation/`) through the same `calico_landing.admission.admit()`
boundary real releases go through, derives an ephemeral manifest-anchor catalog from that
fixture's own just-written manifests, and runs the complete `dbt build` selection and tests
against it. A clean checkout can run this command end to end with no additional setup beyond the
pinned `requirements-dbt.txt` environment.

## Real mode (local, explicit)

```
python -m calico_dbt build --mode real --store <owner-supplied-admitted-store-path> --proof-output
```

Real mode is always explicit and always local. `--store` must point at an owner-controlled
admitted-release store that lives outside every Git worktree; the command verifies every
committed catalog anchor (`contracts/dbt-input-catalog-v1.json`) against that store's own revision
manifests and canonical Parquet objects before dbt reads a single row, and it fails closed on any
mismatch. It also requires that store to carry the private `public-eligibility-v1.json` exclusion
sidecar and fails closed with `preflight.public_eligibility_missing` without one: since the owner's
2026-09-15 decision an unmatched registration key defaults to `eligible`, so a missing sidecar would
publish every identifiable key rather than none. Fixture mode requires no sidecar -- the committed
identity-free fixture legitimately has none and publishes nothing. `--proof-output` atomically writes the fixed, safe `docs/evidence/gate-b/real-build-proof-v2.json`
document from the runner's own `SafeBuildProof` -- a category/count/status summary only, never a
path, row, or excluded value -- plus an explicit `(path, sha256)` `supersedes` reference to the
immutable Phase 3 `docs/evidence/gate-b/real-build-proof-v1.json` document, which `--proof-output`
never reads for anything but that hash and never modifies (D-22, T-04-06C). Real mode runs the
identical dbt selection and tests fixture mode runs; no analytical SQL, model name, or test forks
on mode.

## Docs proof (fixture-only)

```
python -m calico_dbt docs --mode fixture
```

`docs` always runs in fixture mode -- there is no real-mode docs proof and no `--store` argument.
It runs the identical fixture-mode `dbt build` this project's `build --mode fixture` runs, then
pinned dbt 1.10.23 `docs generate`, inside the same kind of runner-owned temporary root. The command
prints a fixed, safe count-only proof (model/test/node counts, plus generated docs-node/artifact
counts) and deletes every generated `target`/`catalog`/`log`/database artifact before exiting, on
success or failure alike -- nothing this command produces is ever committed or left on disk.

## Honest reproducibility boundary

The real admitted-release store this project's own Gate B proof was built against is intentionally
private and lives outside both Git worktrees. Public CI can prove fixture mode end to end, but it
cannot rerun real mode: the canonical registry objects the real command reads are not published
anywhere in this repository or its history, by design (D-02/D-15). Anyone outside the project can
verify fixture-mode behavior in full; verifying real-mode behavior against the actual registry
requires the owner's own private admitted store and cannot be reproduced from what is committed
here.

## Correction — 2026-10-01: private policy enables hosted republish

**Supersedes:** the earlier "always local" real-mode boundary above and the 2026-09-16
manual-only publication requirement. Fixture CI remains public and identity-free. Real builds
also run inside the authorized hosted publication job after B2-only restore to an empty external
runner-owned store; public CI cannot reproduce the private input. The production path restores
the full catalog and exact bound policy before build/export/publication, preserving the same SQL
DAG and fail-closed `preflight.public_eligibility_missing` defense. No local-sidecar bypass exists.

The owner must run `python -m calico_capture seed-policy` against the admitted store and pinned
public manifest/commit first; exact-version readback verifies the immutable private policy bundle.
The existing automation key retains exactly `listFiles`, `readFiles`, `writeFiles`; the existing
publication key retains exactly `listFiles`, `readFiles`, on the same private bucket and
archive/v1/ prefix. No new key or wider prefix is required.

The hosted mechanism uses `mode=republish` and the production real publication CLI, with no
workflow artifact or cache, no private policy in status/logs, and category-only output. Safe
evidence is policy SHA-256 and `classification_version`. A successful hosted proof remains
pending owner seed/readback and evidence collection. The source retired on 2026-09-02: republish
is not an accepted live capture, and skipped schedules are not rejected. Replay proves accepted
trigger chaining; it does not create a new live release.

Power BI keeps manual **Refresh now** as the condition-6 Service fallback; hosted publication
does not prove scheduled Power BI reliability or make the report fully automatic. Private policy
retention includes every immutable version. If the owner later chooses deletion, deliberately
purge **every immutable private version**, including policy objects and private manifest versions;
a latest-copy hide/delete is insufficient. No deletion capability is added. See the dated
[capture runbook correction](capture-runbook.md#correction--2026-10-01-private-policy-enables-hosted-republish).
