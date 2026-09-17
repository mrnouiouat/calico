"""Closed positive capture status projection (06-01-PLAN.md D-08/D-09;
06-02-PLAN.md D-08/D-09; 06-RESEARCH.md "Safe status projection";
`contracts/capture-status-v3.schema.json`).

`CaptureStatus` is the single closed, deterministic, JSON-serializable
document every `calico_capture.orchestrator.capture()` call returns. It is
built as a positive projection -- every field is explicitly assigned from a
fixed, already-safe vocabulary or a safe release-identity value -- never by
removing fields from a private `AdmissionResult` or archive transaction.
It never carries a fingerprint, path, URL, message, exception type, object/
row count, actor/job name, or source artifact (D-09).

Mirrors `calico_landing.attempts`'s exact-closed-key-set-and-enum
discipline: any caller-supplied value outside the closed vocabulary raises
`StatusError` rather than being silently coerced or echoed. Every field is
validated at construction time (`CaptureStatus.__post_init__`) *and* again,
independently, against `contracts/capture-status-v3.schema.json`'s closed
key set and vocabularies before this module ever serializes a document
(`validate_capture_status_document`, called from `to_json()`) -- so a
caller preparing to write to stdout, a GitHub Actions job summary, or the
`published-data` branch (D-08) never trusts construction alone.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime

_STATUS_SCHEMA_VERSION = 3

#: Closed trigger vocabulary (06-RESEARCH.md Pattern 3/4): scheduled cron,
#: manual `workflow_dispatch`, or the mandatory local runbook path.
_TRIGGERS = frozenset({"schedule", "workflow_dispatch", "local"})

#: The existing closed `calico_landing.result.AdmissionResult` outcome
#: vocabulary, reused verbatim as the capture status outcome (D-07): this
#: module never invents a new outcome name for the same closed concept.
_OUTCOMES = frozenset({"accepted", "no_new_release", "rejected", "operational_error"})

#: Closed reason-category vocabulary (06-RESEARCH.md "Safe status
#: projection"). Every category is fixed, provider-neutral, and never a raw
#: exception string, path, or credential fragment.
#:
#: `source_contract_mismatch` was added in schema v2: it separates "the
#: source stopped supplying the contracted four-file set in its contracted
#: shape" from `structural_rejection`, which stays a data-level rejection
#: inside a candidate that still matches the contract. The distinction is
#: derived only from already-closed admission reason codes, never from an
#: exception message or a fetched byte.
_REASON_CATEGORIES = frozenset(
    {
        "none",
        "source_not_advanced",
        "structural_rejection",
        "source_contract_mismatch",
        "source_transfer_error",
        "archive_error",
        "restore_error",
        "warehouse_build_error",
    }
)

_UTC_TIMESTAMP_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$")

_AS_OF_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: The exact closed top-level key set every serialized `CaptureStatus`
#: document must have -- mirrors `contracts/capture-status-v3.schema.json`
#: exactly (`additionalProperties: false`, every key required though some
#: are nullable). Used by both `CaptureStatus.to_dict()`'s implicit shape
#: and `validate_capture_status_document`'s explicit check on an arbitrary
#: already-serialized document.
STATUS_DOCUMENT_KEYS = frozenset(
    {
        "schema_version",
        "trigger",
        "outcome",
        "reason_category",
        "started_at_utc",
        "ended_at_utc",
        "last_accepted_as_of_date",
        "last_accepted_release_revision",
        "newer_attempt_not_accepted",
        "source_publication_state",
        "source_publication_retired_on",
    }
)


def _valid_date(value: object) -> bool:
    if not isinstance(value, str) or not _AS_OF_DATE_PATTERN.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not _UTC_TIMESTAMP_PATTERN.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


class StatusError(Exception):
    """Raised when a caller supplies a trigger, outcome, or reason category
    outside the closed vocabulary. Carries only a fixed safe `category` --
    never the offending value.
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class CaptureStatus:
    """One closed, non-echo capture outcome projection (D-09).

    `last_accepted_as_of_date`/`last_accepted_release_revision` are
    populated only for the `accepted`/`no_new_release` outcomes; every
    other outcome leaves them `None` rather than echoing a stale or
    partial value.
    """

    schema_version: int
    trigger: str
    outcome: str
    reason_category: str
    started_at_utc: str
    ended_at_utc: str
    last_accepted_as_of_date: str | None
    last_accepted_release_revision: int | None
    newer_attempt_not_accepted: bool
    source_publication_state: str
    source_publication_retired_on: str | None

    def __post_init__(self) -> None:
        if self.schema_version != _STATUS_SCHEMA_VERSION:
            raise StatusError("status.unknown_schema_version")
        if self.trigger not in _TRIGGERS:
            raise StatusError("status.unknown_trigger")
        if self.outcome not in _OUTCOMES:
            raise StatusError("status.unknown_outcome")
        if self.reason_category not in _REASON_CATEGORIES:
            raise StatusError("status.unknown_reason_category")
        for field_name, value in (
            ("started_at_utc", self.started_at_utc),
            ("ended_at_utc", self.ended_at_utc),
        ):
            if not _valid_timestamp(value):
                raise StatusError("status.invalid_timestamp")
        if self.last_accepted_as_of_date is not None and (
            not isinstance(self.last_accepted_as_of_date, str)
            or not _valid_date(self.last_accepted_as_of_date)
        ):
            raise StatusError("status.invalid_last_accepted_as_of_date")
        if self.last_accepted_release_revision is not None and (
            not isinstance(self.last_accepted_release_revision, int)
            or isinstance(self.last_accepted_release_revision, bool)
            or self.last_accepted_release_revision < 1
        ):
            raise StatusError("status.invalid_last_accepted_release_revision")

        _validate_display_fields(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "trigger": self.trigger,
            "outcome": self.outcome,
            "reason_category": self.reason_category,
            "started_at_utc": self.started_at_utc,
            "ended_at_utc": self.ended_at_utc,
            "last_accepted_as_of_date": self.last_accepted_as_of_date,
            "last_accepted_release_revision": self.last_accepted_release_revision,
            "newer_attempt_not_accepted": self.newer_attempt_not_accepted,
            "source_publication_state": self.source_publication_state,
            "source_publication_retired_on": self.source_publication_retired_on,
        }

    def to_json(self) -> str:
        """The exact closed, deterministic, newline-terminated JSON shape
        (`contracts/capture-status-v3.schema.json`) -- the sole safe
        rendering for stdout, a GitHub Actions job summary, or a
        `published-data` branch write (D-08/D-09).

        Re-validates `self.to_dict()` against the same closed schema
        `__post_init__` already enforced at construction, so this method
        never trusts construction alone before producing the document a
        caller writes somewhere externally visible.
        """

        document = self.to_dict()
        validate_capture_status_document(document)
        return (
            json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            + "\n"
        )


def validate_capture_status_document(document: object) -> None:
    """Closed-schema validation mirroring
    `contracts/capture-status-v3.schema.json` -- rejects any document with
    an unexpected type, missing/extra top-level key, unknown enum value, or
    malformed field, positively (an allowlisted key/type/vocabulary check),
    never by copying an already-decoded document and then deleting or
    masking suspect fields.

    Every `CaptureStatus` this module constructs already satisfies this
    check by construction (`__post_init__`); this function is the same
    closed check applied directly to an arbitrary already-serialized (or
    hand-built) document -- e.g. one a future reader loads back from the
    `published-data` branch before trusting it, or the shape `to_json()`
    itself re-checks before ever returning a string a caller might write to
    stdout, a job summary, or that branch (D-08/D-09).
    """

    if not isinstance(document, dict) or set(document.keys()) != STATUS_DOCUMENT_KEYS:
        raise StatusError("status.malformed_document")
    if document.get("schema_version") != _STATUS_SCHEMA_VERSION:
        raise StatusError("status.unknown_schema_version")
    if document.get("trigger") not in _TRIGGERS:
        raise StatusError("status.unknown_trigger")
    if document.get("outcome") not in _OUTCOMES:
        raise StatusError("status.unknown_outcome")
    if document.get("reason_category") not in _REASON_CATEGORIES:
        raise StatusError("status.unknown_reason_category")
    for field_name in ("started_at_utc", "ended_at_utc"):
        value = document.get(field_name)
        if not _valid_timestamp(value):
            raise StatusError("status.invalid_timestamp")

    last_accepted_as_of_date = document.get("last_accepted_as_of_date")
    if last_accepted_as_of_date is not None and (
        not isinstance(last_accepted_as_of_date, str)
        or not _valid_date(last_accepted_as_of_date)
    ):
        raise StatusError("status.invalid_last_accepted_as_of_date")

    last_accepted_release_revision = document.get("last_accepted_release_revision")
    if last_accepted_release_revision is not None and (
        not isinstance(last_accepted_release_revision, int)
        or isinstance(last_accepted_release_revision, bool)
        or last_accepted_release_revision < 1
    ):
        raise StatusError("status.invalid_last_accepted_release_revision")


    _validate_display_fields(document)


def _validate_display_fields(document: dict[str, object]) -> None:
    if document.get("outcome") in {"rejected", "operational_error"} and (
        document.get("last_accepted_as_of_date") is not None or
        document.get("last_accepted_release_revision") is not None
    ):
        raise StatusError("status.invalid_failure_release_identity")
    warning = document.get("newer_attempt_not_accepted")
    if type(warning) is not bool:
        raise StatusError("status.invalid_publication_warning")
    if document.get("outcome") in {"rejected", "operational_error"} and not warning:
        raise StatusError("status.invalid_publication_warning")
    if document.get("outcome") == "no_new_release" and warning:
        raise StatusError("status.invalid_publication_warning")
    state = document.get("source_publication_state")
    retired_on = document.get("source_publication_retired_on")
    if not isinstance(state, str) or state not in {"active", "retired"}:
        raise StatusError("status.invalid_source_publication_state")
    if (state == "active" and retired_on is not None) or (
        state == "retired" and (not isinstance(retired_on, str) or
                               not _valid_date(retired_on))
    ):
        raise StatusError("status.invalid_source_retirement_date")


def project_display_state(*, outcome: str, publication_succeeded: bool | None = None) -> dict[str, object]:
    """The sole display-policy projection; retirement is an explicit source decision.

    An accepted capture is pending until a successful publication is observed.
    This projection never constructs a published release identity from attempt fields.
    """
    if outcome not in _OUTCOMES:
        raise StatusError("status.unknown_outcome")
    if publication_succeeded is not None and type(publication_succeeded) is not bool:
        raise StatusError("status.invalid_publication_result")
    return {
        "newer_attempt_not_accepted": outcome in {"rejected", "operational_error"} or
                                     (outcome == "accepted" and publication_succeeded is not True),
        "source_publication_state": "retired",
        "source_publication_retired_on": "2026-09-02",
    }


def project_publication_status(document: object, *, publication_succeeded: bool) -> CaptureStatus:
    """Apply the observed publication result to an already validated attempt."""
    validate_capture_status_document(document)
    assert isinstance(document, dict)
    return project_safe_status(
        trigger=document["trigger"], outcome=document["outcome"],
        reason_category=document["reason_category"],
        started_at_utc=document["started_at_utc"], ended_at_utc=document["ended_at_utc"],
        last_accepted_as_of_date=document["last_accepted_as_of_date"],
        last_accepted_release_revision=document["last_accepted_release_revision"],
        publication_succeeded=publication_succeeded,
    )


def project_safe_status(
    *,
    trigger: str,
    outcome: str,
    reason_category: str,
    started_at_utc: str,
    ended_at_utc: str,
    last_accepted_as_of_date: str | None = None,
    last_accepted_release_revision: int | None = None,
    publication_succeeded: bool | None = None,
) -> CaptureStatus:
    """Build one closed, validated `CaptureStatus` (the sole constructor
    every `calico_capture` caller uses -- never `CaptureStatus(...)`
    directly outside this module, mirroring `AdmissionResult`'s own
    classmethod-constructor discipline).
    """

    return CaptureStatus(
        schema_version=_STATUS_SCHEMA_VERSION,
        trigger=trigger,
        outcome=outcome,
        reason_category=reason_category,
        started_at_utc=started_at_utc,
        ended_at_utc=ended_at_utc,
        last_accepted_as_of_date=last_accepted_as_of_date,
        last_accepted_release_revision=last_accepted_release_revision,
        **project_display_state(outcome=outcome, publication_succeeded=publication_succeeded),
    )


__all__ = [
    "STATUS_DOCUMENT_KEYS",
    "CaptureStatus",
    "StatusError",
    "project_safe_status",
    "project_display_state",
    "project_publication_status",
    "validate_capture_status_document",
]
