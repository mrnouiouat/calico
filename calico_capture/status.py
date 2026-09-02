"""Closed positive capture status projection (06-01-PLAN.md D-08/D-09;
06-RESEARCH.md "Safe status projection").

`CaptureStatus` is the single closed, deterministic, JSON-serializable
document every `calico_capture.orchestrator.capture()` call returns. It is
built as a positive projection -- every field is explicitly assigned from a
fixed, already-safe vocabulary or a safe release-identity value -- never by
removing fields from a private `AdmissionResult` or archive transaction.
It never carries a fingerprint, path, URL, message, exception type, object/
row count, actor/job name, or source artifact (D-09).

Mirrors `calico_landing.attempts`'s exact-closed-key-set-and-enum
discipline: any caller-supplied value outside the closed vocabulary raises
`StatusError` rather than being silently coerced or echoed.
"""

from __future__ import annotations

import json
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
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
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


__all__ = ["CaptureStatus", "StatusError", "project_safe_status"]
