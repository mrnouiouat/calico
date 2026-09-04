"""Deterministically serialize allowlisted DuckDB relations as UTF-8 CSV."""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

import duckdb

from calico_publish.allowlist import Allowlist, CSV_DIALECT_NAME, ExportEntry

_EXPORT_SUBDIR = "exports"
_COPY_OPTIONS = (
    "FORMAT CSV, HEADER, DELIMITER ',', QUOTE '\"', ESCAPE '\"', "
    "DATEFORMAT '%Y-%m-%d', TIMESTAMPFORMAT '%Y-%m-%dT%H:%M:%S', "
    "NULLSTR '', NEW_LINE '\\n', COMPRESSION 'none'"
)


class ExportError(Exception):
    """A value-free deterministic-export failure."""

    def __init__(self, category: str, export_name: str | None = None):
        super().__init__(category)
        self.category = category
        self.export_name = export_name


@dataclass(frozen=True)
class StagedExport:
    export_name: str
    file_name: str
    relative_path: str
    sha256: str
    row_count: int


def _identifier(value: str) -> str:
    return f'"{value}"'


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _varchar_columns(connection: duckdb.DuckDBPyConnection, entry: ExportEntry) -> tuple[str, ...]:
    query = "SELECT " + ", ".join(_identifier(column) for column in entry.columns)
    query += " FROM " + _identifier(entry.source_relation) + " LIMIT 0"
    try:
        description = connection.execute("DESCRIBE " + query).fetchall()
    except duckdb.Error as exc:
        raise ExportError("export.copy_failed", entry.export_name) from exc
    types = {str(row[0]): str(row[1]).upper() for row in description}
    return tuple(column for column in entry.columns if types.get(column) == "VARCHAR")


def _copy_statement(entry: ExportEntry, destination: Path, varchar_columns: tuple[str, ...]) -> str:
    select_list = ", ".join(_identifier(column) for column in entry.columns)
    order_list = ", ".join(_identifier(column) + " ASC" for column in entry.grain)
    options = _COPY_OPTIONS
    if varchar_columns:
        options += ", FORCE_QUOTE (" + ", ".join(_identifier(column) for column in varchar_columns) + ")"
    return (
        "COPY (SELECT "
        + select_list
        + " FROM "
        + _identifier(entry.source_relation)
        + " ORDER BY "
        + order_list
        + ") TO "
        + _sql_string(destination.as_posix())
        + " ("
        + options
        + ")"
    )


def _readback(path: Path, entry: ExportEntry) -> tuple[str, int]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ExportError("export.readback_failed", entry.export_name) from exc
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExportError("export.invalid_encoding", entry.export_name) from exc
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ExportError("export.byte_order_mark_present", entry.export_name)
    if b"\r" in payload:
        raise ExportError("export.carriage_return_present", entry.export_name)
    lines = text.splitlines()
    if not lines or tuple(lines[0].split(",")) != entry.columns:
        raise ExportError("export.header_mismatch", entry.export_name)
    return _hash_file(path), len(lines) - 1


def _normalize_header(path: Path, entry: ExportEntry) -> None:
    """Keep the fixed public header unquoted when DuckDB FORCE_QUOTE quotes it.

    DuckDB 1.5.5 applies FORCE_QUOTE to matching header fields as well as
    data fields. Replacing only that allowlist-derived header preserves
    DuckDB's byte rendering for every data value while keeping the public
    dialect's exact stable header line.
    """

    try:
        payload = path.read_bytes()
        header, separator, body = payload.partition(b"\n")
        parsed = next(csv.reader(io.StringIO(header.decode("utf-8"))))
        if not separator or tuple(parsed) != entry.columns:
            raise ExportError("export.header_mismatch", entry.export_name)
        path.write_bytes(",".join(entry.columns).encode("ascii") + b"\n" + body)
    except ExportError:
        raise
    except (OSError, UnicodeDecodeError, csv.Error, StopIteration) as exc:
        raise ExportError("export.readback_failed", entry.export_name) from exc


def export_all(
    duckdb_path: str | Path, allowlist: Allowlist, staging_dir: str | Path
) -> tuple[StagedExport, ...]:
    """Export every allowlisted relation through one fixed, inert COPY path."""

    root = Path(staging_dir)
    if root.exists() and any(root.iterdir()):
        raise ExportError("export.staging_not_empty")
    export_dir = root / _EXPORT_SUBDIR
    try:
        export_dir.mkdir(parents=True, exist_ok=True)
        connection = duckdb.connect(str(duckdb_path), read_only=True)
    except (OSError, duckdb.Error) as exc:
        raise ExportError("export.copy_failed") from exc

    staged: list[StagedExport] = []
    try:
        for entry in allowlist.exports:
            destination = export_dir / entry.file_name
            try:
                varchar_columns = _varchar_columns(connection, entry)
                connection.execute(_copy_statement(entry, destination, varchar_columns))
            except ExportError:
                raise
            except duckdb.Error as exc:
                raise ExportError("export.copy_failed", entry.export_name) from exc
            _normalize_header(destination, entry)
            sha256, row_count = _readback(destination, entry)
            staged.append(
                StagedExport(
                    export_name=entry.export_name,
                    file_name=entry.file_name,
                    relative_path=f"{_EXPORT_SUBDIR}/{entry.file_name}",
                    sha256=sha256,
                    row_count=row_count,
                )
            )
    finally:
        connection.close()
    return tuple(staged)


__all__ = ["CSV_DIALECT_NAME", "ExportError", "StagedExport", "export_all"]
