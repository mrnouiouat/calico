"""Deterministically serialize allowlisted DuckDB relations as UTF-8 CSV."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import stat
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


def _is_link_or_reparse(stat_result: os.stat_result) -> bool:
    if stat.S_ISLNK(stat_result.st_mode):
        return True
    attributes = getattr(stat_result, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def prepare_staging_directory(path: str | Path) -> Path:
    """Create one directory without traversing an existing link component."""

    try:
        absolute = Path(os.path.abspath(Path(path)))
        parts = absolute.parts
        current = Path(parts[0])
        for part in parts[1:]:
            current = current / part
            try:
                status = os.lstat(current)
            except FileNotFoundError:
                current.mkdir()
                status = os.lstat(current)
            if _is_link_or_reparse(status) or not stat.S_ISDIR(status.st_mode):
                raise ExportError("export.invalid_staging")
        resolved = absolute.resolve(strict=True)
    except ExportError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise ExportError("export.invalid_staging") from exc
    if not resolved.is_dir():
        raise ExportError("export.invalid_staging")
    return resolved


def prepare_staging_subdirectory(root: Path, name: str) -> Path:
    if name not in {_EXPORT_SUBDIR, "manifest"}:
        raise ExportError("export.invalid_staging")
    resolved_root = prepare_staging_directory(root)
    child = prepare_staging_directory(resolved_root / name)
    if not child.is_relative_to(resolved_root) or child.parent != resolved_root:
        raise ExportError("export.invalid_staging")
    return child


def _open_binary_no_follow(path: Path, flags: int):
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags | no_follow)
    return os.fdopen(descriptor, "rb" if flags == os.O_RDONLY else "wb")


def write_staged_text(path: Path, text: str) -> None:
    """Create a new UTF-8 staging file without following a final symlink."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        with _open_binary_no_follow(path, flags) as handle:
            handle.write(text.encode("utf-8"))
    except OSError as exc:
        raise ExportError("export.invalid_staging") from exc


def _identifier(value: str) -> str:
    return f'"{value}"'


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with _open_binary_no_follow(path, os.O_RDONLY) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _varchar_columns(connection: duckdb.DuckDBPyConnection, entry: ExportEntry) -> tuple[str, ...]:
    query = "SELECT " + ", ".join(_identifier(column) for column in entry.columns)
    query += " FROM " + _identifier(entry.source_relation) + " LIMIT 0"
    try:
        description = connection.execute("DESCRIBE " + query).fetchall()
    except duckdb.BinderException as exc:
        raise ExportError("export.header_mismatch", entry.export_name) from exc
    except duckdb.Error as exc:
        raise ExportError("export.copy_failed", entry.export_name) from exc
    types = {str(row[0]): str(row[1]).upper() for row in description}
    return tuple(column for column in entry.columns if types.get(column) == "VARCHAR")


def _copy_statement(entry: ExportEntry, destination: Path, varchar_columns: tuple[str, ...]) -> str:
    select_list = ", ".join(_identifier(column) for column in entry.columns)
    order_list = ", ".join(_identifier(column) + " ASC NULLS LAST" for column in entry.grain)
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
        with _open_binary_no_follow(path, os.O_RDONLY) as handle:
            payload = handle.read()
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
    try:
        rows = csv.reader(io.StringIO(text, newline=""), strict=True)
        if tuple(next(rows, ())) != entry.columns:
            raise ExportError("export.header_mismatch", entry.export_name)
        row_count = 0
        for row in rows:
            if len(row) != len(entry.columns):
                raise ExportError("export.row_arity_mismatch", entry.export_name)
            row_count += 1
        return _hash_file(path), row_count
    except (OSError, csv.Error) as exc:
        raise ExportError("export.readback_failed", entry.export_name) from exc


def _normalize_header(path: Path, entry: ExportEntry) -> None:
    """Keep the fixed public header unquoted when DuckDB FORCE_QUOTE quotes it.

    DuckDB 1.5.5 applies FORCE_QUOTE to matching header fields as well as
    data fields. Replacing only that allowlist-derived header preserves
    DuckDB's byte rendering for every data value while keeping the public
    dialect's exact stable header line.
    """

    try:
        with _open_binary_no_follow(path, os.O_RDONLY) as handle:
            payload = handle.read()
        header, separator, body = payload.partition(b"\n")
        parsed = next(csv.reader(io.StringIO(header.decode("utf-8"))))
        if not separator or tuple(parsed) != entry.columns:
            raise ExportError("export.header_mismatch", entry.export_name)
        with _open_binary_no_follow(path, os.O_WRONLY | os.O_TRUNC) as handle:
            handle.write(",".join(entry.columns).encode("ascii") + b"\n" + body)
    except ExportError:
        raise
    except (OSError, UnicodeDecodeError, csv.Error, StopIteration) as exc:
        raise ExportError("export.readback_failed", entry.export_name) from exc


def export_all(
    duckdb_path: str | Path, allowlist: Allowlist, staging_dir: str | Path
) -> tuple[StagedExport, ...]:
    """Export every allowlisted relation through one fixed, inert COPY path."""

    root = prepare_staging_directory(staging_dir)
    try:
        export_dir = prepare_staging_subdirectory(root, _EXPORT_SUBDIR)
        if export_dir.exists() and any(export_dir.iterdir()):
            raise ExportError("export.staging_not_empty")
        connection = duckdb.connect(str(duckdb_path), read_only=True)
    except (OSError, duckdb.Error) as exc:
        raise ExportError("export.copy_failed") from exc

    staged: list[StagedExport] = []
    written: list[Path] = []
    try:
        entries = sorted(allowlist.exports, key=lambda entry: entry.export_name)
        # Resolve every projection before writing the first file, so a late
        # missing column cannot leave a superficially successful partial set.
        projections = [(entry, _varchar_columns(connection, entry)) for entry in entries]
        for entry, varchar_columns in projections:
            destination = export_dir / entry.file_name
            written.append(destination)
            try:
                connection.execute(_copy_statement(entry, destination, varchar_columns))
            except ExportError:
                raise
            except duckdb.Error as exc:
                raise ExportError("export.copy_failed", entry.export_name) from exc
            try:
                destination_status = os.lstat(destination)
                resolved_destination = destination.resolve(strict=True)
            except OSError as exc:
                raise ExportError("export.copy_failed", entry.export_name) from exc
            if (
                _is_link_or_reparse(destination_status)
                or not stat.S_ISREG(destination_status.st_mode)
                or not resolved_destination.is_relative_to(export_dir)
                or resolved_destination.parent != export_dir
            ):
                raise ExportError("export.invalid_staging", entry.export_name)
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
    except ExportError:
        for path in written:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # The original safe failure still blocks publication; a future
                # attempt refuses the occupied directory if cleanup is denied.
                pass
        raise
    finally:
        connection.close()
    return tuple(staged)


__all__ = [
    "CSV_DIALECT_NAME",
    "ExportError",
    "StagedExport",
    "export_all",
    "prepare_staging_directory",
    "prepare_staging_subdirectory",
    "write_staged_text",
]
