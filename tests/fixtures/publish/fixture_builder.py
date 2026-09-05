"""Copy-before-mutate publication fixtures with identity-free committed bytes.

Every public helper mutates only a disposable copy of ``valid``. Reserved
synthetic sentinels are assembled at runtime where a contiguous literal could
otherwise resemble an identity; no real identity is present in this module or
the committed baseline.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from calico_publish.allowlist import AGGREGATE_PROHIBITED_COLUMNS

BASELINE_DIR = Path(__file__).resolve().parent / "valid"
MANIFEST_RELATIVE_PATH = Path("manifest") / "published-manifest-v1.json"

_EXTRA_COLUMN = "unapproved_field"
_SYNTHETIC_EXTRA = "synthetic_" + "extra"
_SYNTHETIC_IDENTITY = "synthetic_" + "identity"


class FixtureBuilderError(Exception):
    """A value-free fixture-builder boundary failure."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class MutatedPublication:
    root: Path

    def _contained(self, relative_path: str | Path) -> Path:
        root = self.root.resolve()
        target = (root / relative_path).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise FixtureBuilderError("mutation.path_outside_owned_root") from exc
        return target

    @property
    def allowlist_path(self) -> Path:
        return self._contained("publication-exports-v1.json")

    @property
    def manifest_path(self) -> Path:
        return self._contained(MANIFEST_RELATIVE_PATH)

    def read_bytes(self, relative_path: str | Path) -> bytes:
        try:
            return self._contained(relative_path).read_bytes()
        except OSError as exc:
            raise FixtureBuilderError("mutation.read_failed") from exc

    def write_bytes(self, relative_path: str | Path, payload: bytes) -> None:
        if not isinstance(payload, bytes):
            raise FixtureBuilderError("mutation.invalid_payload")
        try:
            with self._contained(relative_path).open("wb") as handle:
                handle.write(payload)
        except OSError as exc:
            raise FixtureBuilderError("mutation.write_failed") from exc

    def read_manifest(self) -> dict[str, object]:
        try:
            document = json.loads(self.read_bytes(MANIFEST_RELATIVE_PATH).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FixtureBuilderError("mutation.invalid_manifest") from exc
        if not isinstance(document, dict):
            raise FixtureBuilderError("mutation.invalid_manifest")
        return document

    def write_manifest(self, document: dict[str, object]) -> None:
        payload = (
            json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            + "\n"
        ).encode("utf-8")
        self.write_bytes(MANIFEST_RELATIVE_PATH, payload)

    def _read_rows(self, relative_path: str | Path) -> list[list[str]]:
        try:
            text = self.read_bytes(relative_path).decode("utf-8")
            return list(csv.reader(io.StringIO(text, newline=""), strict=True))
        except (UnicodeDecodeError, csv.Error) as exc:
            raise FixtureBuilderError("mutation.invalid_csv") from exc

    def _write_rows(self, relative_path: str | Path, rows: list[list[str]]) -> None:
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerows(rows)
        self.write_bytes(relative_path, stream.getvalue().encode("utf-8"))

    def replace_header(self, relative_path: str | Path, header: list[str]) -> None:
        rows = self._read_rows(relative_path)
        if not rows:
            raise FixtureBuilderError("mutation.invalid_csv")
        rows[0] = list(header)
        self._write_rows(relative_path, rows)

    def replace_field(
        self, relative_path: str | Path, row_index: int, column_index: int, value: str
    ) -> None:
        rows = self._read_rows(relative_path)
        try:
            rows[row_index][column_index] = value
        except (IndexError, TypeError) as exc:
            raise FixtureBuilderError("mutation.invalid_csv") from exc
        self._write_rows(relative_path, rows)

    def append_row(self, relative_path: str | Path, row: list[str]) -> None:
        rows = self._read_rows(relative_path)
        rows.append(list(row))
        self._write_rows(relative_path, rows)

    def replace_manifest_field(self, export_name: str, field: str, value: object) -> None:
        document = self.read_manifest()
        exports = document.get("exports")
        if not isinstance(exports, list):
            raise FixtureBuilderError("mutation.invalid_manifest")
        for record in exports:
            if isinstance(record, dict) and record.get("export_name") == export_name:
                record[field] = value
                self.write_manifest(document)
                return
        raise FixtureBuilderError("mutation.invalid_manifest")

    def refresh_export_record(self, export_name: str) -> None:
        relative = Path("exports") / f"{export_name}.csv"
        payload = self.read_bytes(relative)
        rows = self._read_rows(relative)
        self.replace_manifest_field(export_name, "sha256", hashlib.sha256(payload).hexdigest())
        self.replace_manifest_field(export_name, "row_count", max(0, len(rows) - 1))


@contextmanager
def mutated_publication() -> Iterator[MutatedPublication]:
    with tempfile.TemporaryDirectory(prefix="calico-publish-fixture-") as temporary:
        root = Path(temporary) / "publication"
        shutil.copytree(BASELINE_DIR, root)
        yield MutatedPublication(root=root)


@contextmanager
def extra_unapproved_column() -> Iterator[MutatedPublication]:
    with mutated_publication() as publication:
        relative = Path("exports") / "fixture_named_history.csv"
        rows = publication._read_rows(relative)
        rows[0].append(_EXTRA_COLUMN)
        for row in rows[1:]:
            row.append(_SYNTHETIC_EXTRA)
        publication._write_rows(relative, rows)
        publication.refresh_export_record("fixture_named_history")
        yield publication


@contextmanager
def removed_approved_column() -> Iterator[MutatedPublication]:
    with mutated_publication() as publication:
        relative = Path("exports") / "fixture_named_history.csv"
        rows = publication._read_rows(relative)
        removed_index = rows[0].index("observation_state")
        for row in rows:
            del row[removed_index]
        publication._write_rows(relative, rows)
        publication.refresh_export_record("fixture_named_history")
        yield publication


@contextmanager
def reordered_column_list() -> Iterator[MutatedPublication]:
    with mutated_publication() as publication:
        relative = Path("exports") / "fixture_named_history.csv"
        rows = publication._read_rows(relative)
        for row in rows:
            row[2], row[3] = row[3], row[2]
        publication._write_rows(relative, rows)
        publication.refresh_export_record("fixture_named_history")
        yield publication


@contextmanager
def grain_uniqueness_break() -> Iterator[MutatedPublication]:
    with mutated_publication() as publication:
        relative = Path("exports") / "fixture_named_history.csv"
        rows = publication._read_rows(relative)
        rows[2][0] = rows[1][0]
        rows[2][1] = rows[1][1]
        publication._write_rows(relative, rows)
        publication.refresh_export_record("fixture_named_history")
        yield publication


@contextmanager
def identity_column_in_aggregate() -> Iterator[MutatedPublication]:
    with mutated_publication() as publication:
        relative = Path("exports") / "fixture_aggregate_metrics.csv"
        rows = publication._read_rows(relative)
        identity_column = sorted(AGGREGATE_PROHIBITED_COLUMNS)[0]
        rows[0].append(identity_column)
        for index, row in enumerate(rows[1:], start=1):
            row.append(f"{_SYNTHETIC_IDENTITY}_{index}")
        publication._write_rows(relative, rows)
        publication.refresh_export_record("fixture_aggregate_metrics")
        yield publication


@contextmanager
def manifest_hash_disagreement() -> Iterator[MutatedPublication]:
    with mutated_publication() as publication:
        publication.replace_manifest_field(
            "fixture_named_history", "sha256", "c" * 64
        )
        yield publication


__all__ = [
    "BASELINE_DIR",
    "FixtureBuilderError",
    "MutatedPublication",
    "extra_unapproved_column",
    "grain_uniqueness_break",
    "identity_column_in_aggregate",
    "manifest_hash_disagreement",
    "mutated_publication",
    "removed_approved_column",
    "reordered_column_list",
]
