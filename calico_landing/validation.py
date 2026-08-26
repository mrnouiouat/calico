"""Set-level admission validators over already-parsed values (D-05/D-07).

Consumes the four `calico_landing.parser.ParsedList` results unchanged --
`calico_landing.candidate` and `calico_landing.parser` have already proven
mapping/transfer/container success and exact header/arity/line-record
reconciliation by the time this module runs. `validate_set` enforces the
remaining locked set-level rules: every row carries a nonblank date, one
date is shared across every row in every list, every nonblank registration
key matches one of the three approved classified formats, and every
nonblank key is unique both within its own list and across the whole set.

Every check builds a stripped (`str.strip()`) validation-only view for
comparison; the original decoded field strings are never mutated, and no
check here ever returns a raw row, key, or date value -- only
`calico_landing.result.AdmissionReason` instances carrying a closed reason
code, the logical-list identifier, and a safe physical line number
(D-05/D-10 non-echo discipline).
"""

from __future__ import annotations

import re
from collections import Counter

from calico_landing.contracts import LOGICAL_LIST_ORDER
from calico_landing.parser import ParsedList
from calico_landing.result import AdmissionReason

_AS_OF_DATE_HEADER = "As-of Date"
_REG_NUMBER_HEADER = "State Charity Reg#"

#: The three locked approved nonblank registration-key families
#: (02-RESEARCH.md "Three classified families"): anchored bare digits,
#: `CT`-prefixed digits, and `EX`-prefixed digits. No other nonblank shape
#: is approved.
_BARE_DIGITS_PATTERN = re.compile(r"^\d+$")
_CT_FAMILY_PATTERN = re.compile(r"^CT\d+$")
_EX_FAMILY_PATTERN = re.compile(r"^EX\d+$")


class ValidationError(Exception):
    """Raised when a parsed list's own header set does not carry the
    columns this module's set-level rules depend on. Carries only a fixed
    safe `code` -- never a header value.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _column_index(headers: tuple[str, ...], name: str) -> int:
    try:
        return headers.index(name)
    except ValueError as exc:
        raise ValidationError("validation.missing_required_column") from exc


def _is_approved_registration_family(value: str) -> bool:
    return bool(
        _BARE_DIGITS_PATTERN.match(value)
        or _CT_FAMILY_PATTERN.match(value)
        or _EX_FAMILY_PATTERN.match(value)
    )


def _shared_date(nonblank_dates: list[str]) -> str | None:
    """Return the most common nonblank date value, the deterministic
    "expected shared date" every record is compared against. Ties break on
    the lexicographically smallest value so the result never depends on
    dict/record iteration order.
    """

    if not nonblank_dates:
        return None
    counts = Counter(nonblank_dates)
    max_count = max(counts.values())
    tied = sorted(value for value, count in counts.items() if count == max_count)
    return tied[0]


def validate_set(parsed_lists: dict[str, ParsedList]) -> tuple[AdmissionReason, ...]:
    """Validate the complete four-list set's date and registration-key rules.

    `parsed_lists` must map every one of `LOGICAL_LIST_ORDER` to a
    successfully parsed `ParsedList` (candidate resolution, transfer,
    container, header, and arity checks have already passed for all four).
    Returns an empty tuple when every rule passes; otherwise every
    violation found, in arbitrary order -- the caller applies
    `calico_landing.result.sort_reasons` for the locked deterministic
    ordering.
    """

    reasons: list[AdmissionReason] = []
    nonblank_dates: list[str] = []
    date_by_record: list[tuple[str, int, str]] = []
    key_occurrences: dict[str, list[tuple[str, int]]] = {}

    for logical_list in LOGICAL_LIST_ORDER:
        parsed = parsed_lists[logical_list]
        date_index = _column_index(parsed.headers, _AS_OF_DATE_HEADER)
        reg_index = _column_index(parsed.headers, _REG_NUMBER_HEADER)

        for record in parsed.records:
            date_value = record.fields[date_index].strip()
            if not date_value:
                reasons.append(
                    AdmissionReason(
                        code="date.blank",
                        logical_list=logical_list,
                        safe_line_number=record.source_line_no,
                    )
                )
            else:
                nonblank_dates.append(date_value)
                date_by_record.append((logical_list, record.source_line_no, date_value))

            reg_value = record.fields[reg_index].strip()
            if not reg_value:
                continue

            if not _is_approved_registration_family(reg_value):
                reasons.append(
                    AdmissionReason(
                        code="registration.unknown_format",
                        logical_list=logical_list,
                        safe_line_number=record.source_line_no,
                    )
                )

            key_occurrences.setdefault(reg_value, []).append((logical_list, record.source_line_no))

    shared_date = _shared_date(nonblank_dates)
    if shared_date is not None:
        for logical_list, line_no, date_value in date_by_record:
            if date_value != shared_date:
                reasons.append(
                    AdmissionReason(
                        code="date.mismatch",
                        logical_list=logical_list,
                        safe_line_number=line_no,
                    )
                )

    for occurrences in key_occurrences.values():
        if len(occurrences) <= 1:
            continue
        for logical_list, line_no in occurrences:
            reasons.append(
                AdmissionReason(
                    code="registration.duplicate",
                    logical_list=logical_list,
                    safe_line_number=line_no,
                )
            )

    return tuple(reasons)


__all__ = ["ValidationError", "validate_set"]
