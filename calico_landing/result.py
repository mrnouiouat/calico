"""Immutable, non-echo admission result and ordered-reason models.

Locks D-04/D-05: a machine-readable JSON result with an exact closed key
set, a stable human status line, and a stable ordered rejection/error-reason
vocabulary. No field ever carries a raw registry row, matched excluded
value, absolute path, or exception text -- only closed-vocabulary codes,
safe counts, and safe locations (mirrored from
`tools/privacy_scan/scanner.py`'s value-free `Finding`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

#: Locked D-05 reason-code vocabulary and deterministic rank order.
#: Source: 02-RESEARCH.md "Safe Reason Ordering". This is a versioned
#: interface -- codes and ranks are additive, never silently renumbered.
REASON_RANK: dict[str, int] = {
    "candidate.invalid_mapping": 10,
    "transfer.length_mismatch": 20,
    "container.open_failed": 30,
    "contract.unsupported_xlsx": 40,
    "parse.decode_failed": 50,
    "parse.header_mismatch": 60,
    "parse.arity_mismatch": 70,
    "parse.line_record_mismatch": 80,
    "date.blank": 90,
    "date.mismatch": 100,
    "registration.unknown_format": 110,
    "registration.duplicate": 120,
    "canonical.serialization_failed": 130,
    "revision.invalid_same_date": 140,
    "store.busy": 150,
}

#: Locked D-08 canonical logical-list order (identical to
#: `calico_landing.contracts.LOGICAL_LIST_ORDER`); also used to rank reasons
#: deterministically within one reason-code tier.
_LOGICAL_LIST_RANK: dict[str, int] = {
    "charities-may-operate": 0,
    "charities-not-operating": 1,
    "charities-undetermined-status": 2,
    "charities-may-not-operate": 3,
}

#: Reasons with no associated logical list (structural/container/store-level
#: failures) sort ahead of any list-scoped reason within the same rank tier.
_NO_LOGICAL_LIST_RANK = -1

#: Locked D-04 status vocabulary and exit-code mapping.
_EXIT_CODE_BY_STATUS: dict[str, int] = {
    "accepted": 0,
    "rejected": 1,
    "no_new_release": 2,
    "operational_error": 3,
}

_STATUSES = frozenset(_EXIT_CODE_BY_STATUS)

#: The exact closed result-document schema version (contracts/admission-result-v1.schema.json).
RESULT_SCHEMA_VERSION = 1


class ResultError(Exception):
    """Raised when a caller supplies a reason code, logical list, or status
    outside the closed vocabulary. Carries only a fixed safe `category`.
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _logical_list_rank(logical_list: str | None) -> int:
    if logical_list is None:
        return _NO_LOGICAL_LIST_RANK
    return _LOGICAL_LIST_RANK.get(logical_list, _NO_LOGICAL_LIST_RANK)


@dataclass(frozen=True)
class AdmissionReason:
    """One safe, value-free rejection/error reason (D-05).

    Never carries a raw value, raw row, absolute path, or exception text --
    only a closed-vocabulary `code`, an optional logical-list identifier,
    and optional safe line/location/count fields.
    """

    code: str
    logical_list: str | None = None
    safe_line_number: int | None = None
    safe_location: str | None = None
    safe_count: int | None = None

    def __post_init__(self) -> None:
        if self.code not in REASON_RANK:
            raise ResultError("unknown_reason_code")
        if self.logical_list is not None and self.logical_list not in _LOGICAL_LIST_RANK:
            raise ResultError("unknown_logical_list")

    def sort_key(self) -> tuple[int, int, int]:
        return (
            REASON_RANK[self.code],
            _logical_list_rank(self.logical_list),
            self.safe_line_number if self.safe_line_number is not None else 0,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "logical_list": self.logical_list,
            "safe_line_number": self.safe_line_number,
            "safe_location": self.safe_location,
            "safe_count": self.safe_count,
        }


def sort_reasons(
    reasons: "tuple[AdmissionReason, ...] | list[AdmissionReason]",
) -> tuple[AdmissionReason, ...]:
    """Return `reasons` in the locked deterministic D-05 order:
    `(reason_rank, logical_list_rank, safe_line_number)`.
    """

    return tuple(sorted(reasons, key=lambda reason: reason.sort_key()))


@dataclass(frozen=True)
class AdmissionResult:
    """Immutable, non-echo admission outcome (D-04).

    Exposes only a closed status vocabulary, safe release-identity fields,
    and an ordered, value-free reason list. `exit_code` is the stable,
    locked per-status process exit mapping.
    """

    status: str
    as_of_date: str | None
    release_revision: int | None
    revision_fingerprint: str | None
    reasons: tuple[AdmissionReason, ...]

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            raise ResultError("unknown_status")
        object.__setattr__(self, "reasons", sort_reasons(self.reasons))

    @property
    def exit_code(self) -> int:
        return _EXIT_CODE_BY_STATUS[self.status]

    @classmethod
    def accepted(
        cls, as_of_date: str, release_revision: int, revision_fingerprint: str
    ) -> "AdmissionResult":
        return cls(
            status="accepted",
            as_of_date=as_of_date,
            release_revision=release_revision,
            revision_fingerprint=revision_fingerprint,
            reasons=(),
        )

    @classmethod
    def no_new_release(
        cls, as_of_date: str, release_revision: int, revision_fingerprint: str
    ) -> "AdmissionResult":
        return cls(
            status="no_new_release",
            as_of_date=as_of_date,
            release_revision=release_revision,
            revision_fingerprint=revision_fingerprint,
            reasons=(),
        )

    @classmethod
    def rejected(
        cls,
        reasons: "tuple[AdmissionReason, ...] | list[AdmissionReason]",
        *,
        as_of_date: str | None = None,
    ) -> "AdmissionResult":
        return cls(
            status="rejected",
            as_of_date=as_of_date,
            release_revision=None,
            revision_fingerprint=None,
            reasons=tuple(reasons),
        )

    @classmethod
    def operational_error(
        cls, reasons: "tuple[AdmissionReason, ...] | list[AdmissionReason]"
    ) -> "AdmissionResult":
        return cls(
            status="operational_error",
            as_of_date=None,
            release_revision=None,
            revision_fingerprint=None,
            reasons=tuple(reasons),
        )

    def render_status(self) -> str:
        """A concise, human, non-echo status line (D-04)."""

        if self.status == "accepted":
            return f"accepted as_of={self.as_of_date} revision={self.release_revision}"
        if self.status == "no_new_release":
            return f"no_new_release as_of={self.as_of_date} revision={self.release_revision}"
        if self.status == "rejected":
            return f"rejected reasons={len(self.reasons)}"
        return f"operational_error reasons={len(self.reasons)}"

    def to_json(self) -> str:
        """The exact closed, deterministic JSON shape
        (contracts/admission-result-v1.schema.json).
        """

        document = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "status": self.status,
            "as_of_date": self.as_of_date,
            "release_revision": self.release_revision,
            "revision_fingerprint": self.revision_fingerprint,
            "reasons": [reason.to_dict() for reason in self.reasons],
        }
        return json.dumps(document, separators=(",", ":"), ensure_ascii=True)
