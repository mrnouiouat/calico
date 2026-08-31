"""Versioned, closed-schema loaders for the Calico landing parse contracts.

Establishes the two locked interfaces every later landing component depends
on: the current-release CP1252/QUOTE_NONE CSV contract (D-01/D-02) and the
explicitly deferred legacy XLSX contract (D-14/D-15). Both loaders decode
strict UTF-8 bytes, require an exact closed top-level key set, reject any
unsupported contract version, and return frozen, immutable values.

Every failure crosses this module's boundary as one fixed safe
`ContractError` category -- never the offending bytes, path, or exception
text (mirrored from `tools/privacy_scan/policy.py`'s closed-schema
discipline).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

#: The four approved logical-list identities, in their locked canonical
#: order (02-RESEARCH.md `LOGICAL_ORDER`; D-08 revision-fingerprint order).
#: No other identity, and no other order, may appear in a CSV contract
#: document.
LOGICAL_LIST_ORDER: tuple[str, ...] = (
    "charities-may-operate",
    "charities-not-operating",
    "charities-undetermined-status",
    "charities-may-not-operate",
)

#: Locked D-14/D-15 unsupported-contract reason code. Shared with the
#: reason vocabulary in `calico_landing.result`.
UNSUPPORTED_XLSX_REASON = "contract.unsupported_xlsx"

_SUPPORTED_CSV_CONTRACT_VERSION = 1
_SUPPORTED_XLSX_CONTRACT_VERSION = 1

_REQUIRED_CSV_ENCODING = "cp1252"
_REQUIRED_CSV_QUOTING = "QUOTE_NONE"
_REQUIRED_CANONICAL_EXCHANGE_FORMAT = "parquet"
_REQUIRED_XLSX_STATUS = "deferred"
_REQUIRED_HEADER_COUNT = 11

_CSV_TOP_LEVEL_KEYS = frozenset(
    {
        "contract_version",
        "logical_lists",
        "headers",
        "encoding",
        "quoting",
        "canonical_exchange_format",
        "max_compressed_payload_bytes",
        "max_decompressed_payload_bytes",
        "max_physical_line_bytes",
    }
)

_XLSX_TOP_LEVEL_KEYS = frozenset(
    {
        "contract_version",
        "status",
        "unsupported_reason",
        "known_worksheet",
        "reopening_trigger",
        "reader_dependency_required",
    }
)

_XLSX_KNOWN_WORKSHEET_KEYS = frozenset(
    {"header_row", "blank_status_row_count", "required_quality_flag"}
)

#: Locked closed source-status vocabulary contract (04-01-PLAN.md D-02,
#: D-22). Authored fresh from the deduplicated union of the four baseline
#: `status_vocabulary` arrays -- never the archived paths, counts, or
#: excluded source columns those arrays also carried.
_SUPPORTED_STATUS_CONTRACT_VERSION = 1
_REQUIRED_NONBLANK_STATUS_COUNT = 33
_REQUIRED_DELINQUENT_STATUS_COUNT = 2

_STATUS_TOP_LEVEL_KEYS = frozenset(
    {
        "contract_version",
        "logical_lists",
        "nonblank_status_vocabulary",
        "delinquent_statuses",
    }
)


class ContractError(Exception):
    """Raised when a contract document is missing, malformed, or fails closed.

    Carries only a fixed safe `category`; never the offending value, path,
    or exception text.
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class CsvContract:
    """The closed, versioned current-release CSV parse contract (D-01/D-02)."""

    contract_version: int
    logical_lists: tuple[str, ...]
    headers: tuple[str, ...]
    encoding: str
    quoting: str
    canonical_exchange_format: str
    max_compressed_payload_bytes: int
    max_decompressed_payload_bytes: int
    max_physical_line_bytes: int


@dataclass(frozen=True)
class XlsxKnownWorksheet:
    """The known-but-unimplemented 2019 legacy worksheet shape (D-15)."""

    header_row: int
    blank_status_row_count: int
    required_quality_flag: str


@dataclass(frozen=True)
class StatusContract:
    """The closed, versioned source-status vocabulary contract (D-02/D-22).

    `nonblank_status_vocabulary` is the exact closed 33-value deduplicated
    union of the four baseline `status_vocabulary` arrays; blank status is
    deliberately not a member -- it remains reachable through the existing
    typed-path exclusion behavior and is validated separately. Exactly the
    two locked delinquent statuses appear in `delinquent_statuses`, both of
    which are also members of `nonblank_status_vocabulary`.
    """

    contract_version: int
    logical_lists: tuple[str, ...]
    nonblank_status_vocabulary: frozenset[str]
    delinquent_statuses: tuple[str, ...]


@dataclass(frozen=True)
class XlsxContract:
    """The versioned, explicitly deferred legacy XLSX contract (D-14/D-15)."""

    contract_version: int
    status: str
    unsupported_reason: str
    known_worksheet: XlsxKnownWorksheet
    reopening_trigger: str
    reader_dependency_required: bool


def _read_document(path: str | Path) -> dict:
    contract_path = Path(path)
    try:
        raw_bytes = contract_path.read_bytes()
    except OSError as exc:
        raise ContractError("contract_not_found") from exc

    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError("invalid_contract_encoding") from exc

    try:
        document = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ContractError("invalid_contract_json") from exc

    if not isinstance(document, dict):
        raise ContractError("invalid_contract_schema")
    return document


def _require_int(document: dict, key: str, *, category: str, positive: bool = False) -> int:
    value = document.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContractError(category)
    if positive and value <= 0:
        raise ContractError(category)
    return value


def _require_str(document: dict, key: str, *, category: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise ContractError(category)
    return value


def load_csv_contract(path: str | Path) -> CsvContract:
    """Load and strictly validate the current-release CSV parse contract.

    Fails closed on any missing/unknown field, unsupported version, or
    closed-vocabulary mismatch. Never reflects the offending input in the
    raised `ContractError`.
    """

    document = _read_document(path)

    if set(document.keys()) != _CSV_TOP_LEVEL_KEYS:
        raise ContractError("invalid_csv_contract_schema")

    contract_version = _require_int(
        document, "contract_version", category="invalid_csv_contract_version"
    )
    if contract_version != _SUPPORTED_CSV_CONTRACT_VERSION:
        raise ContractError("unsupported_csv_contract_version")

    raw_logical_lists = document["logical_lists"]
    if not isinstance(raw_logical_lists, list) or not all(
        isinstance(item, str) for item in raw_logical_lists
    ):
        raise ContractError("invalid_logical_lists")
    if tuple(raw_logical_lists) != LOGICAL_LIST_ORDER:
        raise ContractError("invalid_logical_lists")

    raw_headers = document["headers"]
    if not isinstance(raw_headers, list) or not all(
        isinstance(item, str) and item for item in raw_headers
    ):
        raise ContractError("invalid_headers")
    if len(raw_headers) != _REQUIRED_HEADER_COUNT or len(set(raw_headers)) != len(raw_headers):
        raise ContractError("invalid_headers")

    encoding = _require_str(document, "encoding", category="invalid_encoding")
    if encoding != _REQUIRED_CSV_ENCODING:
        raise ContractError("invalid_encoding")

    quoting = _require_str(document, "quoting", category="invalid_quoting")
    if quoting != _REQUIRED_CSV_QUOTING:
        raise ContractError("invalid_quoting")

    canonical_exchange_format = _require_str(
        document, "canonical_exchange_format", category="invalid_canonical_exchange_format"
    )
    if canonical_exchange_format != _REQUIRED_CANONICAL_EXCHANGE_FORMAT:
        raise ContractError("invalid_canonical_exchange_format")

    max_compressed_payload_bytes = _require_int(
        document,
        "max_compressed_payload_bytes",
        category="invalid_payload_ceiling",
        positive=True,
    )
    max_decompressed_payload_bytes = _require_int(
        document,
        "max_decompressed_payload_bytes",
        category="invalid_payload_ceiling",
        positive=True,
    )
    max_physical_line_bytes = _require_int(
        document, "max_physical_line_bytes", category="invalid_payload_ceiling", positive=True
    )

    return CsvContract(
        contract_version=contract_version,
        logical_lists=tuple(raw_logical_lists),
        headers=tuple(raw_headers),
        encoding=encoding,
        quoting=quoting,
        canonical_exchange_format=canonical_exchange_format,
        max_compressed_payload_bytes=max_compressed_payload_bytes,
        max_decompressed_payload_bytes=max_decompressed_payload_bytes,
        max_physical_line_bytes=max_physical_line_bytes,
    )


def load_status_contract(path: str | Path) -> StatusContract:
    """Load and strictly validate the closed source-status vocabulary contract.

    Fails closed on any missing/unknown field, unsupported version, wrong
    logical-list order, non-unique or non-33-count nonblank vocabulary, or
    a `delinquent_statuses` set that is not exactly the two locked values
    and a subset of the nonblank vocabulary. Never reflects the offending
    input in the raised `ContractError` (D-05/D-10 non-echo discipline
    mirrored from `load_csv_contract`).
    """

    document = _read_document(path)

    if set(document.keys()) != _STATUS_TOP_LEVEL_KEYS:
        raise ContractError("invalid_status_contract_schema")

    contract_version = _require_int(
        document, "contract_version", category="invalid_status_contract_version"
    )
    if contract_version != _SUPPORTED_STATUS_CONTRACT_VERSION:
        raise ContractError("unsupported_status_contract_version")

    raw_logical_lists = document["logical_lists"]
    if not isinstance(raw_logical_lists, list) or not all(
        isinstance(item, str) for item in raw_logical_lists
    ):
        raise ContractError("invalid_status_logical_lists")
    if tuple(raw_logical_lists) != LOGICAL_LIST_ORDER:
        raise ContractError("invalid_status_logical_lists")

    raw_vocabulary = document["nonblank_status_vocabulary"]
    if not isinstance(raw_vocabulary, list) or not all(
        isinstance(item, str) and item for item in raw_vocabulary
    ):
        raise ContractError("invalid_status_vocabulary")
    if (
        len(raw_vocabulary) != _REQUIRED_NONBLANK_STATUS_COUNT
        or len(set(raw_vocabulary)) != _REQUIRED_NONBLANK_STATUS_COUNT
    ):
        raise ContractError("invalid_status_vocabulary")
    nonblank_status_vocabulary = frozenset(raw_vocabulary)

    raw_delinquent = document["delinquent_statuses"]
    if not isinstance(raw_delinquent, list) or not all(
        isinstance(item, str) and item for item in raw_delinquent
    ):
        raise ContractError("invalid_delinquent_statuses")
    if (
        len(raw_delinquent) != _REQUIRED_DELINQUENT_STATUS_COUNT
        or len(set(raw_delinquent)) != _REQUIRED_DELINQUENT_STATUS_COUNT
    ):
        raise ContractError("invalid_delinquent_statuses")
    if not set(raw_delinquent).issubset(nonblank_status_vocabulary):
        raise ContractError("invalid_delinquent_statuses")

    return StatusContract(
        contract_version=contract_version,
        logical_lists=tuple(raw_logical_lists),
        nonblank_status_vocabulary=nonblank_status_vocabulary,
        delinquent_statuses=tuple(raw_delinquent),
    )


def _parse_known_worksheet(raw: object) -> XlsxKnownWorksheet:
    if not isinstance(raw, dict) or set(raw.keys()) != _XLSX_KNOWN_WORKSHEET_KEYS:
        raise ContractError("invalid_known_worksheet")

    header_row = raw["header_row"]
    if not isinstance(header_row, int) or isinstance(header_row, bool) or header_row <= 0:
        raise ContractError("invalid_known_worksheet")

    blank_status_row_count = raw["blank_status_row_count"]
    if (
        not isinstance(blank_status_row_count, int)
        or isinstance(blank_status_row_count, bool)
        or blank_status_row_count < 0
    ):
        raise ContractError("invalid_known_worksheet")

    required_quality_flag = raw["required_quality_flag"]
    if not isinstance(required_quality_flag, str) or not required_quality_flag:
        raise ContractError("invalid_known_worksheet")

    return XlsxKnownWorksheet(
        header_row=header_row,
        blank_status_row_count=blank_status_row_count,
        required_quality_flag=required_quality_flag,
    )


def load_xlsx_contract(path: str | Path) -> XlsxContract:
    """Load and strictly validate the deferred legacy XLSX contract.

    Encodes D-14/D-15: a versioned extension point that fails closed with
    `contract.unsupported_xlsx` and declares no reader dependency. This
    loader never imports an XLSX reader and never will for a `deferred`
    document.
    """

    document = _read_document(path)

    if set(document.keys()) != _XLSX_TOP_LEVEL_KEYS:
        raise ContractError("invalid_xlsx_contract_schema")

    contract_version = _require_int(
        document, "contract_version", category="invalid_xlsx_contract_version"
    )
    if contract_version != _SUPPORTED_XLSX_CONTRACT_VERSION:
        raise ContractError("unsupported_xlsx_contract_version")

    status = _require_str(document, "status", category="invalid_xlsx_status")
    if status != _REQUIRED_XLSX_STATUS:
        raise ContractError("invalid_xlsx_status")

    unsupported_reason = _require_str(
        document, "unsupported_reason", category="invalid_unsupported_reason"
    )
    if unsupported_reason != UNSUPPORTED_XLSX_REASON:
        raise ContractError("invalid_unsupported_reason")

    known_worksheet = _parse_known_worksheet(document.get("known_worksheet"))

    reopening_trigger = _require_str(
        document, "reopening_trigger", category="invalid_reopening_trigger"
    )

    reader_dependency_required = document.get("reader_dependency_required")
    if not isinstance(reader_dependency_required, bool):
        raise ContractError("invalid_reader_dependency_flag")
    if reader_dependency_required is not False:
        # A `deferred` document may never declare a reader dependency; this
        # loader adds no XLSX reader import regardless (D-15).
        raise ContractError("invalid_reader_dependency_flag")

    return XlsxContract(
        contract_version=contract_version,
        status=status,
        unsupported_reason=unsupported_reason,
        known_worksheet=known_worksheet,
        reopening_trigger=reopening_trigger,
        reader_dependency_required=reader_dependency_required,
    )
