"""Closed publication-export allowlist loading and structural validation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

EXPORT_CLASSES = frozenset({"aggregate", "named_history"})
AGGREGATE_PROHIBITED_COLUMNS = frozenset(
    {"state_charity_registration_number", "organization_name", "city", "state"}
)
CSV_DIALECT_NAME = "calico-csv-v1"

_TOP_LEVEL_KEYS = frozenset({"schema_version", "allowlist_version", "exports"})
_ENTRY_KEYS = frozenset(
    {
        "export_name",
        "export_class",
        "source_relation",
        "columns",
        "grain",
        "measures",
        "file_name",
        "dialect",
    }
)
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_FILE_NAME = re.compile(r"^[a-z][a-z0-9_]*\.csv$")


class AllowlistError(Exception):
    """A value-free failure at the publication allowlist boundary."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class ExportEntry:
    export_name: str
    export_class: str
    source_relation: str
    columns: tuple[str, ...]
    grain: tuple[str, ...]
    measures: tuple[str, ...]
    file_name: str
    dialect: str


@dataclass(frozen=True)
class Allowlist:
    schema_version: int
    allowlist_version: str
    exports: tuple[ExportEntry, ...]


def _is_string_array(value: object, *, nonempty: bool) -> bool:
    return (
        isinstance(value, list)
        and (bool(value) or not nonempty)
        and all(isinstance(item, str) and _IDENTIFIER.fullmatch(item) for item in value)
        and len(value) == len(set(value))
    )


def _parse_entry(document: object) -> ExportEntry:
    if not isinstance(document, dict) or set(document) != _ENTRY_KEYS:
        raise AllowlistError("allowlist.invalid_schema")
    export_name = document.get("export_name")
    export_class = document.get("export_class")
    source_relation = document.get("source_relation")
    columns = document.get("columns")
    grain = document.get("grain")
    measures = document.get("measures")
    file_name = document.get("file_name")
    dialect = document.get("dialect")
    if not (
        isinstance(export_name, str)
        and _IDENTIFIER.fullmatch(export_name)
        and isinstance(export_class, str)
        and export_class in EXPORT_CLASSES
        and isinstance(source_relation, str)
        and _IDENTIFIER.fullmatch(source_relation)
        and _is_string_array(columns, nonempty=True)
        and _is_string_array(grain, nonempty=True)
        and _is_string_array(measures, nonempty=False)
        and isinstance(file_name, str)
        and _FILE_NAME.fullmatch(file_name)
        and dialect == CSV_DIALECT_NAME
    ):
        raise AllowlistError("allowlist.invalid_schema")
    return ExportEntry(
        export_name=export_name,
        export_class=export_class,
        source_relation=source_relation,
        columns=tuple(columns),
        grain=tuple(grain),
        measures=tuple(measures),
        file_name=file_name,
        dialect=dialect,
    )


def _has_duplicate(entries: list[ExportEntry], field: str) -> bool:
    values = [getattr(entry, field) for entry in entries]
    return len(values) != len(set(values))


def load_allowlist(path: str | Path) -> Allowlist:
    """Load the one closed positive publication authority, failing closed."""

    try:
        payload = Path(path).read_bytes()
    except OSError as exc:
        raise AllowlistError("allowlist.not_found") from exc
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AllowlistError("allowlist.invalid_encoding") from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AllowlistError("allowlist.invalid_json") from exc

    if (
        not isinstance(document, dict)
        or set(document) != _TOP_LEVEL_KEYS
        or isinstance(document.get("schema_version"), bool)
        or document.get("schema_version") != 1
        or document.get("allowlist_version") != "publication-exports-v1"
        or not isinstance(document.get("exports"), list)
        or not document["exports"]
    ):
        raise AllowlistError("allowlist.invalid_schema")
    entries = [_parse_entry(item) for item in document["exports"]]

    for field, category in (
        ("export_name", "allowlist.duplicate_export_name"),
        ("file_name", "allowlist.duplicate_file_name"),
        ("source_relation", "allowlist.duplicate_source_relation"),
    ):
        if _has_duplicate(entries, field):
            raise AllowlistError(category)

    for entry in entries:
        if not set(entry.grain).issubset(entry.columns):
            raise AllowlistError("allowlist.grain_not_in_columns")
        if entry.export_class == "aggregate":
            if set(entry.columns) & AGGREGATE_PROHIBITED_COLUMNS:
                raise AllowlistError("allowlist.aggregate_identity_column")
            if set(entry.columns) != set(entry.grain) | set(entry.measures):
                raise AllowlistError("allowlist.aggregate_column_not_grain_or_measure")
        elif entry.measures:
            raise AllowlistError("allowlist.measures_not_allowed_for_class")

    return Allowlist(
        schema_version=1,
        allowlist_version="publication-exports-v1",
        exports=tuple(sorted(entries, key=lambda entry: entry.export_name)),
    )


__all__ = [
    "AGGREGATE_PROHIBITED_COLUMNS",
    "CSV_DIALECT_NAME",
    "EXPORT_CLASSES",
    "Allowlist",
    "AllowlistError",
    "ExportEntry",
    "load_allowlist",
]
