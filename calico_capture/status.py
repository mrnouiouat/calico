"""Closed positive capture status projection (06-01-PLAN.md D-08/D-09;
06-02-PLAN.md D-08/D-09; 06-RESEARCH.md "Safe status projection";
`contracts/capture-status-v1.schema.json`).

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
independently, against `contracts/capture-status-v1.schema.json`'s closed
key set and vocabularies before this module ever serializes a document
(`validate_capture_status_document`, called from `to_json()`) -- so a
caller preparing to write to stdout, a GitHub Actions job summary, or the
`published-data` branch (D-08) never trusts construction alone.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_STATUS_SCHEMA_VERSION = 1

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
_REASON_CATEGORIES = frozenset(
    {
        "none",
        "source_not_advanced",
        "structural_rejection",
        "source_transfer_error",
        "archive_error",
        "restore_error",
        "warehouse_build_error",
    }
)

_UTC_TIMESTAMP_MIN_LENGTH = len("YYYY-MM-DDTHH:MM:SSZ")

_AS_OF_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: The exact closed top-level key set every serialized `CaptureStatus`
#: document must have -- mirrors `contracts/capture-status-v1.schema.json`
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
    }
)


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
            if not isinstance(value, str) or len(value) < _UTC_TIMESTAMP_MIN_LENGTH:
                raise StatusError("status.invalid_timestamp")
        if self.last_accepted_as_of_date is not None and (
            not isinstance(self.last_accepted_as_of_date, str)
            or not _AS_OF_DATE_PATTERN.match(self.last_accepted_as_of_date)
        ):
            raise StatusError("status.invalid_last_accepted_as_of_date")
        if self.last_accepted_release_revision is not None and (
            not isinstance(self.last_accepted_release_revision, int)
            or isinstance(self.last_accepted_release_revision, bool)
            or self.last_accepted_release_revision < 1
        ):
            raise StatusError("status.invalid_last_accepted_release_revision")

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
        }

    def to_json(self) -> str:
        """The exact closed, deterministic, newline-terminated JSON shape
        (`contracts/capture-status-v1.schema.json`) -- the sole safe
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
    `contracts/capture-status-v1.schema.json` -- rejects any document with
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
        if not isinstance(value, str) or len(value) < _UTC_TIMESTAMP_MIN_LENGTH:
            raise StatusError("status.invalid_timestamp")

    last_accepted_as_of_date = document.get("last_accepted_as_of_date")
    if last_accepted_as_of_date is not None and (
        not isinstance(last_accepted_as_of_date, str)
        or not _AS_OF_DATE_PATTERN.match(last_accepted_as_of_date)
    ):
        raise StatusError("status.invalid_last_accepted_as_of_date")

    last_accepted_release_revision = document.get("last_accepted_release_revision")
    if last_accepted_release_revision is not None and (
        not isinstance(last_accepted_release_revision, int)
        or isinstance(last_accepted_release_revision, bool)
        or last_accepted_release_revision < 1
    ):
        raise StatusError("status.invalid_last_accepted_release_revision")


def project_safe_status(
    *,
    trigger: str,
    outcome: str,
    reason_category: str,
    started_at_utc: str,
    ended_at_utc: str,
    last_accepted_as_of_date: str | None = None,
    last_accepted_release_revision: int | None = None,
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
    )


__all__ = [
    "STATUS_DOCUMENT_KEYS",
    "CaptureStatus",
    "StatusError",
    "project_safe_status",
    "validate_capture_status_document",
]
