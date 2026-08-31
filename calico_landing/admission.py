"""Public admission service orchestration (D-06/D-07/D-08/D-09).

Wires the whole Gate A admission transaction into one public entry point,
`admit(candidate_input, store)`. Its flow is exactly the diagram locked in
`02-RESEARCH.md`: resolve and copy-once-stage the candidate set
(`calico_landing.candidate`), parse each staged copy exactly once
(`calico_landing.parser`), validate the complete four-list set
(`calico_landing.validation`), serialize four canonical Parquet objects
from the unchanged parsed values (`calico_landing.parquet`), and commit or
recover one immutable release revision (`calico_landing.store`). Any
failure at any stage rejects the whole set: no partial canonical output is
ever exposed, and the prior promoted release is left untouched.

This module computes no analytical status, transition, cohort, or metric --
it aggregates safe structural outcomes and calls the four lower-level
services. Every result is one immutable `calico_landing.result.AdmissionResult`;
no row, field value, or absolute local path ever crosses this module's
public boundary (D-05/D-10 non-echo discipline).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import uuid
from datetime import date
from pathlib import Path

from calico_landing.attempts import utc_now_iso, write_v2_attempt
from calico_landing.candidate import (
    CandidateError,
    reject_store_in_git_worktree,
    resolve_and_stage_candidate,
)
from calico_landing.contracts import (
    LOGICAL_LIST_ORDER,
    CsvContract,
    StatusContract,
    load_csv_contract,
    load_status_contract,
)
from calico_landing.parquet import CanonicalSerializationError, write_parquet
from calico_landing.parser import ParsedList, StructuralReject, parse_payload
from calico_landing.result import AdmissionReason, AdmissionResult, sort_reasons
from calico_landing.store import RevisionCommit, StoreError, commit_revision, ensure_store_layout
from calico_landing.validation import validate_set

#: The single locked current-release CSV contract every admission call
#: loads fresh (D-01/D-02). Resolved relative to the package's own
#: location, never the process working directory.
_CSV_CONTRACT_PATH = (
    Path(__file__).resolve().parent.parent / "contracts" / "ag-registry-csv-v1.json"
)

#: The closed source-status vocabulary contract (04-01-PLAN.md D-02/D-22).
#: Resolved relative to the package's own location, mirroring
#: `_CSV_CONTRACT_PATH`. Loading is exposed via `load_default_status_contract()`
#: below; `admit()` itself only enforces it when a caller explicitly opts in
#: through the `status_contract` parameter (see that function's docstring
#: for why this is additive-only rather than an unconditional default).
_STATUS_CONTRACT_PATH = (
    Path(__file__).resolve().parent.parent / "contracts" / "ag-registry-status-v1.json"
)

#: Locked D-08 fingerprint algorithm identifier -- must match the constant
#: recorded independently by `calico_landing.store` in every manifest.
_FINGERPRINT_ALGORITHM = "ordered-source-sha256-json-v1"


def _exception_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) else "candidate.invalid_mapping"


def _reason_from_exception(exc: Exception) -> AdmissionReason:
    return AdmissionReason(
        code=_exception_code(exc),
        logical_list=getattr(exc, "logical_list", None),
        safe_line_number=getattr(exc, "safe_line_number", None),
        safe_count=getattr(exc, "safe_count", None),
    )


def _cleanup_run_dir(run_dir: Path, staging_root: Path) -> None:
    """Remove exactly the one resolved staging child this call created --
    mirrors `calico_landing.store`'s own cleanup boundary. Never touches a
    caller-supplied store or candidate root.
    """

    if run_dir.parent != staging_root:
        return
    shutil.rmtree(run_dir, ignore_errors=True)


#: The source registry publishes the "As-of Date" column with a `/`
#: separator (e.g. `2026/07/15`); the release-identity contract this value
#: feeds (`calico_landing.store._validate_as_of_date`) requires strict ISO
#: `YYYY-MM-DD`. Accepts either separator so a future source correction to
#: the ISO form is not itself treated as a regression.
_AS_OF_DATE_RAW_PATTERN = re.compile(r"^(\d{4})[/-](\d{2})[/-](\d{2})$")


def _normalize_as_of_date(raw_value: str) -> str | None:
    """Normalize one source "As-of Date" field to strict ISO `YYYY-MM-DD`.

    Returns `None` on anything that is not a genuine calendar date in
    either separator form -- the caller's existing `as_of_date is None`
    fallback (`operational_error`) already handles that safely without
    this function ever needing to echo the offending raw value.
    """

    match = _AS_OF_DATE_RAW_PATTERN.match(raw_value)
    if match is None:
        return None
    year, month, day = match.groups()
    candidate = f"{year}-{month}-{day}"
    try:
        date.fromisoformat(candidate)
    except ValueError:
        return None
    return candidate


def _best_effort_as_of_date(
    contract: CsvContract, parsed_lists: dict[str, ParsedList]
) -> str | None:
    if "As-of Date" not in contract.headers:
        return None
    date_index = contract.headers.index("As-of Date")
    for logical_list in LOGICAL_LIST_ORDER:
        parsed = parsed_lists.get(logical_list)
        if parsed is None:
            continue
        for record in parsed.records:
            value = record.fields[date_index].strip()
            if value:
                return _normalize_as_of_date(value)
    return None


def _revision_fingerprint(raw_sha256_by_list: dict[str, str]) -> str:
    ordered = [[name, raw_sha256_by_list[name]] for name in LOGICAL_LIST_ORDER]
    framed = json.dumps(ordered, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(framed).hexdigest()


def load_default_status_contract() -> StatusContract:
    """Load the committed closed source-status vocabulary contract from its
    fixed package-relative path. Provided so a caller can opt an `admit()`
    call into status-vocabulary enforcement without hardcoding the
    contract path itself; raises `calico_landing.contracts.ContractError`
    on any malformed or missing contract document.
    """

    return load_status_contract(_STATUS_CONTRACT_PATH)


def admit(
    candidate_input: str | Path,
    store: str | Path,
    *,
    status_contract: StatusContract | None = None,
) -> AdmissionResult:
    """Admit one candidate release into `store` as a single atomic transaction.

    Copies and hashes all four candidate objects into isolated same-store
    staging, parses each staged copy exactly once, validates the complete
    set against every locked structural/date/registration-key rule, and --
    only if every check passes -- serializes four canonical Parquet objects
    plus one closed release manifest and commits/recovers one immutable
    store revision.

    `status_contract` is optional and additive (04-01-PLAN.md D-02/D-22):
    when supplied (for example via `load_default_status_contract()`), the
    complete four-list set's nonblank `Registry Status` values are also
    validated against the contract's closed 33-value vocabulary before
    admission commits anything; an unknown nonblank value rejects the
    whole candidate set through the same non-echoing `AdmissionReason`
    path as every other structural rule. When omitted (the default), no
    status-vocabulary check runs at all -- callers that do not yet supply
    a contract keep their exact existing behavior. This module's own CLI
    boundary (`calico_landing.cli`) and the still-immutable Phase 3
    `gate-b-fixture-v1.json` fixture both intentionally continue to omit
    it, since both predate the closed 33-value vocabulary and are not
    this plan's to rewrite; a later plan should migrate any remaining
    non-compliant placeholder status values before making this the
    unconditional default.

    Any failure at any stage returns a `rejected` (or `operational_error`)
    `AdmissionResult`: no partial canonical output is ever exposed, the
    unique staging child this call created is removed, and the previously
    promoted release for the affected date is left untouched.
    """

    # Captured once, at the very top of this call, per D-13: this is the one
    # true attempt identity and start boundary for the whole logical
    # `admit()` transaction, regardless of which stage ultimately decides
    # the outcome. `ended_at_utc` is captured once more at whichever single
    # `_record_v2_attempt` call below finalizes that outcome.
    attempt_id = uuid.uuid4().hex
    started_at_utc = utc_now_iso()

    def _record_v2_attempt(
        store_root: Path,
        *,
        status: str,
        as_of_date: str | None,
        release_revision: int | None = None,
        revision_fingerprint: str | None = None,
        reason_count: int | None = None,
    ) -> None:
        write_v2_attempt(
            store_root,
            attempt_id=attempt_id,
            started_at_utc=started_at_utc,
            ended_at_utc=utc_now_iso(),
            status=status,
            as_of_date=as_of_date,
            release_revision=release_revision,
            revision_fingerprint=revision_fingerprint,
            reason_count=reason_count,
        )

    try:
        reject_store_in_git_worktree(store)
    except CandidateError:
        return AdmissionResult.operational_error(reasons=())

    try:
        layout = ensure_store_layout(store)
    except StoreError:
        return AdmissionResult.operational_error(reasons=())

    try:
        contract = load_csv_contract(_CSV_CONTRACT_PATH)
    except Exception:
        return AdmissionResult.operational_error(reasons=())

    try:
        run_dir = Path(tempfile.mkdtemp(dir=str(layout.staging_root), prefix="run-"))
    except OSError:
        return AdmissionResult.operational_error(reasons=())

    raw_dir = run_dir / "raw"
    try:
        raw_dir.mkdir()
    except OSError:
        _cleanup_run_dir(run_dir, layout.staging_root)
        return AdmissionResult.operational_error(reasons=())

    reasons: list[AdmissionReason] = []

    try:
        staged_objects = resolve_and_stage_candidate(candidate_input, raw_dir, contract)
    except CandidateError as exc:
        reasons.append(_reason_from_exception(exc))
        _cleanup_run_dir(run_dir, layout.staging_root)
        result = AdmissionResult.rejected(sort_reasons(reasons))
        _record_v2_attempt(
            layout.store_root, status="rejected", as_of_date=None, reason_count=len(reasons)
        )
        return result

    parsed_lists: dict[str, ParsedList] = {}
    for logical_list in LOGICAL_LIST_ORDER:
        staged = staged_objects[logical_list]
        try:
            payload = staged.staged_path.read_bytes()
            parsed_lists[logical_list] = parse_payload(payload, logical_list, contract)
        except StructuralReject as exc:
            reasons.append(_reason_from_exception(exc))
        except OSError:
            reasons.append(AdmissionReason(code="container.open_failed", logical_list=logical_list))

    if len(parsed_lists) != len(LOGICAL_LIST_ORDER):
        best_effort_date = _best_effort_as_of_date(contract, parsed_lists)
        _cleanup_run_dir(run_dir, layout.staging_root)
        result = AdmissionResult.rejected(sort_reasons(reasons), as_of_date=best_effort_date)
        _record_v2_attempt(
            layout.store_root,
            status="rejected",
            as_of_date=best_effort_date,
            reason_count=len(reasons),
        )
        return result

    reasons.extend(validate_set(parsed_lists, status_contract=status_contract))

    if reasons:
        best_effort_date = _best_effort_as_of_date(contract, parsed_lists)
        _cleanup_run_dir(run_dir, layout.staging_root)
        result = AdmissionResult.rejected(sort_reasons(reasons), as_of_date=best_effort_date)
        _record_v2_attempt(
            layout.store_root,
            status="rejected",
            as_of_date=best_effort_date,
            reason_count=len(reasons),
        )
        return result

    as_of_date = _best_effort_as_of_date(contract, parsed_lists)
    if as_of_date is None:
        _cleanup_run_dir(run_dir, layout.staging_root)
        return AdmissionResult.operational_error(reasons=())

    canonical_dir = run_dir / "canonical"
    ndjson_staging_dir = run_dir / ".parquet_staging"
    try:
        canonical_dir.mkdir()
        ndjson_staging_dir.mkdir()
    except OSError:
        _cleanup_run_dir(run_dir, layout.staging_root)
        return AdmissionResult.operational_error(reasons=())

    parquet_artifacts = {}
    for logical_list in LOGICAL_LIST_ORDER:
        try:
            parquet_artifacts[logical_list] = write_parquet(
                parsed_lists[logical_list],
                ndjson_staging_dir,
                canonical_dir / f"{logical_list}.parquet",
                contract,
            )
        except CanonicalSerializationError as exc:
            reasons.append(_reason_from_exception(exc))

    if len(parquet_artifacts) != len(LOGICAL_LIST_ORDER):
        _cleanup_run_dir(run_dir, layout.staging_root)
        result = AdmissionResult.rejected(sort_reasons(reasons), as_of_date=as_of_date)
        _record_v2_attempt(
            layout.store_root, status="rejected", as_of_date=as_of_date, reason_count=len(reasons)
        )
        return result

    raw_sha256_by_list = {
        logical_list: staged_objects[logical_list].sha256 for logical_list in LOGICAL_LIST_ORDER
    }
    revision_fingerprint = _revision_fingerprint(raw_sha256_by_list)

    first_artifact = parquet_artifacts[LOGICAL_LIST_ORDER[0]]
    manifest_metadata: dict[str, object] = {
        "fingerprint_algorithm": _FINGERPRINT_ALGORITHM,
        "parser_contract_version": contract.contract_version,
        "parquet_writer_version": first_artifact.writer_version,
        "parquet_compression": first_artifact.compression,
        "parquet_row_group_size": first_artifact.row_group_size,
        "admission_reasons": [],
        "logical_lists": {
            logical_list: {
                "raw_sha256": staged_objects[logical_list].sha256,
                "raw_byte_count": staged_objects[logical_list].byte_count,
                "parsed_record_count": len(parsed_lists[logical_list].records),
                "line_record_reconciled": True,
                "parquet_sha256": parquet_artifacts[logical_list].sha256,
                "parquet_row_count": parquet_artifacts[logical_list].row_count,
            }
            for logical_list in LOGICAL_LIST_ORDER
        },
    }

    try:
        commit: RevisionCommit = commit_revision(
            store_root=layout.store_root,
            staged_revision_dir=run_dir,
            as_of_date=as_of_date,
            revision_fingerprint=revision_fingerprint,
            manifest_metadata=manifest_metadata,
            # This module now records the single v2 attempt for the whole
            # logical `admit()` call itself (below); disabling the store's
            # own legacy store-level v1 write here is what prevents this one
            # call from being recorded twice under two incompatible schemas
            # (D-13).
            write_attempt=False,
        )
    except StoreError:
        return AdmissionResult.operational_error(reasons=())

    if commit.status == "no_new_release":
        v2_status = "no_new_release"
    elif commit.recovered:
        v2_status = "recovered"
    else:
        v2_status = "accepted"

    _record_v2_attempt(
        layout.store_root,
        status=v2_status,
        as_of_date=commit.as_of_date,
        release_revision=commit.release_revision,
        revision_fingerprint=commit.revision_fingerprint,
    )

    if commit.status == "no_new_release":
        return AdmissionResult.no_new_release(
            commit.as_of_date, commit.release_revision, commit.revision_fingerprint
        )
    return AdmissionResult.accepted(
        commit.as_of_date, commit.release_revision, commit.revision_fingerprint
    )


__all__ = ["admit", "load_default_status_contract"]
