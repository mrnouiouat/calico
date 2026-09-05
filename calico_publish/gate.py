"""The single fail-closed verifier for staged publication bytes."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

from calico_landing.contracts import LOGICAL_LIST_ORDER
from calico_publish.allowlist import AGGREGATE_PROHIBITED_COLUMNS, Allowlist
from calico_publish.manifest import ManifestError, validate_published_manifest_document

GATE_VIOLATION_CATEGORIES = frozenset(
    {
        "gate.column_set_mismatch",
        "gate.column_order_mismatch",
        "gate.row_arity_mismatch",
        "gate.grain_not_unique",
        "gate.identity_column_in_aggregate",
        "gate.manifest_export_hash_mismatch",
        "gate.manifest_row_count_mismatch",
        "gate.manifest_export_set_mismatch",
        "gate.manifest_eligible_key_count_mismatch",
    }
)
GATE_ERROR_CATEGORIES = frozenset(
    {
        "gate.export_file_missing",
        "gate.unexpected_export_file",
        "gate.export_empty_file",
        "gate.export_invalid_encoding",
        "gate.export_byte_order_mark",
        "gate.export_carriage_return",
        "gate.manifest_invalid_schema",
        "gate.manifest_not_found",
    }
)

_SAFE_LOCATOR = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class GateError(Exception):
    """A value-free boundary failure that prevents content verification."""

    def __init__(self, category: str):
        if category not in GATE_ERROR_CATEGORIES:
            raise ValueError("unknown gate error category")
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class GateViolation:
    export_name: str
    category: str
    column_name: str | None = None

    def render(self) -> str:
        """Render only closed categories and bounded identifier locators."""

        export = self.export_name if _SAFE_LOCATOR.fullmatch(self.export_name) else "-"
        column = (
            self.column_name
            if self.column_name is not None and _SAFE_LOCATOR.fullmatch(self.column_name)
            else "-"
        )
        category = self.category if self.category in GATE_VIOLATION_CATEGORIES else "gate.violation"
        return f"{export}:{column}: {category}"


@dataclass(frozen=True)
class GateResult:
    violations: tuple[GateViolation, ...]

    @property
    def passed(self) -> bool:
        return not self.violations


def _read_csv(path: Path) -> tuple[bytes, list[list[str]]]:
    if path.is_symlink() or not path.is_file():
        raise GateError("gate.export_file_missing")
    try:
        with path.open("rb") as handle:
            payload = handle.read()
    except OSError as exc:
        raise GateError("gate.export_file_missing") from exc
    if not payload:
        raise GateError("gate.export_empty_file")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise GateError("gate.export_byte_order_mark")
    if b"\r" in payload:
        raise GateError("gate.export_carriage_return")
    try:
        text = payload.decode("utf-8")
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise GateError("gate.export_invalid_encoding") from exc
    if not rows or not rows[0]:
        raise GateError("gate.export_empty_file")
    return payload, rows


def _load_manifest_payload(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.exists():
        category = "gate.manifest_invalid_schema" if path.is_symlink() else "gate.manifest_not_found"
        raise GateError(category)
    if not path.is_file():
        raise GateError("gate.manifest_invalid_schema")
    try:
        with path.open("rb") as handle:
            payload = handle.read()
        document = json.loads(payload.decode("utf-8"))
    except OSError as exc:
        raise GateError("gate.manifest_not_found") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateError("gate.manifest_invalid_schema") from exc
    if not isinstance(document, dict):
        raise GateError("gate.manifest_invalid_schema")
    return document


def _validate_manifest(
    document: dict[str, object],
    allowlist: Allowlist,
    observed: dict[str, tuple[str, int]],
    source_lists: tuple[str, ...],
) -> bool:
    """Validate identities and return whether a known export is omitted."""

    exports = document.get("exports")
    expected = {entry.export_name: entry for entry in allowlist.exports}
    if isinstance(exports, list) and all(isinstance(item, dict) for item in exports):
        names = [item.get("export_name") for item in exports]
        if (
            all(isinstance(name, str) for name in names)
            and len(names) == len(set(names))
            and names == sorted(names)
            and set(names).issubset(expected)
            and set(names) != set(expected)
        ):
            padded = dict(document)
            padded_exports = list(exports)
            for name in sorted(set(expected) - set(names)):
                sha256, row_count = observed[name]
                entry = expected[name]
                padded_exports.append(
                    {
                        "export_name": name,
                        "file_name": entry.file_name,
                        "sha256": sha256,
                        "row_count": row_count,
                        "grain": list(entry.grain),
                    }
                )
            padded["exports"] = sorted(padded_exports, key=lambda item: item["export_name"])
            try:
                validate_published_manifest_document(
                    padded, allowlist=allowlist, source_lists=source_lists
                )
            except ManifestError as exc:
                raise GateError("gate.manifest_invalid_schema") from exc
            return True
    try:
        validate_published_manifest_document(
            document, allowlist=allowlist, source_lists=source_lists
        )
    except ManifestError as exc:
        raise GateError("gate.manifest_invalid_schema") from exc
    return False


def _check_export_directory(export_dir: Path, expected_files: set[str]) -> None:
    if export_dir.is_symlink() or not export_dir.is_dir():
        raise GateError("gate.export_file_missing")
    try:
        entries = list(export_dir.iterdir())
    except OSError as exc:
        raise GateError("gate.export_file_missing") from exc
    for path in entries:
        if path.is_symlink() or not path.is_file() or path.name not in expected_files:
            raise GateError("gate.unexpected_export_file")


def verify(
    staging_dir: str | Path,
    allowlist: Allowlist,
    manifest_path: str | Path | None = None,
    *,
    source_lists: tuple[str, ...] = LOGICAL_LIST_ORDER,
    eligible_export_name: str | None = "dim_public_organizations",
) -> GateResult:
    """Verify exact fields, order, grain, encoding, and optional provenance."""

    root = Path(staging_dir)
    if root.is_symlink() or not root.is_dir():
        raise GateError("gate.export_file_missing")
    export_dir = root / "exports"
    expected_files = {entry.file_name for entry in allowlist.exports}
    _check_export_directory(export_dir, expected_files)

    violations: list[GateViolation] = []
    observed: dict[str, tuple[str, int]] = {}
    for entry in allowlist.exports:
        path = export_dir / entry.file_name
        if path.is_symlink() or not path.is_file():
            raise GateError("gate.export_file_missing")
        payload, rows = _read_csv(path)
        header = tuple(rows[0])
        expected_columns = set(entry.columns)
        observed_columns = set(header)
        if observed_columns != expected_columns or len(header) != len(entry.columns):
            locators = sorted((observed_columns - expected_columns) | (expected_columns - observed_columns))
            if not locators:
                locators = [None]
            violations.extend(
                GateViolation(entry.export_name, "gate.column_set_mismatch", locator)
                for locator in locators
            )
        elif header != entry.columns:
            violations.append(GateViolation(entry.export_name, "gate.column_order_mismatch"))

        valid_rows: list[list[str]] = []
        for row in rows[1:]:
            if len(row) != len(header):
                violations.append(GateViolation(entry.export_name, "gate.row_arity_mismatch"))
            else:
                valid_rows.append(row)
        if header == entry.columns:
            positions = tuple(entry.columns.index(column) for column in entry.grain)
            keys = [tuple(row[position] for position in positions) for row in valid_rows]
            if len(keys) != len(set(keys)):
                violations.append(GateViolation(entry.export_name, "gate.grain_not_unique"))
        if entry.export_class == "aggregate":
            for column in sorted(observed_columns & AGGREGATE_PROHIBITED_COLUMNS):
                violations.append(
                    GateViolation(entry.export_name, "gate.identity_column_in_aggregate", column)
                )
        observed[entry.export_name] = (hashlib.sha256(payload).hexdigest(), len(rows) - 1)

    if manifest_path is not None:
        document = _load_manifest_payload(Path(manifest_path))
        export_set_mismatch = _validate_manifest(
            document, allowlist, observed, source_lists
        )
        if export_set_mismatch:
            violations.append(GateViolation("manifest", "gate.manifest_export_set_mismatch"))
        records = {
            item["export_name"]: item
            for item in document["exports"]
            if isinstance(item, dict) and isinstance(item.get("export_name"), str)
        }
        for export_name in sorted(set(records) & set(observed)):
            sha256, row_count = observed[export_name]
            if records[export_name]["sha256"] != sha256:
                violations.append(GateViolation(export_name, "gate.manifest_export_hash_mismatch"))
            if records[export_name]["row_count"] != row_count:
                violations.append(GateViolation(export_name, "gate.manifest_row_count_mismatch"))
        if eligible_export_name is not None:
            eligible_observation = observed.get(eligible_export_name)
            if eligible_observation is None:
                raise GateError("gate.manifest_invalid_schema")
            if document["eligible_key_count"] != eligible_observation[1]:
                violations.append(
                    GateViolation(
                        eligible_export_name,
                        "gate.manifest_eligible_key_count_mismatch",
                    )
                )

    return GateResult(
        violations=tuple(
            sorted(
                violations,
                key=lambda item: (item.export_name, item.category, item.column_name or ""),
            )
        )
    )


GATE_CATEGORIES = GATE_VIOLATION_CATEGORIES | GATE_ERROR_CATEGORIES

__all__ = [
    "GATE_CATEGORIES",
    "GATE_ERROR_CATEGORIES",
    "GATE_VIOLATION_CATEGORIES",
    "GateError",
    "GateResult",
    "GateViolation",
    "verify",
]
