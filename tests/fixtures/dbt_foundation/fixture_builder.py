"""Closed Gate B multi-date/multi-revision fixture loader and builder (D-01, D-14, D-16).

Loads the committed, versioned `gate-b-fixture-v1.json` scenario document --
three synthetic as-of dates, four immutable revisions (two sharing the
middle date with an explicit pointer variant each), and the four locked
D-01 defect shapes (universal field padding, an unmatched quote, a blank
registration number, and a blank status) -- and materializes each revision
as a CP1252/`QUOTE_NONE` candidate directory inside its own owned
`TemporaryDirectory`, admitted through the real Phase 2
`calico_landing.admission.admit()` boundary exactly like a genuine release.

This module never constructs Parquet or a store manifest directly (D-01,
D-14): `gate_b_fixture_store()` is the only path from the committed JSON
scenario to an admitted store, and it always crosses `admit()`. Every
generated candidate lives only under a `TemporaryDirectory` this module
owns; no generated row-bearing path is ever committed, and every path this
module writes is verified to stay inside its own owned root (T-03-05).

Every failure -- malformed scenario document, an excluded field carrying a
nonblank value, an unapproved registration-number family, an oversized
declared row/scenario, or an attempted write outside an owned temporary
root -- crosses this module's boundary as a `GateBFixtureError` carrying
only a fixed safe `category`, mirroring `tests/fixtures/landing/fixture_builder.py`
and the product's own non-echo exception discipline (D-05/D-10). No real
organization identity, FEIN/EIN-shaped value, or real admitted as-of date
is ever used -- only invented synthetic values.
"""

from __future__ import annotations

import json
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator

from calico_landing.admission import admit
from calico_landing.contracts import LOGICAL_LIST_ORDER
from calico_landing.result import AdmissionResult

#: The committed closed fixture-scenario document this module always loads.
FIXTURE_SPEC_PATH = Path(__file__).resolve().parent / "gate-b-fixture-v1.json"

MANIFEST_FILENAME = "candidate-set.json"

#: Exact eleven published contract column names, in contract order --
#: matches `calico_landing.contracts` / `ag-registry-csv-v1.json` exactly.
_FIELD_ORDER: tuple[str, ...] = (
    "Registry Status",
    "State Charity Reg#",
    "FEIN",
    "SOS/FTB#",
    "Name",
    "City",
    "State",
    "Issue Date",
    "Last Renewal",
    "Date Status Set",
    "As-of Date",
)
_RECORD_FIELD_SET = frozenset(_FIELD_ORDER)

_TOP_LEVEL_KEYS = frozenset(
    {
        "fixture_version",
        "max_rows_per_logical_list",
        "max_declared_bytes_per_object",
        "as_of_dates",
        "revisions",
    }
)
_REVISION_KEYS = frozenset({"revision_label", "as_of_date", "pointer_variant", "records"})

_SUPPORTED_FIXTURE_VERSION = 1
_REQUIRED_DISTINCT_DATES = 3
_REQUIRED_REVISION_COUNT = 4
_REQUIRED_MIDDLE_DATE_REVISIONS = 2

#: Safety ceilings this loader enforces on the committed document itself,
#: independent of its own declared `max_rows_per_logical_list` /
#: `max_declared_bytes_per_object` values (T-03-05 defense in depth).
_MAX_ROWS_PER_LIST_CAP = 25
_MAX_DECLARED_BYTES_CAP = 8192

#: The three locked approved nonblank registration-key families, mirrored
#: from `calico_landing.validation` so a fixture-level violation fails
#: closed here rather than only at `admit()` time.
_BARE_DIGITS_PATTERN = re.compile(r"^\d+$")
_CT_FAMILY_PATTERN = re.compile(r"^CT\d+$")
_EX_FAMILY_PATTERN = re.compile(r"^EX\d+$")

#: The four locked D-01 defect shapes this fixture must collectively prove.
_REQUIRED_DEFECT_SHAPES: tuple[str, ...] = (
    "universal_padding",
    "unmatched_quote",
    "blank_registration",
    "blank_status",
)


class GateBFixtureError(Exception):
    """Raised on any malformed scenario document, excluded-field violation,
    resource-ceiling breach, or attempted write outside an owned temporary
    root. Carries only a fixed safe `category` -- never an offending value
    or path (D-05/D-10 non-echo discipline).
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def is_approved_registration_family(value: str) -> bool:
    """Return whether `value` matches one of the three locked approved
    nonblank registration-key families (bare digits, `CT` + digits, or
    `EX` + digits).
    """

    return bool(
        _BARE_DIGITS_PATTERN.match(value)
        or _CT_FAMILY_PATTERN.match(value)
        or _EX_FAMILY_PATTERN.match(value)
    )


@dataclass(frozen=True)
class RevisionSpec:
    """One closed, validated fixture revision: its release identity, an
    optional pointer-variant label (nonblank only for a middle-date
    revision), and its complete four-logical-list record set.
    """

    revision_label: str
    as_of_date: str
    pointer_variant: str | None
    records: dict[str, tuple[dict[str, str], ...]]


@dataclass(frozen=True)
class GateBFixtureSpec:
    """The complete, closed, validated Gate B fixture scenario."""

    fixture_version: int
    as_of_dates: tuple[str, ...]
    revisions: tuple[RevisionSpec, ...]
    max_rows_per_logical_list: int
    max_declared_bytes_per_object: int
    middle_as_of_date: str
    middle_revision_labels: tuple[str, ...]


def _parse_record(
    raw_row: object, *, as_of_date: str, max_declared_bytes: int
) -> dict[str, str]:
    if not isinstance(raw_row, dict) or set(raw_row.keys()) != _RECORD_FIELD_SET:
        raise GateBFixtureError("fixture.invalid_record_schema")

    record: dict[str, str] = {}
    for field in _FIELD_ORDER:
        value = raw_row[field]
        if not isinstance(value, str):
            raise GateBFixtureError("fixture.invalid_record_schema")
        if "\r" in value or "\n" in value or "," in value:
            raise GateBFixtureError("fixture.invalid_record_schema")
        record[field] = value

    if record["FEIN"].strip() or record["SOS/FTB#"].strip():
        raise GateBFixtureError("fixture.excluded_field_nonblank")

    reg_value = record["State Charity Reg#"].strip()
    if reg_value and not is_approved_registration_family(reg_value):
        raise GateBFixtureError("fixture.invalid_registration_family")

    if record["As-of Date"].strip() != as_of_date:
        raise GateBFixtureError("fixture.date_mismatch")

    try:
        row_text = ",".join(record[field] for field in _FIELD_ORDER)
        declared_bytes = len(row_text.encode("cp1252")) + 2  # +2 for CRLF
    except UnicodeEncodeError as exc:
        raise GateBFixtureError("fixture.invalid_record_schema") from exc
    if declared_bytes > max_declared_bytes:
        raise GateBFixtureError("fixture.row_bytes_exceeded")

    return record


def _parse_revision(
    raw_revision: object,
    *,
    as_of_dates: tuple[str, ...],
    max_rows: int,
    max_declared_bytes: int,
    seen_labels: set[str],
) -> RevisionSpec:
    if not isinstance(raw_revision, dict) or set(raw_revision.keys()) != _REVISION_KEYS:
        raise GateBFixtureError("fixture.invalid_revision_schema")

    revision_label = raw_revision.get("revision_label")
    if not isinstance(revision_label, str) or not revision_label:
        raise GateBFixtureError("fixture.invalid_revision_schema")
    if revision_label in seen_labels:
        raise GateBFixtureError("fixture.invalid_revision_schema")
    seen_labels.add(revision_label)

    as_of_date = raw_revision.get("as_of_date")
    if not isinstance(as_of_date, str) or as_of_date not in as_of_dates:
        raise GateBFixtureError("fixture.invalid_revision_schema")

    pointer_variant = raw_revision.get("pointer_variant")
    if pointer_variant is not None and (
        not isinstance(pointer_variant, str) or not pointer_variant
    ):
        raise GateBFixtureError("fixture.invalid_revision_schema")

    raw_records = raw_revision.get("records")
    if not isinstance(raw_records, dict) or set(raw_records.keys()) != set(LOGICAL_LIST_ORDER):
        raise GateBFixtureError("fixture.invalid_revision_schema")

    records: dict[str, tuple[dict[str, str], ...]] = {}
    for logical_list in LOGICAL_LIST_ORDER:
        raw_rows = raw_records[logical_list]
        if not isinstance(raw_rows, list) or not raw_rows:
            raise GateBFixtureError("fixture.invalid_revision_schema")
        if len(raw_rows) > max_rows:
            raise GateBFixtureError("fixture.row_count_exceeded")

        records[logical_list] = tuple(
            _parse_record(raw_row, as_of_date=as_of_date, max_declared_bytes=max_declared_bytes)
            for raw_row in raw_rows
        )

    return RevisionSpec(
        revision_label=revision_label,
        as_of_date=as_of_date,
        pointer_variant=pointer_variant,
        records=records,
    )


def load_gate_b_fixture_spec(path: str | Path = FIXTURE_SPEC_PATH) -> GateBFixtureSpec:
    """Load and strictly validate the closed Gate B fixture scenario document.

    Fails closed with a fixed `GateBFixtureError` category on any unknown
    key, unsupported version, malformed revision/record shape, excluded
    (FEIN/SOS-FTB) field carrying a nonblank value, unapproved registration
    family, declared row/byte ceiling breach, or a revision distribution
    that does not carry exactly three dates, four revisions, and one
    middle date with two distinctly labeled pointer variants. Validation
    completes entirely in memory before any candidate is ever materialized
    (T-03-05) -- no filesystem write happens as a side effect of this call.
    """

    spec_path = Path(path)
    try:
        raw_bytes = spec_path.read_bytes()
    except OSError as exc:
        raise GateBFixtureError("fixture.spec_not_found") from exc

    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateBFixtureError("fixture.invalid_spec_json") from exc

    if not isinstance(document, dict) or set(document.keys()) != _TOP_LEVEL_KEYS:
        raise GateBFixtureError("fixture.invalid_spec_schema")

    fixture_version = document.get("fixture_version")
    if not isinstance(fixture_version, int) or isinstance(fixture_version, bool):
        raise GateBFixtureError("fixture.invalid_spec_schema")
    if fixture_version != _SUPPORTED_FIXTURE_VERSION:
        raise GateBFixtureError("fixture.unsupported_version")

    max_rows = document.get("max_rows_per_logical_list")
    if (
        not isinstance(max_rows, int)
        or isinstance(max_rows, bool)
        or not (1 <= max_rows <= _MAX_ROWS_PER_LIST_CAP)
    ):
        raise GateBFixtureError("fixture.invalid_row_cap")

    max_declared_bytes = document.get("max_declared_bytes_per_object")
    if (
        not isinstance(max_declared_bytes, int)
        or isinstance(max_declared_bytes, bool)
        or not (1 <= max_declared_bytes <= _MAX_DECLARED_BYTES_CAP)
    ):
        raise GateBFixtureError("fixture.invalid_byte_cap")

    raw_dates = document.get("as_of_dates")
    if not isinstance(raw_dates, list) or not all(isinstance(item, str) for item in raw_dates):
        raise GateBFixtureError("fixture.invalid_date_set")
    if len(raw_dates) != _REQUIRED_DISTINCT_DATES or len(set(raw_dates)) != _REQUIRED_DISTINCT_DATES:
        raise GateBFixtureError("fixture.invalid_date_set")
    for value in raw_dates:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise GateBFixtureError("fixture.invalid_date_set") from exc
    as_of_dates = tuple(raw_dates)

    raw_revisions = document.get("revisions")
    if not isinstance(raw_revisions, list) or len(raw_revisions) != _REQUIRED_REVISION_COUNT:
        raise GateBFixtureError("fixture.invalid_revision_count")

    seen_labels: set[str] = set()
    revisions = tuple(
        _parse_revision(
            raw_revision,
            as_of_dates=as_of_dates,
            max_rows=max_rows,
            max_declared_bytes=max_declared_bytes,
            seen_labels=seen_labels,
        )
        for raw_revision in raw_revisions
    )

    counts_by_date: dict[str, int] = {}
    for revision in revisions:
        counts_by_date[revision.as_of_date] = counts_by_date.get(revision.as_of_date, 0) + 1
    if set(counts_by_date.keys()) != set(as_of_dates):
        raise GateBFixtureError("fixture.invalid_revision_distribution")

    middle_dates = [
        value for value, count in counts_by_date.items() if count == _REQUIRED_MIDDLE_DATE_REVISIONS
    ]
    single_dates = [value for value, count in counts_by_date.items() if count == 1]
    if len(middle_dates) != 1 or len(single_dates) != _REQUIRED_DISTINCT_DATES - 1:
        raise GateBFixtureError("fixture.invalid_revision_distribution")
    middle_as_of_date = middle_dates[0]

    middle_revisions = [r for r in revisions if r.as_of_date == middle_as_of_date]
    other_revisions = [r for r in revisions if r.as_of_date != middle_as_of_date]

    middle_variants = [r.pointer_variant for r in middle_revisions]
    if any(variant is None for variant in middle_variants) or len(
        set(middle_variants)
    ) != _REQUIRED_MIDDLE_DATE_REVISIONS:
        raise GateBFixtureError("fixture.invalid_pointer_variant_assignment")
    if any(r.pointer_variant is not None for r in other_revisions):
        raise GateBFixtureError("fixture.invalid_pointer_variant_assignment")

    counts = _defect_shape_counts(revisions)
    for shape in _REQUIRED_DEFECT_SHAPES:
        if counts[shape] <= 0:
            raise GateBFixtureError("fixture.missing_required_defect_shape")

    return GateBFixtureSpec(
        fixture_version=fixture_version,
        as_of_dates=as_of_dates,
        revisions=revisions,
        max_rows_per_logical_list=max_rows,
        max_declared_bytes_per_object=max_declared_bytes,
        middle_as_of_date=middle_as_of_date,
        middle_revision_labels=tuple(r.revision_label for r in middle_revisions),
    )


def _defect_shape_counts(revisions: tuple[RevisionSpec, ...]) -> dict[str, int]:
    counts = {shape: 0 for shape in _REQUIRED_DEFECT_SHAPES}
    for revision in revisions:
        for logical_list in LOGICAL_LIST_ORDER:
            for record in revision.records[logical_list]:
                if any(value != value.strip() for value in record.values()):
                    counts["universal_padding"] += 1
                if any('"' in value for value in record.values()):
                    counts["unmatched_quote"] += 1
                if record["State Charity Reg#"].strip() == "":
                    counts["blank_registration"] += 1
                if record["Registry Status"].strip() == "":
                    counts["blank_status"] += 1
    return counts


def defect_shape_counts(spec: GateBFixtureSpec) -> dict[str, int]:
    """Return the collective count of each locked D-01 defect shape present
    across every record in `spec`.
    """

    return _defect_shape_counts(spec.revisions)


def _contained(root: Path, filename: str) -> Path:
    """Resolve `filename` strictly inside `root`, raising `GateBFixtureError`
    for anything that would escape it (T-03-05; mirrors
    `tests/fixtures/landing/fixture_builder.py`'s `MutatedCandidate._contained`).
    """

    resolved_root = root.resolve()
    target = (resolved_root / filename).resolve()
    if target.parent != resolved_root:
        raise GateBFixtureError("fixture.path_outside_owned_root")
    return target


@contextmanager
def _materialize_candidate(revision: RevisionSpec) -> Iterator[Path]:
    """Materialize one revision's four CP1252/`QUOTE_NONE` candidate CSVs
    plus a closed `candidate-set.json` manifest, entirely inside one owned
    `TemporaryDirectory`. Declared `content_length` values are computed
    from the actually-written bytes, never hardcoded, so they can never
    drift from the generated payload. Every write is written using
    `_contained` and raw byte concatenation -- never `csv.writer` -- so an
    embedded, unmatched quote in a field stays byte-identical data rather
    than being re-escaped.
    """

    with tempfile.TemporaryDirectory(prefix="calico-gate-b-candidate-") as temp_name:
        candidate_root = Path(temp_name).resolve()
        manifest_objects: dict[str, dict[str, object]] = {}

        for logical_list in LOGICAL_LIST_ORDER:
            rows = revision.records[logical_list]
            lines = [",".join(_FIELD_ORDER)]
            for record in rows:
                lines.append(",".join(record[field] for field in _FIELD_ORDER))
            text = "\r\n".join(lines) + "\r\n"
            payload = text.encode("cp1252")

            filename = f"{logical_list}.csv"
            target = _contained(candidate_root, filename)
            target.write_bytes(payload)

            manifest_objects[logical_list] = {
                "relative_path": filename,
                "content_length": len(payload),
            }

        manifest_document = {"manifest_version": 1, "objects": manifest_objects}
        manifest_path = _contained(candidate_root, MANIFEST_FILENAME)
        manifest_path.write_text(json.dumps(manifest_document), encoding="utf-8")

        yield candidate_root


@dataclass(frozen=True)
class GateBFixtureAdmission:
    """One revision's safe admission outcome, paired with its fixture
    identity for test assertions.
    """

    revision_label: str
    as_of_date: str
    result: AdmissionResult


@dataclass(frozen=True)
class GateBFixtureStore:
    """The complete admitted Gate B fixture store: every revision's
    admission outcome plus the resolved pointer variant that was admitted
    last for the shared middle date.
    """

    store_root: Path
    admissions: tuple[GateBFixtureAdmission, ...]
    pointer_variant: str


def _ordered_revisions(
    spec: GateBFixtureSpec, pointer_variant: str | None
) -> tuple[tuple[RevisionSpec, ...], str]:
    resolved_variant = (
        pointer_variant if pointer_variant is not None else spec.middle_revision_labels[-1]
    )
    if resolved_variant not in spec.middle_revision_labels:
        raise GateBFixtureError("fixture.unknown_pointer_variant")

    non_middle = [r for r in spec.revisions if r.revision_label not in spec.middle_revision_labels]
    middle_by_label = {
        r.revision_label: r for r in spec.revisions if r.revision_label in spec.middle_revision_labels
    }
    ordered_middle = [
        middle_by_label[label] for label in spec.middle_revision_labels if label != resolved_variant
    ]
    ordered_middle.append(middle_by_label[resolved_variant])

    return tuple(non_middle + ordered_middle), resolved_variant


@contextmanager
def gate_b_fixture_store(pointer_variant: str | None = None) -> Iterator[GateBFixtureStore]:
    """Admit the complete closed Gate B fixture scenario through the real
    Phase 2 `calico_landing.admission.admit()` boundary and yield the
    resulting `GateBFixtureStore`.

    `pointer_variant`, if given, must name one of the two middle-date
    revision labels; that revision is admitted last for its shared date so
    it becomes the store's promoted pointer for that date (mirrors D-06:
    the most recently admitted revision is the default promotion). If
    omitted, the scenario document's own declared last middle-date
    revision label is used.

    Every revision this call admits is materialized only inside its own
    owned `TemporaryDirectory` (`_materialize_candidate`); the returned
    store itself lives inside a second owned `TemporaryDirectory` that is
    fully removed -- together with every revision, canonical Parquet
    object, and manifest it contains -- when this context manager exits.
    No path this function ever touches is inside a Git worktree or any
    committed fixture directory (D-02, D-16).
    """

    spec = load_gate_b_fixture_spec(FIXTURE_SPEC_PATH)
    ordered_revisions, resolved_variant = _ordered_revisions(spec, pointer_variant)

    with tempfile.TemporaryDirectory(prefix="calico-gate-b-fixture-store-") as store_dir:
        store_root = Path(store_dir).resolve()
        admissions: list[GateBFixtureAdmission] = []

        for revision in ordered_revisions:
            with _materialize_candidate(revision) as candidate_root:
                result = admit(candidate_root, store_root)

            if result.status != "accepted":
                raise GateBFixtureError("fixture.admission_rejected")

            admissions.append(
                GateBFixtureAdmission(
                    revision_label=revision.revision_label,
                    as_of_date=revision.as_of_date,
                    result=result,
                )
            )

        yield GateBFixtureStore(
            store_root=store_root,
            admissions=tuple(admissions),
            pointer_variant=resolved_variant,
        )


__all__ = [
    "FIXTURE_SPEC_PATH",
    "MANIFEST_FILENAME",
    "GateBFixtureError",
    "RevisionSpec",
    "GateBFixtureSpec",
    "GateBFixtureAdmission",
    "GateBFixtureStore",
    "is_approved_registration_family",
    "load_gate_b_fixture_spec",
    "defect_shape_counts",
    "gate_b_fixture_store",
]
