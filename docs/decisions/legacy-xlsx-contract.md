# Legacy XLSX contract: deferred (2019 shape)

**Status:** `deferred`
**Contract:** `contracts/ag-registry-xlsx-2019-deferred-v1.json`, `contract_version: 1`
**Unsupported result:** `contract.unsupported_xlsx` (closed D-05 reason vocabulary)

## What this decision covers

The Attorney General registry has published at least one legacy `.xlsx` release (2019). This
project does not implement an XLSX reader in v1. This record exists so that gap is explicit,
testable, and reopenable -- not silent (D-14, D-15).

## Known worksheet shape

The 2019 workbook is known, from prior investigation, to differ from the current CSV contract in
these specific ways:

- The published header row is **row 5** of the worksheet, not row 1.
- **25** otherwise-populated records carry a blank `Registry Status` value. A future
  implementation must add a `missing_registry_status` quality flag for these records rather than
  silently dropping or reinterpreting them.

No other structural claim about the 2019 workbook is made here. No complete historical census of
legacy releases exists, and no synthetic approximation of the workbook is accepted as proof of
this shape (D-14).

## Deterministic unsupported behavior

Until a real legacy release is selected for implementation, any `.xlsx` candidate input fails
closed with the single reason code `contract.unsupported_xlsx`. There is no partial parse, no
best-effort fallback to the CSV contract, and no silent skip. The `contracts/
ag-registry-xlsx-2019-deferred-v1.json` document is versioned and machine-checked
(`calico_landing.contracts.load_xlsx_contract`); `status` must read exactly `"deferred"` and
`reader_dependency_required` must read exactly `false`.

## Scope boundary

This decision does not implement, or plan the implementation of, an XLSX parser, an XLSX reader
dependency, or a mapping from the 2019 worksheet columns onto the current 11-header CSV contract.
`requirements-dbt.txt` gains no new dependency as part of this record, and no product code in this
repository imports an XLSX reader library.

## Reopening trigger

This decision is reopened only when a real legacy XLSX release is selected for admission -- not on
a schedule, and not in response to the archive-census question, which remains out of v1 scope
(D-012). Reopening means: replace `contracts/ag-registry-xlsx-2019-deferred-v1.json` with a new
versioned successor document (`status: "active"` or equivalent), add the justified reader
dependency at that time, and implement the parser against a real workbook -- never a synthetic
approximation.
