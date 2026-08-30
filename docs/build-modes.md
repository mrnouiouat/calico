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
mismatch. `--proof-output` atomically writes the fixed, safe `docs/evidence/gate-b/real-build-proof-v1.json`
document from the runner's own `SafeBuildProof` -- a category/count/status summary only, never a
path, row, or excluded value. Real mode runs the identical dbt selection and tests fixture mode
runs; no analytical SQL, model name, or test forks on mode.

## Honest reproducibility boundary

The real admitted-release store this project's own Gate B proof was built against is intentionally
private and lives outside both Git worktrees. Public CI can prove fixture mode end to end, but it
cannot rerun real mode: the canonical registry objects the real command reads are not published
anywhere in this repository or its history, by design (D-02/D-15). Anyone outside the project can
verify fixture-mode behavior in full; verifying real-mode behavior against the actual registry
requires the owner's own private admitted store and cannot be reproduced from what is committed
here.
