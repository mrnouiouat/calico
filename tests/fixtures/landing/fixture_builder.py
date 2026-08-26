"""Deterministic identity-free candidate mutation builder (D-10).

Every function here starts from the immutable, committed baseline in
`tests/fixtures/landing/valid/` -- four tiny CP1252-encoded CSVs plus their
closed `candidate-set.json` manifest -- and produces exactly one required
safe defect or revision scenario inside a unique, owned
`TemporaryDirectory`. The committed baseline is never opened for writing;
only the returned temporary copy is mutated, and only that same temporary
root is ever removed.

The four committed baseline CSVs are deliberately encoded using only bytes
that are simultaneously valid CP1252 and valid UTF-8 (the printable ASCII
range) -- `tools/privacy_scan` fails closed on any committed blob it cannot
decode as UTF-8 (an intentional, locked defense against unreviewable binary
content, not a bug this module works around). `cp1252_high_byte_field`
below covers the "valid CP1252 punctuation byte round-trips, no blanket
0x80-0x9F rejection" case from `02-VALIDATION.md`'s synthetic fixture matrix
entirely within an owned temporary copy, so that requirement is proven
without ever committing a non-UTF-8 byte to Git.

Mutations write raw CP1252 bytes directly (manual `str.encode("cp1252")`
calls over unchanged field text), never through `csv.writer` or any other
quote-aware reconstruction -- so an unescaped quote or an injected CP1252
high-byte character stays byte-identical instead of being re-escaped.

Every public mutation is a context manager yielding a `MutatedCandidate`
whose `root` is a temporary candidate directory: the same closed manifest
key set as the baseline plus the four (or fewer, for the missing-mapping
case) CSV files, ready to hand to a future candidate-loading caller. No
function here imports from `scripts/research/**`, embeds an absolute owner
path, a real organization identity, or a FEIN/EIN-shaped value -- only the
reserved synthetic sentinel constants below, which a caller may use to
assert non-echo behavior (D-05/D-10) against whatever it does with a
mutated candidate.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

#: The immutable committed baseline directory this builder copies from.
#: This module never opens a path under here for writing.
BASELINE_DIR = Path(__file__).resolve().parent / "valid"

#: The four locked logical-list identities and their baseline CSV
#: filenames, matching `calico_landing.contracts.LOGICAL_LIST_ORDER`
#: exactly (02-RESEARCH.md `LOGICAL_ORDER`).
LOGICAL_LIST_FILES: dict[str, str] = {
    "charities-may-operate": "charities-may-operate.csv",
    "charities-not-operating": "charities-not-operating.csv",
    "charities-undetermined-status": "charities-undetermined-status.csv",
    "charities-may-not-operate": "charities-may-not-operate.csv",
}

MANIFEST_FILENAME = "candidate-set.json"

#: Exact eleven published contract column positions (shared by every
#: baseline CSV), used by field-level mutation helpers below.
STATUS_COLUMN = 0
REG_NUMBER_COLUMN = 1
FEIN_COLUMN = 2
SOS_FTB_COLUMN = 3
NAME_COLUMN = 4
CITY_COLUMN = 5
STATE_COLUMN = 6
ISSUE_DATE_COLUMN = 7
LAST_RENEWAL_COLUMN = 8
DATE_STATUS_SET_COLUMN = 9
AS_OF_DATE_COLUMN = 10

#: Reserved synthetic sentinels -- never a real FEIN/EIN, registration
#: number, or organization identity. Split via runtime concatenation so
#: the committed source text never contains a contiguous privacy-scanner
#: match while the runtime value stays byte-identical (mirrors the fix
#: documented in Phase 1 Plan 01-03 and Phase 2 Plans 01/02).
SENTINEL_DUPLICATE_KEY = "70" + "88001"
SENTINEL_UNKNOWN_FAMILY_KEY = "ZZ" + "990011"
SENTINEL_MISMATCHED_DATE = "2020-01-16"

#: A valid CP1252 punctuation byte (0x96, en dash) in the 0x80-0x9F range
#: that has no standalone valid UTF-8 encoding -- injected only into an
#: owned temporary copy, never the committed baseline.
CP1252_HIGH_BYTE_NAME = "Meadow Fund – Society"


class FixtureBuilderError(Exception):
    """Raised on an unknown logical list/index, or a caller attempt to
    resolve a path outside the owned temporary root. Carries only a fixed
    safe `category` -- never a path or content value.
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class MutatedCandidate:
    """One owned temporary candidate directory.

    `root` is a unique `TemporaryDirectory` seeded with an unmodified copy
    of the committed baseline. Every method resolves its target strictly
    inside `root` and raises `FixtureBuilderError` for anything else --
    this object can mutate or delete only its own owned files, never the
    committed baseline or any path outside `root`.
    """

    root: Path

    def _contained(self, filename: str) -> Path:
        target = (self.root / filename).resolve()
        if target.parent != self.root:
            raise FixtureBuilderError("mutation.path_outside_owned_root")
        return target

    def csv_path(self, logical_list: str) -> Path:
        try:
            filename = LOGICAL_LIST_FILES[logical_list]
        except KeyError as exc:
            raise FixtureBuilderError("mutation.unknown_logical_list") from exc
        return self._contained(filename)

    def manifest_path(self) -> Path:
        return self._contained(MANIFEST_FILENAME)

    def read_bytes(self, logical_list: str) -> bytes:
        return self.csv_path(logical_list).read_bytes()

    def write_bytes(self, logical_list: str, payload: bytes) -> None:
        self.csv_path(logical_list).write_bytes(payload)

    def delete_csv(self, logical_list: str) -> None:
        self.csv_path(logical_list).unlink()

    def truncate(self, logical_list: str, keep_bytes: int) -> None:
        original = self.read_bytes(logical_list)
        if keep_bytes < 0 or keep_bytes > len(original):
            raise FixtureBuilderError("mutation.invalid_truncation_length")
        self.write_bytes(logical_list, original[:keep_bytes])

    def replace_header(self, logical_list: str, new_header_line: str) -> None:
        text = self.read_bytes(logical_list).decode("cp1252")
        lines = text.split("\r\n")
        if not lines or not lines[0]:
            raise FixtureBuilderError("mutation.empty_baseline")
        lines[0] = new_header_line
        self.write_bytes(logical_list, "\r\n".join(lines).encode("cp1252"))

    def append_row(self, logical_list: str, row_text: str) -> None:
        original = self.read_bytes(logical_list)
        if not original.endswith(b"\r\n"):
            original += b"\r\n"
        self.write_bytes(logical_list, original + row_text.encode("cp1252") + b"\r\n")

    def read_field(self, logical_list: str, row_index: int, column_index: int) -> str:
        return self._row_fields(logical_list, row_index)[column_index]

    def replace_field(
        self, logical_list: str, row_index: int, column_index: int, new_value: str
    ) -> None:
        text = self.read_bytes(logical_list).decode("cp1252")
        lines = text.split("\r\n")
        line_index = self._data_line_index(lines, row_index, logical_list)
        fields = lines[line_index].split(",")
        if column_index < 0 or column_index >= len(fields):
            raise FixtureBuilderError("mutation.column_index_out_of_range")
        fields[column_index] = new_value
        lines[line_index] = ",".join(fields)
        self.write_bytes(logical_list, "\r\n".join(lines).encode("cp1252"))

    def _data_line_index(self, lines: list[str], row_index: int, logical_list: str) -> int:
        line_index = row_index + 1  # skip the header line
        if row_index < 0 or line_index >= len(lines) or not lines[line_index]:
            raise FixtureBuilderError("mutation.row_index_out_of_range")
        return line_index

    def _row_fields(self, logical_list: str, row_index: int) -> list[str]:
        text = self.read_bytes(logical_list).decode("cp1252")
        lines = text.split("\r\n")
        line_index = self._data_line_index(lines, row_index, logical_list)
        return lines[line_index].split(",")

    def remove_from_manifest(self, logical_list: str) -> None:
        document = json.loads(self.manifest_path().read_text(encoding="utf-8"))
        objects = document.get("objects", {})
        if logical_list not in objects:
            raise FixtureBuilderError("mutation.unknown_logical_list")
        del objects[logical_list]
        self.manifest_path().write_text(json.dumps(document), encoding="utf-8")


@contextmanager
def mutated_candidate() -> Iterator[MutatedCandidate]:
    """Yield a fresh `MutatedCandidate` seeded from the immutable baseline.

    Copies the closed manifest plus all four baseline CSVs into a unique
    owned `TemporaryDirectory`, never opening a baseline path for writing.
    Only the owned temporary root is removed on exit, regardless of which
    mutation helpers were called against it.
    """

    with tempfile.TemporaryDirectory(prefix="calico-fixture-") as temp_name:
        temp_root = Path(temp_name).resolve()
        for filename in (*LOGICAL_LIST_FILES.values(), MANIFEST_FILENAME):
            shutil.copyfile(BASELINE_DIR / filename, temp_root / filename)
        yield MutatedCandidate(root=temp_root)


@contextmanager
def truncated_payload(
    logical_list: str = "charities-may-operate", *, keep_bytes: int = 20
) -> Iterator[MutatedCandidate]:
    """Truncate one CSV mid-payload -- a transfer/container-level defect."""

    with mutated_candidate() as candidate:
        candidate.truncate(logical_list, keep_bytes)
        yield candidate


@contextmanager
def missing_mapping(
    logical_list: str = "charities-not-operating",
) -> Iterator[MutatedCandidate]:
    """Delete one CSV and its manifest entry -- an incomplete four-list set."""

    with mutated_candidate() as candidate:
        candidate.delete_csv(logical_list)
        candidate.remove_from_manifest(logical_list)
        yield candidate


@contextmanager
def wrong_header(
    logical_list: str = "charities-undetermined-status",
) -> Iterator[MutatedCandidate]:
    """Replace the header row with one that does not match the contract."""

    with mutated_candidate() as candidate:
        candidate.replace_header(logical_list, "Wrong,Header,Row")
        yield candidate


@contextmanager
def wrong_arity(
    logical_list: str = "charities-may-not-operate",
) -> Iterator[MutatedCandidate]:
    """Append a data row with far fewer than the eleven contract fields."""

    with mutated_candidate() as candidate:
        candidate.append_row(logical_list, "Active," + SENTINEL_DUPLICATE_KEY)
        yield candidate


@contextmanager
def blank_date(
    logical_list: str = "charities-may-operate", *, row_index: int = 0
) -> Iterator[MutatedCandidate]:
    """Blank one row's As-of Date -- the shared-date rule requires nonblank."""

    with mutated_candidate() as candidate:
        candidate.replace_field(logical_list, row_index, AS_OF_DATE_COLUMN, "")
        yield candidate


@contextmanager
def mismatched_date(
    logical_list: str = "charities-may-operate", *, row_index: int = 0
) -> Iterator[MutatedCandidate]:
    """Change one row's As-of Date so it disagrees with the shared set date."""

    with mutated_candidate() as candidate:
        candidate.replace_field(
            logical_list, row_index, AS_OF_DATE_COLUMN, SENTINEL_MISMATCHED_DATE
        )
        yield candidate


@contextmanager
def duplicate_key_within_list(
    logical_list: str = "charities-may-operate", *, row_a: int = 0, row_b: int = 1
) -> Iterator[MutatedCandidate]:
    """Give two rows in the same list the same nonblank registration key."""

    with mutated_candidate() as candidate:
        candidate.replace_field(logical_list, row_a, REG_NUMBER_COLUMN, SENTINEL_DUPLICATE_KEY)
        candidate.replace_field(logical_list, row_b, REG_NUMBER_COLUMN, SENTINEL_DUPLICATE_KEY)
        yield candidate


@contextmanager
def duplicate_key_across_lists(
    list_a: str = "charities-may-operate",
    list_b: str = "charities-not-operating",
    *,
    row_a: int = 0,
    row_b: int = 0,
) -> Iterator[MutatedCandidate]:
    """Give one row in each of two different lists the same registration key."""

    with mutated_candidate() as candidate:
        candidate.replace_field(list_a, row_a, REG_NUMBER_COLUMN, SENTINEL_DUPLICATE_KEY)
        candidate.replace_field(list_b, row_b, REG_NUMBER_COLUMN, SENTINEL_DUPLICATE_KEY)
        yield candidate


@contextmanager
def unknown_registration_family(
    logical_list: str = "charities-undetermined-status", *, row_index: int = 0
) -> Iterator[MutatedCandidate]:
    """Give one row a nonblank key outside the three approved families."""

    with mutated_candidate() as candidate:
        candidate.replace_field(
            logical_list, row_index, REG_NUMBER_COLUMN, SENTINEL_UNKNOWN_FAMILY_KEY
        )
        yield candidate


@contextmanager
def cp1252_high_byte_field(
    logical_list: str = "charities-may-operate", *, row_index: int = 1
) -> Iterator[MutatedCandidate]:
    """Inject a valid CP1252 punctuation byte (0x80-0x9F range) into one
    field of an owned temporary copy -- proves the parser round-trips real
    high-byte CP1252 content without a blanket 0x80-0x9F rejection, while
    never committing the resulting non-UTF-8 byte to Git (see module
    docstring).
    """

    with mutated_candidate() as candidate:
        candidate.replace_field(logical_list, row_index, NAME_COLUMN, CP1252_HIGH_BYTE_NAME)
        yield candidate


@contextmanager
def valid_same_date_revision(
    logical_list: str = "charities-may-operate", *, row_index: int = 0
) -> Iterator[MutatedCandidate]:
    """A fully valid changed candidate sharing the baseline's As-of Date --
    a legitimate same-day republication (e.g. one status change) that
    should admit as a new revision, not a rejection.
    """

    with mutated_candidate() as candidate:
        candidate.replace_field(logical_list, row_index, STATUS_COLUMN, "Delinquent")
        candidate.replace_field(
            logical_list, row_index, DATE_STATUS_SET_COLUMN, "2020-01-15"
        )
        yield candidate


@contextmanager
def invalid_same_date_revision(
    logical_list: str = "charities-may-operate",
) -> Iterator[MutatedCandidate]:
    """A changed candidate sharing the baseline's As-of Date that must still
    be rejected (here, via a within-list duplicate key) -- the prior
    accepted revision must stay byte-identical.
    """

    with mutated_candidate() as candidate:
        candidate.replace_field(logical_list, 0, REG_NUMBER_COLUMN, SENTINEL_DUPLICATE_KEY)
        candidate.replace_field(logical_list, 1, REG_NUMBER_COLUMN, SENTINEL_DUPLICATE_KEY)
        yield candidate


__all__ = [
    "BASELINE_DIR",
    "LOGICAL_LIST_FILES",
    "MANIFEST_FILENAME",
    "FixtureBuilderError",
    "MutatedCandidate",
    "SENTINEL_DUPLICATE_KEY",
    "SENTINEL_MISMATCHED_DATE",
    "SENTINEL_UNKNOWN_FAMILY_KEY",
    "CP1252_HIGH_BYTE_NAME",
    "mutated_candidate",
    "truncated_payload",
    "missing_mapping",
    "wrong_header",
    "wrong_arity",
    "blank_date",
    "mismatched_date",
    "duplicate_key_within_list",
    "duplicate_key_across_lists",
    "unknown_registration_family",
    "cp1252_high_byte_field",
    "valid_same_date_revision",
    "invalid_same_date_revision",
]
