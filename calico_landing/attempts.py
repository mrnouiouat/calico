"""One closed v1/v2 durable capture-attempt boundary (04-04-PLAN.md D-02/D-12/D-13).

`calico_landing.admission` and `calico_landing.store` each historically wrote
their own timing-less structural attempt record on every decided admission
outcome -- two incompatible shapes under the same `schema_version: 1`:
admission-level (`status`/`as_of_date`/`reason_count`, written before a
candidate ever reaches the store) and store-level (`as_of_date`/
`revision_fingerprint`/`status`/`release_revision`/`recovered`, written by
`calico_landing.store.commit_revision`). Neither shape has ever carried
timing, and neither is rewritten here.

This module adds one additive v2 shape that records one true attempt
identity plus the actual UTC start/end captured once at the outer `admit()`
boundary, and is now the *only* shape `admit()` writes for every new call
(`write_v2_attempt`). It also validates and safely parses all three closed
shapes (`parse_attempt_document`/`load_attempt_file`) so a later dbt
preflight step can bind whatever mix of historical v1 and new v2 records a
store actually contains into one nullable structural relation.

This module performs no analytical classification, outcome normalization,
or duration calculation (D-02) -- that is exclusively
`dbt/models/intermediate/int_capture_runs.sql`'s job. It only validates
document structure and durably writes bytes, using the project's existing
sibling-temp/flush/fsync/atomic-replace discipline
(`calico_landing.admission`/`calico_landing.store`'s legacy writers).

Every failure crosses this module's boundary as an `AttemptError` carrying
only a fixed safe `category` -- never an offending path, raw document, or
parsed value (mirrors `calico_landing.store.StoreError`'s non-echo
discipline).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

_ATTEMPT_SCHEMA_VERSION_V1 = 1
_ATTEMPT_SCHEMA_VERSION_V2 = 2

#: Exact closed key sets for the three shapes this module ever accepts.
#: Mirrors the plan's own locked `<interfaces>` block verbatim.
_ADMISSION_V1_KEYS = frozenset(
    {"schema_version", "attempt_id", "status", "as_of_date", "reason_count"}
)
_STORE_V1_KEYS = frozenset(
    {
        "schema_version",
        "attempt_id",
        "as_of_date",
        "revision_fingerprint",
        "status",
        "release_revision",
        "recovered",
    }
)
_V2_KEYS = frozenset(
    {
        "schema_version",
        "attempt_id",
        "started_at_utc",
        "ended_at_utc",
        "status",
        "as_of_date",
        "release_revision",
        "revision_fingerprint",
        "reason_count",
    }
)

#: Closed status vocabularies observed/permitted per shape. `admission.py`'s
#: legacy writer only ever recorded `"rejected"` (every call site sits on a
#: rejection path); `store.py`'s legacy writer only ever recorded
#: `"accepted"` or `"no_new_release"` (its own `RevisionCommit.status`
#: contract). v2's structural status additionally distinguishes `"recovered"`
#: from an ordinary fresh `"accepted"` commit (D-12).
_ADMISSION_V1_STATUSES = frozenset({"rejected"})
_STORE_V1_STATUSES = frozenset({"accepted", "no_new_release"})
_V2_STATUSES = frozenset({"accepted", "no_new_release", "rejected", "recovered"})

_AS_OF_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_UTC_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

_ATTEMPTS_DIRNAME = "attempts"


class AttemptError(Exception):
    """Raised on any malformed, unknown-version, or aliased attempt
    document. Carries only a fixed safe `category` -- never a path or raw
    document content (mirrors `calico_landing.store.StoreError`).
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class AdmissionV1Attempt:
    """The existing admission-level v1 shape
    (`calico_landing.admission`'s historical rejected-before-store writer).
    Timing is always unavailable -- this shape predates any UTC capture
    boundary and is never rewritten.
    """

    attempt_id: str
    status: str
    as_of_date: str | None
    reason_count: int


@dataclass(frozen=True)
class StoreV1Attempt:
    """The existing store-level v1 shape
    (`calico_landing.store.commit_revision`'s historical writer, still the
    default for any direct caller that does not opt out via
    `write_attempt=False`). Timing is always unavailable.
    """

    attempt_id: str
    status: str
    as_of_date: str
    revision_fingerprint: str
    release_revision: int
    recovered: bool


@dataclass(frozen=True)
class V2Attempt:
    """The additive v2 shape (D-13): one true attempt identity with actual
    UTC start/end captured once at the `admit()` boundary. `status` is
    closed to the four structural capture outcomes; the remaining nullable
    fields carry only the release identity/reason count needed to
    distinguish them -- never a derived duration or classification.
    """

    attempt_id: str
    status: str
    started_at_utc: str
    ended_at_utc: str
    as_of_date: str | None
    release_revision: int | None
    revision_fingerprint: str | None
    reason_count: int | None


def utc_now_iso() -> str:
    """One timezone-aware UTC timestamp in the closed v2 `...Z` millisecond
    form. Called once for `started_at_utc` and once more for `ended_at_utc`
    at the outer `admit()` boundary -- never inferred from file metadata,
    an identifier, or a release/admission date (D-13).
    """

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _require_str(value: object) -> str:
    if not isinstance(value, str):
        raise AttemptError("attempt.invalid_document_schema")
    return value


def _require_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise AttemptError("attempt.invalid_document_schema")
    return value


def _require_optional_int(value: object) -> int | None:
    if value is None:
        return None
    return _require_int(value)


def _require_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise AttemptError("attempt.invalid_document_schema")
    return value


def _require_as_of_date(value: object) -> str:
    text = _require_str(value)
    if not _AS_OF_DATE_PATTERN.match(text):
        raise AttemptError("attempt.invalid_document_schema")
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise AttemptError("attempt.invalid_document_schema") from exc
    return text


def _require_optional_as_of_date(value: object) -> str | None:
    if value is None:
        return None
    return _require_as_of_date(value)


def _require_fingerprint(value: object) -> str:
    text = _require_str(value)
    if not _FINGERPRINT_PATTERN.match(text):
        raise AttemptError("attempt.invalid_document_schema")
    return text


def _require_optional_fingerprint(value: object) -> str | None:
    if value is None:
        return None
    return _require_fingerprint(value)


def _require_utc_timestamp(value: object) -> str:
    text = _require_str(value)
    if not _UTC_TIMESTAMP_PATTERN.match(text):
        raise AttemptError("attempt.invalid_document_schema")
    return text


def _parse_admission_v1(document: dict) -> AdmissionV1Attempt:
    status = _require_str(document["status"])
    if status not in _ADMISSION_V1_STATUSES:
        raise AttemptError("attempt.invalid_document_schema")
    return AdmissionV1Attempt(
        attempt_id=_require_str(document["attempt_id"]),
        status=status,
        as_of_date=_require_optional_as_of_date(document["as_of_date"]),
        reason_count=_require_int(document["reason_count"]),
    )


def _parse_store_v1(document: dict) -> StoreV1Attempt:
    status = _require_str(document["status"])
    if status not in _STORE_V1_STATUSES:
        raise AttemptError("attempt.invalid_document_schema")
    return StoreV1Attempt(
        attempt_id=_require_str(document["attempt_id"]),
        status=status,
        as_of_date=_require_as_of_date(document["as_of_date"]),
        revision_fingerprint=_require_fingerprint(document["revision_fingerprint"]),
        release_revision=_require_int(document["release_revision"]),
        recovered=_require_bool(document["recovered"]),
    )


def _parse_v2(document: dict) -> V2Attempt:
    status = _require_str(document["status"])
    if status not in _V2_STATUSES:
        raise AttemptError("attempt.invalid_document_schema")
    return V2Attempt(
        attempt_id=_require_str(document["attempt_id"]),
        status=status,
        started_at_utc=_require_utc_timestamp(document["started_at_utc"]),
        ended_at_utc=_require_utc_timestamp(document["ended_at_utc"]),
        as_of_date=_require_optional_as_of_date(document["as_of_date"]),
        release_revision=_require_optional_int(document["release_revision"]),
        revision_fingerprint=_require_optional_fingerprint(document["revision_fingerprint"]),
        reason_count=_require_optional_int(document["reason_count"]),
    )


def parse_attempt_document(
    document: object,
) -> AdmissionV1Attempt | StoreV1Attempt | V2Attempt:
    """Validate one already-decoded JSON document against the three closed
    attempt shapes and return the matching safe dataclass.

    Rejects any document whose key set does not exactly match one shape,
    any unknown `schema_version`, and any field of the wrong type,
    vocabulary, or format. Never echoes the offending document -- every
    failure carries only the fixed `attempt.invalid_document_schema` or
    `attempt.unsupported_schema_version` category.
    """

    if not isinstance(document, dict):
        raise AttemptError("attempt.invalid_document_schema")

    schema_version = document.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise AttemptError("attempt.invalid_document_schema")

    keys = frozenset(document.keys())

    if schema_version == _ATTEMPT_SCHEMA_VERSION_V1:
        if keys == _ADMISSION_V1_KEYS:
            return _parse_admission_v1(document)
        if keys == _STORE_V1_KEYS:
            return _parse_store_v1(document)
        raise AttemptError("attempt.invalid_document_schema")

    if schema_version == _ATTEMPT_SCHEMA_VERSION_V2:
        if keys != _V2_KEYS:
            raise AttemptError("attempt.invalid_document_schema")
        return _parse_v2(document)

    raise AttemptError("attempt.unsupported_schema_version")


def load_attempt_file(path: Path) -> AdmissionV1Attempt | StoreV1Attempt | V2Attempt:
    """Read and validate exactly one attempt JSON file.

    Rejects a symlink or reparse-point alias at `path` itself (T-04-04A).
    A caller enumerating a directory (`calico_dbt.preflight`) must
    additionally restrict itself to exact direct children before calling
    this -- this function only defends the one file it is given.
    """

    if path.is_symlink():
        raise AttemptError("attempt.link_rejected")
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise AttemptError("attempt.read_failed") from exc
    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AttemptError("attempt.invalid_document_schema") from exc
    return parse_attempt_document(document)


def write_v2_attempt(
    store_root: Path,
    *,
    attempt_id: str,
    started_at_utc: str,
    ended_at_utc: str,
    status: str,
    as_of_date: str | None,
    release_revision: int | None,
    revision_fingerprint: str | None,
    reason_count: int | None,
) -> None:
    """Durably write one new v2 attempt record.

    Uses the project's existing sibling-temp-file, flush, fsync, atomic-
    replace discipline (mirrors `calico_landing.admission`/
    `calico_landing.store`'s legacy writers). Best-effort: a failure here
    never masks the already-decided admission outcome, exactly like the
    legacy v1 writers this is now the sole replacement for on every new
    `admit()` call.
    """

    if status not in _V2_STATUSES:
        raise AttemptError("attempt.invalid_status")

    attempts_dir = store_root / _ATTEMPTS_DIRNAME
    document = {
        "schema_version": _ATTEMPT_SCHEMA_VERSION_V2,
        "attempt_id": attempt_id,
        "started_at_utc": started_at_utc,
        "ended_at_utc": ended_at_utc,
        "status": status,
        "as_of_date": as_of_date,
        "release_revision": release_revision,
        "revision_fingerprint": revision_fingerprint,
        "reason_count": reason_count,
    }
    try:
        fd, temp_name = tempfile.mkstemp(prefix=".attempt.", suffix=".tmp", dir=attempts_dir)
    except OSError:
        return
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, attempts_dir / f"{attempt_id}.json")
    except OSError:
        temp_path.unlink(missing_ok=True)


__all__ = [
    "AttemptError",
    "AdmissionV1Attempt",
    "StoreV1Attempt",
    "V2Attempt",
    "parse_attempt_document",
    "load_attempt_file",
    "write_v2_attempt",
    "utc_now_iso",
]
