"""The singular current-release CP1252/QUOTE_NONE physical-line CSV parser.

Locks D-02/D-03: Python alone decodes CP1252 strictly, rejects the Unicode
replacement character, splits only explicit CRLF/LF/CR physical
terminators, and parses each non-empty physical line independently with
`csv.QUOTE_NONE` so an unescaped quote stays data in one physical record
rather than fusing two rows (the predecessor default-parser defect,
`GATE-A-EVIDENCE.md` Section 4). Header and per-record arity are checked
against the exact contract; non-empty physical data-line count is
reconciled against parsed-record count. Every decoded source field string
is preserved unchanged in the returned `ParsedList` for serialization --
`calico_landing.parquet` must never reinterpret raw bytes.

Every failure crosses this module's boundary as a `StructuralReject`
carrying only a fixed safe reason code plus a logical-list identifier and
an optional safe line number/count -- never the offending row, field
value, or exception text (mirrored from `calico_landing.contracts` and
`calico_landing.result`'s non-echo discipline; consumed later by
`calico_landing.result.AdmissionReason`, which shares this module's exact
reason-code vocabulary).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass

from calico_landing.contracts import CsvContract

#: Split only on the three explicit physical line terminators. No other
#: character is a record boundary -- these files contain no embedded
#: record newlines (D-003; `AGENTS.md` Section 4).
_PHYSICAL_BREAK = re.compile(r"\r\n|\n|\r")

#: The locked D-02 dialect: no special quote processing, no escape
#: character, no leading-space stripping, and `strict=True` so the module
#: fails loudly rather than silently on any dialect misuse. `quotechar`
#: must stay `None` -- `csv.QUOTE_NONE` disables quote interpretation
#: entirely, which is the only way an unescaped quote remains data instead
#: of fusing two physical records.
_CSV_DIALECT_KWARGS: dict[str, object] = {
    "delimiter": ",",
    "quoting": csv.QUOTE_NONE,
    "quotechar": None,
    "escapechar": None,
    "skipinitialspace": False,
    "strict": True,
}


class StructuralReject(Exception):
    """Raised when a payload fails a locked D-02 structural check.

    Carries only a fixed safe `code` (drawn from
    `calico_landing.result.REASON_RANK`), the `logical_list` identifier,
    and optional safe `safe_line_number`/`safe_count` fields -- never the
    offending decoded text, row, or field value.
    """

    def __init__(
        self,
        code: str,
        *,
        logical_list: str,
        safe_line_number: int | None = None,
        safe_count: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.logical_list = logical_list
        self.safe_line_number = safe_line_number
        self.safe_count = safe_count


@dataclass(frozen=True)
class ParsedRecord:
    """One parsed data record: its physical source line and unchanged
    decoded field strings, in contract header order.
    """

    source_line_no: int
    fields: tuple[str, ...]


@dataclass(frozen=True)
class ParsedList:
    """The complete parsed contents of one logical list.

    `records` preserves every decoded source field string unchanged;
    `calico_landing.parquet` serializes exactly these values plus
    structural provenance (`logical_list`, `source_line_no`) and never
    reopens or reinterprets the raw payload.
    """

    logical_list: str
    headers: tuple[str, ...]
    records: tuple[ParsedRecord, ...]


def _read_one_row(text: str, logical_list: str, *, safe_line_number: int) -> list[str]:
    """Parse one physical line under the locked `QUOTE_NONE` dialect.

    A `csv.Error` here is not expected in normal operation because
    `quotechar=None` disables all quote-interpretation logic that
    `strict=True` could otherwise flag; the fallback path exists only so a
    genuinely malformed physical line still fails closed with a safe
    reason instead of an unhandled exception leaking line text.
    """

    try:
        return next(csv.reader([text], **_CSV_DIALECT_KWARGS))
    except csv.Error as exc:
        raise StructuralReject(
            "parse.line_record_mismatch",
            logical_list=logical_list,
            safe_line_number=safe_line_number,
        ) from exc


def parse_payload(payload: bytes, logical_list: str, contract: CsvContract) -> ParsedList:
    """Parse one staged logical-list payload under the locked D-02 contract.

    Decodes strictly with `contract.encoding` (`cp1252`), rejects any
    decoded U+FFFD, splits only explicit CRLF/LF/CR physical terminators,
    requires exact ordered header equality, requires exactly
    `len(contract.headers)` fields per data record, and requires the
    non-empty physical data-line count to equal the parsed-record count.
    Blank physical lines (including one trailing blank line produced by a
    final terminator) are skipped and never counted as data lines or
    records.

    Raises `StructuralReject` with a fixed safe reason on any failure;
    never raises a bare `UnicodeDecodeError` or `csv.Error` to the caller.
    """

    try:
        decoded = payload.decode(contract.encoding, errors="strict")
    except (UnicodeDecodeError, LookupError) as exc:
        raise StructuralReject("parse.decode_failed", logical_list=logical_list) from exc

    if "�" in decoded:
        raise StructuralReject("parse.decode_failed", logical_list=logical_list)

    physical_lines = _PHYSICAL_BREAK.split(decoded)

    if not physical_lines or not physical_lines[0]:
        raise StructuralReject(
            "parse.header_mismatch", logical_list=logical_list, safe_line_number=1
        )

    header_row = _read_one_row(physical_lines[0], logical_list, safe_line_number=1)
    if tuple(header_row) != contract.headers:
        raise StructuralReject(
            "parse.header_mismatch", logical_list=logical_list, safe_line_number=1
        )

    data_lines = [
        (line_no, text) for line_no, text in enumerate(physical_lines[1:], start=2) if text
    ]

    records: list[ParsedRecord] = []
    for line_no, text in data_lines:
        row = _read_one_row(text, logical_list, safe_line_number=line_no)
        if len(row) != len(contract.headers):
            raise StructuralReject(
                "parse.arity_mismatch",
                logical_list=logical_list,
                safe_line_number=line_no,
            )
        records.append(ParsedRecord(source_line_no=line_no, fields=tuple(row)))

    if len(records) != len(data_lines):
        raise StructuralReject(
            "parse.line_record_mismatch",
            logical_list=logical_list,
            safe_count=len(data_lines),
        )

    return ParsedList(logical_list=logical_list, headers=contract.headers, records=tuple(records))
