"""The single fail-closed verifier for staged publication bytes."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path

from calico_publish.allowlist import AGGREGATE_PROHIBITED_COLUMNS, Allowlist
from calico_publish.manifest import ManifestError, validate_published_manifest_document

GATE_CATEGORIES = frozenset(
    {
        "gate.column_set_mismatch",
        "gate.column_order_mismatch",
        "gate.row_arity_mismatch",
        "gate.grain_not_unique",
        "gate.identity_column_in_aggregate",
        "gate.manifest_export_hash_mismatch",
        "gate.manifest_row_count_mismatch",
        "gate.manifest_export_set_mismatch",
        "gate.export_file_missing",
        "gate.unexpected_export_file",
        "gate.export_empty_file",
        "gate.export_invalid_encoding",
        "gate.manifest_invalid_schema",
    }
)


class GateError(Exception):
    """A value-free boundary failure that prevents content verification."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class GateViolation:
    export_name: str
    category: str
    column_name: str | None = None

    def render(self) -> str:
        return f"{self.export_name}:{self.column_name or '-'}: {self.category}"


@dataclass(frozen=True)
class GateResult:
    violations: tuple[GateViolation, ...]

    @property
    def passed(self) -> bool:
        return not self.violations


def _read_csv(path: Path) -> tuple[bytes, list[list[str]]]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise GateError("gate.export_file_missing") from exc
    if not payload:
        raise GateError("gate.export_empty_file")
    if payload.startswith(b"\xef\xbb\xbf") or b"\r" in payload:
        raise GateError("gate.export_invalid_encoding")
    try:
        text = payload.decode("utf-8")
        rows = list(csv.reader(io.StringIO(text, newline="")))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise GateError("gate.export_invalid_encoding") from exc
    if not rows:
        raise GateError("gate.export_empty_file")
    return payload, rows


def _load_manifest(path: Path, allowlist: Allowlist) -> dict[str, object]:
    try:
        document = json.loads(path.read_bytes().decode("utf-8"))
        validate_published_manifest_document(document, allowlist=allowlist)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ManifestError) as exc:
        raise GateError("gate.manifest_invalid_schema") from exc
    return document


def verify(
    staging_dir: str | Path,
    allowlist: Allowlist,
    manifest_path: str | Path | None = None,
) -> GateResult:
    """Verify exact fields, order, grain, encoding, and optional provenance."""

    root = Path(staging_dir)
    export_dir = root / "exports"
    expected_files = {entry.file_name for entry in allowlist.exports}
    actual_files = (
        {path.relative_to(export_dir).as_posix() for path in export_dir.rglob("*") if path.is_file()}
        if export_dir.is_dir()
        else set()
    )
    if actual_files - expected_files:
        raise GateError("gate.unexpected_export_file")

    violations: list[GateViolation] = []
    observed: dict[str, tuple[str, int]] = {}
    for entry in allowlist.exports:
        path = export_dir / entry.file_name
        if not path.is_file():
            raise GateError("gate.export_file_missing")
        payload, rows = _read_csv(path)
        header = tuple(rows[0])
        if set(header) != set(entry.columns):
            violations.append(GateViolation(entry.export_name, "gate.column_set_mismatch"))
        elif header != entry.columns:
            violations.append(GateViolation(entry.export_name, "gate.column_order_mismatch"))

        valid_rows: list[list[str]] = []
        for row in rows[1:]:
            if len(row) != len(entry.columns):
                violations.append(GateViolation(entry.export_name, "gate.row_arity_mismatch"))
            else:
                valid_rows.append(row)
        if header == entry.columns:
            positions = tuple(entry.columns.index(column) for column in entry.grain)
            keys = [tuple(row[position] for position in positions) for row in valid_rows]
            if len(keys) != len(set(keys)):
                violations.append(GateViolation(entry.export_name, "gate.grain_not_unique"))
        if entry.export_class == "aggregate":
            for column in sorted(set(header) & AGGREGATE_PROHIBITED_COLUMNS):
                violations.append(
                    GateViolation(entry.export_name, "gate.identity_column_in_aggregate", column)
                )
        observed[entry.export_name] = (hashlib.sha256(payload).hexdigest(), len(rows) - 1)

    if manifest_path is not None:
        document = _load_manifest(Path(manifest_path), allowlist)
        records = {item["export_name"]: item for item in document["exports"]}
        if set(records) != set(observed):
            violations.append(GateViolation("manifest", "gate.manifest_export_set_mismatch"))
        for export_name in sorted(set(records) & set(observed)):
            sha256, row_count = observed[export_name]
            if records[export_name]["sha256"] != sha256:
                violations.append(
                    GateViolation(export_name, "gate.manifest_export_hash_mismatch")
                )
            if records[export_name]["row_count"] != row_count:
                violations.append(
                    GateViolation(export_name, "gate.manifest_row_count_mismatch")
                )

    return GateResult(
        violations=tuple(
            sorted(
                violations,
                key=lambda item: (item.export_name, item.category, item.column_name or ""),
            )
        )
    )


__all__ = ["GATE_CATEGORIES", "GateError", "GateResult", "GateViolation", "verify"]
