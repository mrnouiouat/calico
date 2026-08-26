"""Deterministic Parquet serialization from already-parsed values (D-01/D-02).

Locks Pattern 2 of `02-RESEARCH.md`: already-parsed `ParsedList` field
strings are written to a generated UTF-8 newline-delimited JSON file inside
a caller-owned staging directory, then read into a DuckDB relation through
an explicit all-`VARCHAR`/`BIGINT` schema and written as Zstandard Parquet.
DuckDB never opens the raw payload -- it only ever sees the temporary
NDJSON this module generates from already-parsed Python values, so
serialization cannot silently reinterpret raw source bytes under a second
dialect (the exact failure mode D-02 forbids).

After writing, this module reopens only the Parquet file to prove exact
column order/types, row count, source-line ordering, and the absence of
any U+FFFD before returning safe artifact metadata. The generated NDJSON
is always removed -- on success and on failure -- before control returns
to the caller, so a caller (Plan 04's store) never has to reason about a
leftover row-bearing intermediate. Nothing this module returns or raises
ever carries a row value; only counts, hashes, and writer/schema settings
(mirrored from `calico_landing.parser`'s non-echo `StructuralReject`).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import duckdb

from calico_landing.contracts import CsvContract
from calico_landing.parser import ParsedList

#: Locked Zstandard compression setting (Pattern 2).
_COMPRESSION = "zstd"

#: Locked fixed row-group size (Pattern 2; ~120K default DuckDB target).
_ROW_GROUP_SIZE = 122_880

#: Structural provenance columns appended after every contract source
#: header, in this fixed order. `source_list` is the logical-list identity;
#: `source_line_no` is the physical source line the record came from.
_PROVENANCE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("source_list", "VARCHAR"),
    ("source_line_no", "BIGINT"),
)

_READ_CHUNK_BYTES = 1024 * 1024


class CanonicalSerializationError(Exception):
    """Raised when Parquet serialization or its round-trip proof fails.

    Carries only the fixed safe `code` `"canonical.serialization_failed"`,
    a `logical_list` identifier, and an optional safe `safe_count` -- never
    a row value, field value, DuckDB exception message, or path.
    """

    def __init__(self, code: str, *, logical_list: str, safe_count: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.logical_list = logical_list
        self.safe_count = safe_count


@dataclass(frozen=True)
class ParquetArtifact:
    """Safe metadata describing one written, round-trip-verified Parquet file.

    Never carries a row or field value -- only counts, a content hash, and
    the fixed schema/writer settings used to produce the file.
    """

    logical_list: str
    row_count: int
    sha256: str
    schema_columns: tuple[str, ...]
    schema_types: tuple[str, ...]
    writer_version: str
    compression: str
    row_group_size: int


def _schema_columns(contract: CsvContract) -> dict[str, str]:
    columns: dict[str, str] = {name: "VARCHAR" for name in contract.headers}
    for name, sql_type in _PROVENANCE_COLUMNS:
        columns[name] = sql_type
    return columns


def _write_ndjson(parsed: ParsedList, ndjson_path: Path) -> None:
    with open(ndjson_path, "w", encoding="utf-8", newline="\n") as handle:
        for record in parsed.records:
            row: dict[str, object] = dict(zip(parsed.headers, record.fields, strict=True))
            row["source_list"] = parsed.logical_list
            row["source_line_no"] = record.source_line_no
            json.dump(row, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        handle.flush()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_round_trip(
    destination_path: Path,
    parsed: ParsedList,
    columns: dict[str, str],
) -> tuple[int, tuple[str, ...], tuple[str, ...]]:
    expected_columns = tuple(columns.keys())
    expected_types = tuple(columns.values())

    try:
        with duckdb.connect(":memory:") as connection:
            relation = connection.read_parquet(str(destination_path))
            actual_columns = tuple(relation.columns)
            actual_types = tuple(str(sql_type) for sql_type in relation.types)
            rows = relation.fetchall()
    except duckdb.Error as exc:
        raise CanonicalSerializationError(
            "canonical.serialization_failed", logical_list=parsed.logical_list
        ) from exc

    if actual_columns != expected_columns or actual_types != expected_types:
        raise CanonicalSerializationError(
            "canonical.serialization_failed", logical_list=parsed.logical_list
        )

    if len(rows) != len(parsed.records):
        raise CanonicalSerializationError(
            "canonical.serialization_failed",
            logical_list=parsed.logical_list,
            safe_count=len(rows),
        )

    line_no_index = expected_columns.index("source_line_no")
    expected_line_numbers = tuple(
        sorted(record.source_line_no for record in parsed.records)
    )
    actual_line_numbers = tuple(row[line_no_index] for row in rows)
    if actual_line_numbers != expected_line_numbers:
        raise CanonicalSerializationError(
            "canonical.serialization_failed", logical_list=parsed.logical_list
        )

    for row in rows:
        for value in row:
            if isinstance(value, str) and "�" in value:
                raise CanonicalSerializationError(
                    "canonical.serialization_failed", logical_list=parsed.logical_list
                )

    return len(rows), actual_columns, actual_types


def write_parquet(
    parsed: ParsedList,
    staging_dir: str | Path,
    destination_path: str | Path,
    contract: CsvContract,
) -> ParquetArtifact:
    """Serialize `parsed` to deterministic Zstandard Parquet at `destination_path`.

    `staging_dir` is a caller-owned directory (inside the caller's staging
    tree) used only to hold the temporary generated NDJSON; it is removed
    unconditionally before this function returns or raises. `destination_path`
    must not already exist -- this function never overwrites a prior
    artifact (D-07: only a complete revision directory rename may publish a
    canonical file; this module never republishes in place).

    Raises `CanonicalSerializationError` on any schema mismatch, DuckDB
    failure, or failed round-trip proof (column order/type, row count,
    source-line ordering, or a decoded U+FFFD in the written Parquet).
    """

    staging_dir = Path(staging_dir)
    destination_path = Path(destination_path)

    if tuple(parsed.headers) != tuple(contract.headers):
        raise CanonicalSerializationError(
            "canonical.serialization_failed", logical_list=parsed.logical_list
        )

    if destination_path.exists():
        raise CanonicalSerializationError(
            "canonical.serialization_failed", logical_list=parsed.logical_list
        )

    columns = _schema_columns(contract)

    staging_dir.mkdir(parents=True, exist_ok=True)
    ndjson_path = staging_dir / f"{parsed.logical_list}.ndjson"

    try:
        _write_ndjson(parsed, ndjson_path)

        destination_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with duckdb.connect(":memory:") as connection:
                connection.execute("SET threads = 1")
                relation = connection.read_json(
                    str(ndjson_path), columns=columns, format="newline_delimited"
                ).order("source_line_no")
                relation.write_parquet(
                    str(destination_path),
                    compression=_COMPRESSION,
                    row_group_size=_ROW_GROUP_SIZE,
                    overwrite=False,
                )
        except duckdb.Error as exc:
            raise CanonicalSerializationError(
                "canonical.serialization_failed", logical_list=parsed.logical_list
            ) from exc
    finally:
        ndjson_path.unlink(missing_ok=True)

    row_count, schema_columns, schema_types = _verify_round_trip(
        destination_path, parsed, columns
    )

    return ParquetArtifact(
        logical_list=parsed.logical_list,
        row_count=row_count,
        sha256=_hash_file(destination_path),
        schema_columns=schema_columns,
        schema_types=schema_types,
        writer_version=f"duckdb-{duckdb.__version__}",
        compression=_COMPRESSION,
        row_group_size=_ROW_GROUP_SIZE,
    )
