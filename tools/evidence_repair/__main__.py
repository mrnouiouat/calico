"""Safe evidence-repair derivation command (D-11/D-12/D-13; 02-06-PLAN.md).

`python -m tools.evidence_repair derive` reads only admitted release
manifests and their canonical Parquet from an explicit external store,
verifies every manifest/Parquet hash before touching a single row, and
recomputes structural keyed/keyless coverage, the exact D-006 delinquent
count, and canonical membership hashes for three explicitly selected
admitted releases using the bundled, fixed `spike_002_confirmation.sql`
DuckDB script -- never a second raw-CSV interpretation and never a
caller-supplied query. It emits exactly four closed-schema, deterministic
JSON successors: `august-manifest-successor-v1.json`,
`spike-001-successor-v1.json`, `spike-002-successor-v1.json`, and
`correction-index-v1.json`. `python -m tools.evidence_repair verify`
re-checks an existing four-artifact directory's schema, hashes, and
lineage against the same three predecessor paths (and, optionally, the
current admitted store) without rewriting anything.

Three explicit private predecessor paths (the private August manifest, the
private spike 001 manifest, and the private spike 002 entity-change
document) are used for hash/lineage only -- this module never opens or
parses their content, and never records their caller-supplied filesystem
path. Every `supersedes.private_path` value emitted is one of four fixed,
already-public planning-document labels (`_PRIVATE_PATH_*` below), never
the literal path argument the caller passed on the command line (D-05/D-10
non-echo discipline: a local absolute path is exactly what this module
must never carry into a committed artifact).

Every boundary failure is a fixed-category `EvidenceRepairError` -- never
an offending path, row, or exception object crosses this module's CLI
boundary (mirrors `calico_landing.candidate.CandidateError` and
`tools/privacy_scan/git_objects.GitObjectError`'s non-echo exception
shape).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from calico_landing.candidate import CandidateError, reject_store_in_git_worktree
from calico_landing.contracts import LOGICAL_LIST_ORDER
from calico_landing.store import StoreError, read_promoted_releases

# --------------------------------------------------------------------------
# Fixed, closed-schema constants (the agent's discretion; 02-06-PLAN.md).
# --------------------------------------------------------------------------

SCHEMA_VERSION = "correction-index-v1"

_AUGUST_SUCCESSOR_FILENAME = "august-manifest-successor-v1.json"
_SPIKE_001_SUCCESSOR_FILENAME = "spike-001-successor-v1.json"
_SPIKE_002_SUCCESSOR_FILENAME = "spike-002-successor-v1.json"
_CORRECTION_INDEX_FILENAME = "correction-index-v1.json"

_ALL_OUTPUT_FILENAMES = (
    _AUGUST_SUCCESSOR_FILENAME,
    _SPIKE_001_SUCCESSOR_FILENAME,
    _SPIKE_002_SUCCESSOR_FILENAME,
    _CORRECTION_INDEX_FILENAME,
)

#: Fixed, already-public relative lineage labels (`02-CONTEXT.md`
#: `canonical_refs`) -- never the caller's actual local filesystem path.
_PRIVATE_PATH_AUGUST = "data/registry-archive/manifest.json"
_PRIVATE_PATH_SPIKE_001 = (
    ".planning/spikes/001-archive-sample-validation/archive-sample-manifest.json"
)
_PRIVATE_PATH_SPIKE_002 = ".planning/spikes/002-entity-change-validation/entity-changes.json"
_PRIVATE_PATH_GATE_A_EVIDENCE = "GATE-A-EVIDENCE.md"

#: The private `GATE-A-EVIDENCE.md` benchmark is never read by this tool
#: (D-020: it is the fixed benchmark Gate B SQL must reproduce, not an
#: input to Gate A evidence repair). Its hash is locked here once, by the
#: repository owner, as the fixed proof that it remains byte-for-byte
#: unchanged (D-12) -- this constant is never recomputed at runtime.
_GATE_A_EVIDENCE_SHA256 = "3c7943ad82184cd3e54ab0fd844c2b3ec2732fc63eb05395bd53d9662890cf62"

#: Locked D-08 fingerprint algorithm identifier -- must match
#: `calico_landing.store._FINGERPRINT_ALGORITHM` exactly; duplicated here
#: (rather than imported) because that name is private to `store.py`.
_FINGERPRINT_ALGORITHM = "ordered-source-sha256-json-v1"

#: Fixed derivation command identifier recorded in every correction index.
_COMMAND_ID = "evidence-repair-derive-v1"

_CLAIM_STATUS_CORRECTED = "corrected"
_CLAIM_STATUS_CONFIRMED = "confirmed_by_recomputation"

_RESOURCE_MIN = 0
_ELAPSED_MS_MAX = 86_400_000
_PEAK_BYTES_MAX = 1_099_511_627_776

_CANONICAL_UNSIGNED_INTEGER = re.compile(r"^(0|[1-9][0-9]*)$")
_AS_OF_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UTC_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_ARG_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}):([0-9]+)$")

_LOCK_FILENAME = ".evidence-repair.lock"

_MANIFEST_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "as_of_date",
        "release_revision",
        "revision_fingerprint",
        "fingerprint_algorithm",
        "metadata",
    }
)
_METADATA_TOP_LEVEL_KEYS = frozenset(
    {
        "fingerprint_algorithm",
        "parser_contract_version",
        "parquet_writer_version",
        "parquet_compression",
        "parquet_row_group_size",
        "admission_reasons",
        "logical_lists",
    }
)
_LOGICAL_LIST_ENTRY_KEYS = frozenset(
    {
        "raw_sha256",
        "raw_byte_count",
        "parsed_record_count",
        "line_record_reconciled",
        "parquet_sha256",
        "parquet_row_count",
    }
)

_CORRECTION_INDEX_TOP_LEVEL_KEYS = ("schema_version", "derivation", "corrections", "gate_a_evidence")
_DERIVATION_KEYS = (
    "command_id",
    "generated_at_utc",
    "parser_contract_version",
    "parquet_writer_version",
    "source_release_fingerprints",
    "resource_measurements",
)
_SOURCE_FINGERPRINT_KEYS = (
    "as_of_date",
    "release_revision",
    "revision_fingerprint",
    "manifest_sha256",
)
_RESOURCE_MEASUREMENT_KEYS = (
    "first_admission_elapsed_ms",
    "first_admission_peak_temporary_disk_bytes",
)
_CORRECTION_ENTRY_KEYS = ("successor_file", "supersedes", "successor_sha256")
_SUPERSEDES_KEYS = ("private_path", "predecessor_sha256")
_GATE_A_EVIDENCE_KEYS = ("private_path", "sha256", "status")

_READ_CHUNK_BYTES = 1024 * 1024


class EvidenceRepairError(Exception):
    """Raised on any evidence-repair boundary failure.

    Carries only a fixed safe `code` -- never an offending path, row, hash
    mismatch value, or exception text (D-05/D-10 non-echo discipline).
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --------------------------------------------------------------------------
# Safe scalar/path validation helpers.
# --------------------------------------------------------------------------


def _parse_bounded_integer(raw_values: list[str], *, minimum: int, maximum: int, code: str) -> int:
    """Accept exactly one canonical unsigned base-10 digit string.

    Rejects omitted/duplicated occurrences, any non-canonical form (sign,
    fractional/exponent notation, leading zero, whitespace, boolean-shaped
    text), and any value outside `[minimum, maximum]`.
    """

    if len(raw_values) != 1:
        raise EvidenceRepairError(f"{code}.count_invalid")
    raw = raw_values[0]
    if not _CANONICAL_UNSIGNED_INTEGER.match(raw):
        raise EvidenceRepairError(f"{code}.format_invalid")
    value = int(raw)
    if value < minimum or value > maximum:
        raise EvidenceRepairError(f"{code}.range_invalid")
    return value


@dataclass(frozen=True)
class _ReleaseSelection:
    as_of_date: str
    release_revision: int


def _parse_release_selections(raw_values: list[str]) -> tuple[_ReleaseSelection, ...]:
    """Parse exactly three `<as_of_date>:<revision>` selections in strict
    ascending chronological order with no duplicate date.
    """

    if len(raw_values) != 3:
        raise EvidenceRepairError("release.count_invalid")

    selections: list[_ReleaseSelection] = []
    for raw in raw_values:
        match = _RELEASE_ARG_PATTERN.match(raw)
        if match is None:
            raise EvidenceRepairError("release.format_invalid")
        as_of_date, revision_text = match.group(1), match.group(2)
        try:
            date.fromisoformat(as_of_date)
        except ValueError as exc:
            raise EvidenceRepairError("release.format_invalid") from exc
        if revision_text != "0" and revision_text.startswith("0"):
            raise EvidenceRepairError("release.format_invalid")
        revision = int(revision_text)
        if revision <= 0:
            raise EvidenceRepairError("release.format_invalid")
        selections.append(_ReleaseSelection(as_of_date=as_of_date, release_revision=revision))

    for earlier, later in zip(selections, selections[1:]):
        if later.as_of_date <= earlier.as_of_date:
            raise EvidenceRepairError("release.order_invalid")

    return tuple(selections)


def _resolve_existing_dir(raw_path: str, *, code: str) -> Path:
    raw = Path(raw_path)
    if raw.is_symlink():
        raise EvidenceRepairError(code)
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise EvidenceRepairError(code) from exc
    if not resolved.is_dir():
        raise EvidenceRepairError(code)
    return resolved


def _resolve_path_without_requiring_existence(raw_path: str, *, code: str) -> Path:
    """Resolve `raw_path` for early aliasing/collision checks without
    creating it. Rejects a symlinked leaf component; intermediate
    directories may not exist yet.
    """

    raw = Path(raw_path)
    if raw.is_symlink():
        raise EvidenceRepairError(code)
    try:
        return raw.resolve(strict=False)
    except OSError as exc:
        raise EvidenceRepairError(code) from exc


def _resolve_existing_file(raw_path: str, *, code: str) -> Path:
    raw = Path(raw_path)
    if raw.is_symlink():
        raise EvidenceRepairError(code)
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise EvidenceRepairError(code) from exc
    if not resolved.is_file():
        raise EvidenceRepairError(code)
    return resolved


def _is_contained(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _reject_aliasing(paths: dict[str, Path]) -> None:
    """Fail closed if any two of the given resolved roots/files alias,
    nest inside, or equal one another (T-02-04/T-02-07).
    """

    items = list(paths.items())
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            _, path_a = items[i]
            _, path_b = items[j]
            if path_a == path_b or _is_contained(path_a, path_b) or _is_contained(path_b, path_a):
                raise EvidenceRepairError("input.path_aliasing")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# Admitted manifest reading (own strict, closed-schema copy; T-02-02).
# --------------------------------------------------------------------------


def _require_nonnegative_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise EvidenceRepairError("release.malformed_manifest")
    return value


def _require_positive_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise EvidenceRepairError("release.malformed_manifest")
    return value


def _require_nonempty_str(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceRepairError("release.malformed_manifest")
    return value


def _read_admitted_manifest(manifest_path: Path) -> tuple[bytes, dict]:
    if manifest_path.is_symlink():
        raise EvidenceRepairError("release.malformed_manifest")
    try:
        raw_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise EvidenceRepairError("release.malformed_manifest") from exc

    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceRepairError("release.malformed_manifest") from exc

    if not isinstance(document, dict) or set(document.keys()) != _MANIFEST_TOP_LEVEL_KEYS:
        raise EvidenceRepairError("release.malformed_manifest")
    if document.get("schema_version") != 1:
        raise EvidenceRepairError("release.malformed_manifest")
    if not isinstance(document.get("as_of_date"), str) or not _AS_OF_DATE_PATTERN.match(
        document["as_of_date"]
    ):
        raise EvidenceRepairError("release.malformed_manifest")
    _require_positive_int(document.get("release_revision"))
    fingerprint = document.get("revision_fingerprint")
    if not isinstance(fingerprint, str) or not _FINGERPRINT_PATTERN.match(fingerprint):
        raise EvidenceRepairError("release.malformed_manifest")
    if document.get("fingerprint_algorithm") != _FINGERPRINT_ALGORITHM:
        raise EvidenceRepairError("release.malformed_manifest")

    metadata = document.get("metadata")
    if not isinstance(metadata, dict) or set(metadata.keys()) != _METADATA_TOP_LEVEL_KEYS:
        raise EvidenceRepairError("release.malformed_manifest")
    if metadata.get("fingerprint_algorithm") != _FINGERPRINT_ALGORITHM:
        raise EvidenceRepairError("release.malformed_manifest")
    _require_positive_int(metadata.get("parser_contract_version"))
    _require_nonempty_str(metadata.get("parquet_writer_version"))
    _require_nonempty_str(metadata.get("parquet_compression"))
    _require_positive_int(metadata.get("parquet_row_group_size"))
    if not isinstance(metadata.get("admission_reasons"), list):
        raise EvidenceRepairError("release.malformed_manifest")

    logical_lists = metadata.get("logical_lists")
    if not isinstance(logical_lists, dict) or set(logical_lists.keys()) != set(LOGICAL_LIST_ORDER):
        raise EvidenceRepairError("release.malformed_manifest")
    for logical_list in LOGICAL_LIST_ORDER:
        entry = logical_lists[logical_list]
        if not isinstance(entry, dict) or set(entry.keys()) != _LOGICAL_LIST_ENTRY_KEYS:
            raise EvidenceRepairError("release.malformed_manifest")
        raw_sha256 = entry.get("raw_sha256")
        if not isinstance(raw_sha256, str) or not _FINGERPRINT_PATTERN.match(raw_sha256):
            raise EvidenceRepairError("release.malformed_manifest")
        _require_nonnegative_int(entry.get("raw_byte_count"))
        _require_nonnegative_int(entry.get("parsed_record_count"))
        if entry.get("line_record_reconciled") is not True:
            raise EvidenceRepairError("release.malformed_manifest")
        parquet_sha256 = entry.get("parquet_sha256")
        if not isinstance(parquet_sha256, str) or not _FINGERPRINT_PATTERN.match(parquet_sha256):
            raise EvidenceRepairError("release.malformed_manifest")
        _require_nonnegative_int(entry.get("parquet_row_count"))

    return raw_bytes, document


@dataclass(frozen=True)
class _AdmittedRelease:
    as_of_date: str
    release_revision: int
    revision_fingerprint: str
    manifest_sha256: str
    parser_contract_version: int
    parquet_writer_version: str
    canonical_dir: Path
    logical_lists: dict[str, dict]


def _resolve_admitted_release(
    store_root: Path,
    promoted: dict,
    selection: _ReleaseSelection,
) -> _AdmittedRelease:
    entry = promoted.get(selection.as_of_date)
    if entry is None or entry.release_revision != selection.release_revision:
        raise EvidenceRepairError("release.not_promoted")

    revision_dir_raw = store_root / entry.revision_dir
    if revision_dir_raw.is_symlink():
        raise EvidenceRepairError("release.malformed_manifest")
    try:
        revision_dir = revision_dir_raw.resolve(strict=True)
    except OSError as exc:
        raise EvidenceRepairError("release.malformed_manifest") from exc
    if not _is_contained(revision_dir, store_root) or not revision_dir.is_dir():
        raise EvidenceRepairError("release.malformed_manifest")

    manifest_bytes, manifest = _read_admitted_manifest(revision_dir / "manifest.json")
    if (
        manifest["as_of_date"] != selection.as_of_date
        or manifest["release_revision"] != selection.release_revision
        or manifest["revision_fingerprint"] != entry.revision_fingerprint
    ):
        raise EvidenceRepairError("release.manifest_mismatch")

    canonical_dir_raw = revision_dir / "canonical"
    if canonical_dir_raw.is_symlink():
        raise EvidenceRepairError("release.malformed_manifest")
    try:
        canonical_dir = canonical_dir_raw.resolve(strict=True)
    except OSError as exc:
        raise EvidenceRepairError("release.malformed_manifest") from exc
    if not _is_contained(canonical_dir, revision_dir) or not canonical_dir.is_dir():
        raise EvidenceRepairError("release.malformed_manifest")

    metadata = manifest["metadata"]
    for logical_list in LOGICAL_LIST_ORDER:
        parquet_path = canonical_dir / f"{logical_list}.parquet"
        if parquet_path.is_symlink():
            raise EvidenceRepairError("release.malformed_manifest")
        if not parquet_path.is_file():
            raise EvidenceRepairError("release.parquet_missing")
        expected_hash = metadata["logical_lists"][logical_list]["parquet_sha256"]
        if _hash_file(parquet_path) != expected_hash:
            raise EvidenceRepairError("release.parquet_hash_mismatch")

    return _AdmittedRelease(
        as_of_date=selection.as_of_date,
        release_revision=selection.release_revision,
        revision_fingerprint=entry.revision_fingerprint,
        manifest_sha256=_hash_bytes(manifest_bytes),
        parser_contract_version=metadata["parser_contract_version"],
        parquet_writer_version=metadata["parquet_writer_version"],
        canonical_dir=canonical_dir,
        logical_lists=metadata["logical_lists"],
    )


# --------------------------------------------------------------------------
# Bundled fixed SQL loading and execution.
# --------------------------------------------------------------------------

_SQL_SCRIPT_PATH = Path(__file__).resolve().parent / "spike_002_confirmation.sql"
_BLOCK_MARKER = re.compile(r"^-- @query:\s*(\S+)\s*$", re.MULTILINE)


def _load_sql_blocks(sql_path: Path) -> dict[str, str]:
    text = sql_path.read_text(encoding="utf-8")
    markers = list(_BLOCK_MARKER.finditer(text))
    if not markers:
        raise EvidenceRepairError("internal.sql_script_empty")
    blocks: dict[str, str] = {}
    for index, match in enumerate(markers):
        name = match.group(1)
        start = match.end()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        blocks[name] = text[start:end].strip()
    return blocks


def _register_release_views(
    connection: "duckdb.DuckDBPyConnection", release_index: int, release: _AdmittedRelease
) -> None:
    for logical_list in LOGICAL_LIST_ORDER:
        parquet_path = release.canonical_dir / f"{logical_list}.parquet"
        view_name = f"r{release_index}_{logical_list.replace('-', '_')}"
        try:
            relation = connection.read_parquet(str(parquet_path))
            relation.create_view(view_name)
        except duckdb.Error as exc:
            raise EvidenceRepairError("internal.duckdb_failure") from exc


def _fetch_one(connection: "duckdb.DuckDBPyConnection", sql: str) -> tuple:
    try:
        row = connection.execute(sql).fetchone()
    except duckdb.Error as exc:
        raise EvidenceRepairError("internal.duckdb_failure") from exc
    if row is None:
        raise EvidenceRepairError("internal.duckdb_failure")
    return row


def _hash_ordered_keys(connection: "duckdb.DuckDBPyConnection", sql: str) -> str:
    """Stream the SQL-ordered nonblank key sequence into SHA-256.

    Fetches one row at a time from the DuckDB cursor -- the full ordered
    membership sequence is never materialized as a Python list, printed,
    or written anywhere; only the resulting digest ever leaves this
    function (D-11).
    """

    digest = hashlib.sha256()
    try:
        cursor = connection.execute(sql)
        while True:
            row = cursor.fetchone()
            if row is None:
                break
            digest.update(row[0].encode("utf-8"))
            digest.update(b"\n")
    except duckdb.Error as exc:
        raise EvidenceRepairError("internal.duckdb_failure") from exc
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Deterministic JSON serialization.
# --------------------------------------------------------------------------


def _dump_json(document: dict) -> bytes:
    text = json.dumps(document, separators=(",", ":"), ensure_ascii=True)
    return (text + "\n").encode("ascii")


def _write_new_file(path: Path, data: bytes) -> None:
    """Write `data` to a brand-new sibling-temp path, then `os.replace` it
    into place. Fails closed if `path` already exists (D-07 collision
    policy: this function never overwrites a prior artifact).
    """

    if path.exists() or path.is_symlink():
        raise EvidenceRepairError("output.collision")
    temp_path = path.parent / f".{path.name}.tmp-{os.getpid()}"
    fd = os.open(str(temp_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise EvidenceRepairError("output.write_failed") from exc
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


class _OutputLock:
    """Exclusive-create lock file serializing concurrent evidence runs
    against the same output directory (T-02-03). Fails closed rather than
    ever producing a torn/mixed output set.
    """

    def __init__(self, output_dir: Path) -> None:
        self._lock_path = output_dir / _LOCK_FILENAME

    def __enter__(self) -> "_OutputLock":
        try:
            fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError as exc:
            raise EvidenceRepairError("output.busy") from exc
        except OSError as exc:
            raise EvidenceRepairError("output.write_failed") from exc
        os.close(fd)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        try:
            self._lock_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


# --------------------------------------------------------------------------
# `derive`
# --------------------------------------------------------------------------


def _run_derive(args: argparse.Namespace) -> int:
    elapsed_ms = _parse_bounded_integer(
        args.first_admission_elapsed_ms,
        minimum=_RESOURCE_MIN,
        maximum=_ELAPSED_MS_MAX,
        code="resource.elapsed_ms",
    )
    peak_bytes = _parse_bounded_integer(
        args.first_admission_peak_temporary_disk_bytes,
        minimum=_RESOURCE_MIN,
        maximum=_PEAK_BYTES_MAX,
        code="resource.peak_bytes",
    )
    selections = _parse_release_selections(args.release)

    store_root = _resolve_existing_dir(args.store, code="store.invalid")
    reject_store_in_git_worktree_or_raise(store_root)

    # Resolved without creating anything yet -- the output directory is
    # only ever created once every upstream check below has already
    # passed, so a rejected run never leaves so much as an empty
    # directory behind (T-02-03 no-partial-output policy).
    output_dir_early = _resolve_path_without_requiring_existence(
        args.output_dir, code="output.invalid"
    )

    august_predecessor = _resolve_existing_file(
        args.august_predecessor, code="predecessor.invalid"
    )
    spike_001_predecessor = _resolve_existing_file(
        args.spike_001_predecessor, code="predecessor.invalid"
    )
    spike_002_predecessor = _resolve_existing_file(
        args.spike_002_predecessor, code="predecessor.invalid"
    )

    _reject_aliasing(
        {
            "store": store_root,
            "output": output_dir_early,
            "august_predecessor": august_predecessor,
            "spike_001_predecessor": spike_001_predecessor,
            "spike_002_predecessor": spike_002_predecessor,
        }
    )

    if output_dir_early.is_dir():
        for filename in _ALL_OUTPUT_FILENAMES:
            if (output_dir_early / filename).exists():
                raise EvidenceRepairError("output.collision")

    promoted = read_promoted_releases(store_root)
    releases = tuple(
        _resolve_admitted_release(store_root, promoted, selection) for selection in selections
    )

    parser_contract_versions = {release.parser_contract_version for release in releases}
    parquet_writer_versions = {release.parquet_writer_version for release in releases}
    if len(parser_contract_versions) != 1 or len(parquet_writer_versions) != 1:
        raise EvidenceRepairError("release.contract_mismatch")

    sql_blocks = _load_sql_blocks(_SQL_SCRIPT_PATH)

    with duckdb.connect(":memory:") as connection:
        connection.execute("SET threads = 1")
        for index, release in enumerate(releases):
            _register_release_views(connection, index, release)
        for index in range(3):
            try:
                connection.execute(sql_blocks[f"create_population_release{index}"])
            except duckdb.Error as exc:
                raise EvidenceRepairError("internal.duckdb_failure") from exc

        totals = []
        for index in range(3):
            total_count, keyed_count, delinquent_count = _fetch_one(
                connection, sql_blocks[f"totals_release{index}"]
            )
            totals.append(
                {
                    "total_count": total_count,
                    "keyed_count": keyed_count,
                    "keyless_count": total_count - keyed_count,
                    "delinquent_count": delinquent_count,
                }
            )

        membership_hashes = [
            _hash_ordered_keys(connection, sql_blocks["membership_release0"]),
            _hash_ordered_keys(connection, sql_blocks["membership_release1"]),
        ]

        (exit_count,) = _fetch_one(connection, sql_blocks["exit_count_release0_to_release1"])

    generated_at_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    documents = _build_documents(
        releases=releases,
        totals=totals,
        membership_hashes=membership_hashes,
        exit_count=exit_count,
        elapsed_ms=elapsed_ms,
        peak_bytes=peak_bytes,
        august_predecessor=august_predecessor,
        spike_001_predecessor=spike_001_predecessor,
        spike_002_predecessor=spike_002_predecessor,
        generated_at_utc=generated_at_utc,
    )

    output_dir = _resolve_or_create_output_dir(args.output_dir)
    with _OutputLock(output_dir):
        for filename in _ALL_OUTPUT_FILENAMES:
            if (output_dir / filename).exists():
                raise EvidenceRepairError("output.collision")
        for filename in _ALL_OUTPUT_FILENAMES:
            _write_new_file(output_dir / filename, documents[filename])

    return 0


def reject_store_in_git_worktree_or_raise(store_root: Path) -> None:
    try:
        reject_store_in_git_worktree(store_root)
    except CandidateError as exc:
        raise EvidenceRepairError("store.in_git_worktree") from exc


def _resolve_or_create_output_dir(raw_path: str) -> Path:
    raw = Path(raw_path)
    if raw.is_symlink():
        raise EvidenceRepairError("output.invalid")
    try:
        raw.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EvidenceRepairError("output.invalid") from exc
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise EvidenceRepairError("output.invalid") from exc
    if not resolved.is_dir():
        raise EvidenceRepairError("output.invalid")
    return resolved


def _build_documents(
    *,
    releases: tuple[_AdmittedRelease, ...],
    totals: list[dict[str, int]],
    membership_hashes: list[str],
    exit_count: int,
    elapsed_ms: int,
    peak_bytes: int,
    august_predecessor: Path,
    spike_001_predecessor: Path,
    spike_002_predecessor: Path,
    generated_at_utc: str,
) -> dict[str, bytes]:
    august_release = releases[1]
    august_totals = {
        logical_list: august_release.logical_lists[logical_list]["parquet_row_count"]
        for logical_list in LOGICAL_LIST_ORDER
    }
    august_document = {
        "schema_version": "august-manifest-successor-v1",
        "as_of_date": august_release.as_of_date,
        "release_revision": august_release.release_revision,
        "logical_list_totals": august_totals,
        "release_total": sum(august_totals.values()),
        "status": _CLAIM_STATUS_CORRECTED,
    }

    july_release = releases[0]
    july_totals = {
        logical_list: july_release.logical_lists[logical_list]["parquet_row_count"]
        for logical_list in LOGICAL_LIST_ORDER
    }
    spike_001_document = {
        "schema_version": "spike-001-successor-v1",
        "as_of_date": july_release.as_of_date,
        "release_revision": july_release.release_revision,
        "logical_list_totals": july_totals,
        "release_total": sum(july_totals.values()),
        "embedded_newline_explanation_retracted": True,
        "status": _CLAIM_STATUS_CORRECTED,
    }

    spike_002_document = {
        "schema_version": "spike-002-successor-v1",
        "coverage": [
            {
                "as_of_date": releases[0].as_of_date,
                "release_revision": releases[0].release_revision,
                "total_row_count": totals[0]["total_count"],
                "keyed_row_count": totals[0]["keyed_count"],
                "keyless_row_count": totals[0]["keyless_count"],
                "delinquent_row_count": totals[0]["delinquent_count"],
                "coverage_status": _CLAIM_STATUS_CORRECTED,
            },
            {
                "as_of_date": releases[1].as_of_date,
                "release_revision": releases[1].release_revision,
                "total_row_count": totals[1]["total_count"],
                "keyed_row_count": totals[1]["keyed_count"],
                "keyless_row_count": totals[1]["keyless_count"],
                "delinquent_row_count": totals[1]["delinquent_count"],
                "coverage_status": _CLAIM_STATUS_CORRECTED,
            },
        ],
        "keyed_membership": [
            {
                "as_of_date": releases[0].as_of_date,
                "release_revision": releases[0].release_revision,
                "sha256": membership_hashes[0],
                "membership_status": _CLAIM_STATUS_CONFIRMED,
            },
            {
                "as_of_date": releases[1].as_of_date,
                "release_revision": releases[1].release_revision,
                "sha256": membership_hashes[1],
                "membership_status": _CLAIM_STATUS_CONFIRMED,
            },
        ],
        "transition_confirmation": {
            "from_as_of_date": releases[0].as_of_date,
            "to_as_of_date": releases[1].as_of_date,
            "exit_count": exit_count,
            "status": _CLAIM_STATUS_CONFIRMED,
        },
    }

    august_bytes = _dump_json(august_document)
    spike_001_bytes = _dump_json(spike_001_document)
    spike_002_bytes = _dump_json(spike_002_document)

    correction_index = {
        "schema_version": SCHEMA_VERSION,
        "derivation": {
            "command_id": _COMMAND_ID,
            "generated_at_utc": generated_at_utc,
            "parser_contract_version": releases[0].parser_contract_version,
            "parquet_writer_version": releases[0].parquet_writer_version,
            "source_release_fingerprints": [
                {
                    "as_of_date": release.as_of_date,
                    "release_revision": release.release_revision,
                    "revision_fingerprint": release.revision_fingerprint,
                    "manifest_sha256": release.manifest_sha256,
                }
                for release in releases
            ],
            "resource_measurements": {
                "first_admission_elapsed_ms": elapsed_ms,
                "first_admission_peak_temporary_disk_bytes": peak_bytes,
            },
        },
        "corrections": [
            {
                "successor_file": _AUGUST_SUCCESSOR_FILENAME,
                "supersedes": {
                    "private_path": _PRIVATE_PATH_AUGUST,
                    "predecessor_sha256": _hash_file(august_predecessor),
                },
                "successor_sha256": _hash_bytes(august_bytes),
            },
            {
                "successor_file": _SPIKE_001_SUCCESSOR_FILENAME,
                "supersedes": {
                    "private_path": _PRIVATE_PATH_SPIKE_001,
                    "predecessor_sha256": _hash_file(spike_001_predecessor),
                },
                "successor_sha256": _hash_bytes(spike_001_bytes),
            },
            {
                "successor_file": _SPIKE_002_SUCCESSOR_FILENAME,
                "supersedes": {
                    "private_path": _PRIVATE_PATH_SPIKE_002,
                    "predecessor_sha256": _hash_file(spike_002_predecessor),
                },
                "successor_sha256": _hash_bytes(spike_002_bytes),
            },
        ],
        "gate_a_evidence": {
            "private_path": _PRIVATE_PATH_GATE_A_EVIDENCE,
            "sha256": _GATE_A_EVIDENCE_SHA256,
            "status": "unchanged",
        },
    }

    return {
        _AUGUST_SUCCESSOR_FILENAME: august_bytes,
        _SPIKE_001_SUCCESSOR_FILENAME: spike_001_bytes,
        _SPIKE_002_SUCCESSOR_FILENAME: spike_002_bytes,
        _CORRECTION_INDEX_FILENAME: _dump_json(correction_index),
    }


# --------------------------------------------------------------------------
# `verify`
# --------------------------------------------------------------------------


def _require_exact_keys(document: object, keys: tuple[str, ...], *, code: str) -> dict:
    if not isinstance(document, dict) or set(document.keys()) != set(keys):
        raise EvidenceRepairError(code)
    return document


def _validate_correction_index_schema(document: object) -> dict:
    document = _require_exact_keys(
        document, _CORRECTION_INDEX_TOP_LEVEL_KEYS, code="verify.schema_invalid"
    )
    if document["schema_version"] != SCHEMA_VERSION:
        raise EvidenceRepairError("verify.schema_invalid")

    derivation = _require_exact_keys(
        document["derivation"], _DERIVATION_KEYS, code="verify.schema_invalid"
    )
    if not isinstance(derivation["command_id"], str) or not derivation["command_id"]:
        raise EvidenceRepairError("verify.schema_invalid")
    if not isinstance(derivation["generated_at_utc"], str) or not _UTC_TIMESTAMP_PATTERN.match(
        derivation["generated_at_utc"]
    ):
        raise EvidenceRepairError("verify.schema_invalid")
    _require_positive_int(derivation["parser_contract_version"])
    if (
        not isinstance(derivation["parquet_writer_version"], str)
        or not derivation["parquet_writer_version"]
    ):
        raise EvidenceRepairError("verify.schema_invalid")

    fingerprints = derivation["source_release_fingerprints"]
    if not isinstance(fingerprints, list) or len(fingerprints) != 3:
        raise EvidenceRepairError("verify.schema_invalid")
    previous_date = None
    for fingerprint_entry in fingerprints:
        entry = _require_exact_keys(
            fingerprint_entry, _SOURCE_FINGERPRINT_KEYS, code="verify.schema_invalid"
        )
        if not isinstance(entry["as_of_date"], str) or not _AS_OF_DATE_PATTERN.match(
            entry["as_of_date"]
        ):
            raise EvidenceRepairError("verify.schema_invalid")
        if previous_date is not None and entry["as_of_date"] <= previous_date:
            raise EvidenceRepairError("verify.schema_invalid")
        previous_date = entry["as_of_date"]
        _require_positive_int(entry["release_revision"])
        if not isinstance(entry["revision_fingerprint"], str) or not _FINGERPRINT_PATTERN.match(
            entry["revision_fingerprint"]
        ):
            raise EvidenceRepairError("verify.schema_invalid")
        if not isinstance(entry["manifest_sha256"], str) or not _FINGERPRINT_PATTERN.match(
            entry["manifest_sha256"]
        ):
            raise EvidenceRepairError("verify.schema_invalid")

    resource_measurements = _require_exact_keys(
        derivation["resource_measurements"], _RESOURCE_MEASUREMENT_KEYS, code="verify.schema_invalid"
    )
    elapsed_ms = resource_measurements["first_admission_elapsed_ms"]
    peak_bytes = resource_measurements["first_admission_peak_temporary_disk_bytes"]
    if (
        not isinstance(elapsed_ms, int)
        or isinstance(elapsed_ms, bool)
        or not (_RESOURCE_MIN <= elapsed_ms <= _ELAPSED_MS_MAX)
    ):
        raise EvidenceRepairError("verify.schema_invalid")
    if (
        not isinstance(peak_bytes, int)
        or isinstance(peak_bytes, bool)
        or not (_RESOURCE_MIN <= peak_bytes <= _PEAK_BYTES_MAX)
    ):
        raise EvidenceRepairError("verify.schema_invalid")

    corrections = document["corrections"]
    if not isinstance(corrections, list) or len(corrections) != 3:
        raise EvidenceRepairError("verify.schema_invalid")
    expected_order = (
        _AUGUST_SUCCESSOR_FILENAME,
        _SPIKE_001_SUCCESSOR_FILENAME,
        _SPIKE_002_SUCCESSOR_FILENAME,
    )
    for expected_filename, correction_entry in zip(expected_order, corrections):
        entry = _require_exact_keys(
            correction_entry, _CORRECTION_ENTRY_KEYS, code="verify.schema_invalid"
        )
        if entry["successor_file"] != expected_filename:
            raise EvidenceRepairError("verify.schema_invalid")
        supersedes = _require_exact_keys(
            entry["supersedes"], _SUPERSEDES_KEYS, code="verify.schema_invalid"
        )
        if not isinstance(supersedes["private_path"], str) or not supersedes["private_path"]:
            raise EvidenceRepairError("verify.schema_invalid")
        if not isinstance(
            supersedes["predecessor_sha256"], str
        ) or not _FINGERPRINT_PATTERN.match(supersedes["predecessor_sha256"]):
            raise EvidenceRepairError("verify.schema_invalid")
        if not isinstance(entry["successor_sha256"], str) or not _FINGERPRINT_PATTERN.match(
            entry["successor_sha256"]
        ):
            raise EvidenceRepairError("verify.schema_invalid")

    gate_a_evidence = _require_exact_keys(
        document["gate_a_evidence"], _GATE_A_EVIDENCE_KEYS, code="verify.schema_invalid"
    )
    if gate_a_evidence["status"] != "unchanged":
        raise EvidenceRepairError("verify.schema_invalid")
    if not isinstance(gate_a_evidence["private_path"], str) or not gate_a_evidence["private_path"]:
        raise EvidenceRepairError("verify.schema_invalid")
    if not isinstance(gate_a_evidence["sha256"], str) or not _FINGERPRINT_PATTERN.match(
        gate_a_evidence["sha256"]
    ):
        raise EvidenceRepairError("verify.schema_invalid")

    return document


def _run_verify(args: argparse.Namespace) -> int:
    artifacts_dir = _resolve_existing_dir(args.artifacts, code="verify.artifacts_invalid")

    predecessor_paths = {
        _PRIVATE_PATH_AUGUST: _resolve_existing_file(
            args.august_predecessor, code="predecessor.invalid"
        ),
        _PRIVATE_PATH_SPIKE_001: _resolve_existing_file(
            args.spike_001_predecessor, code="predecessor.invalid"
        ),
        _PRIVATE_PATH_SPIKE_002: _resolve_existing_file(
            args.spike_002_predecessor, code="predecessor.invalid"
        ),
    }

    index_path = artifacts_dir / _CORRECTION_INDEX_FILENAME
    if index_path.is_symlink() or not index_path.is_file():
        raise EvidenceRepairError("verify.schema_invalid")
    try:
        index_document = json.loads(index_path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceRepairError("verify.schema_invalid") from exc

    index_document = _validate_correction_index_schema(index_document)

    for correction_entry in index_document["corrections"]:
        successor_path = artifacts_dir / correction_entry["successor_file"]
        if successor_path.is_symlink() or not successor_path.is_file():
            raise EvidenceRepairError("verify.successor_missing")
        if _hash_file(successor_path) != correction_entry["successor_sha256"]:
            raise EvidenceRepairError("verify.hash_mismatch")

        expected_private_path = correction_entry["supersedes"]["private_path"]
        predecessor_path = predecessor_paths.get(expected_private_path)
        if predecessor_path is None:
            raise EvidenceRepairError("verify.lineage_mismatch")
        if _hash_file(predecessor_path) != correction_entry["supersedes"]["predecessor_sha256"]:
            raise EvidenceRepairError("verify.hash_mismatch")

    if index_document["gate_a_evidence"]["sha256"] != _GATE_A_EVIDENCE_SHA256:
        raise EvidenceRepairError("verify.hash_mismatch")

    if args.store is not None:
        store_root = _resolve_existing_dir(args.store, code="store.invalid")
        reject_store_in_git_worktree_or_raise(store_root)
        promoted = read_promoted_releases(store_root)
        for fingerprint_entry in index_document["derivation"]["source_release_fingerprints"]:
            selection = _ReleaseSelection(
                as_of_date=fingerprint_entry["as_of_date"],
                release_revision=fingerprint_entry["release_revision"],
            )
            release = _resolve_admitted_release(store_root, promoted, selection)
            if (
                release.revision_fingerprint != fingerprint_entry["revision_fingerprint"]
                or release.manifest_sha256 != fingerprint_entry["manifest_sha256"]
            ):
                raise EvidenceRepairError("verify.store_drift")

    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools.evidence_repair",
        description="Derive or verify safe Gate A evidence successors from admitted releases.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    derive_parser = subparsers.add_parser("derive", help="Derive the four safe evidence successors.")
    derive_parser.add_argument("--store", required=True)
    derive_parser.add_argument("--release", action="append", default=[])
    derive_parser.add_argument("--august-predecessor", required=True)
    derive_parser.add_argument("--spike-001-predecessor", required=True)
    derive_parser.add_argument("--spike-002-predecessor", required=True)
    derive_parser.add_argument("--output-dir", required=True)
    derive_parser.add_argument(
        "--first-admission-elapsed-ms", action="append", default=[], dest="first_admission_elapsed_ms"
    )
    derive_parser.add_argument(
        "--first-admission-peak-temporary-disk-bytes",
        action="append",
        default=[],
        dest="first_admission_peak_temporary_disk_bytes",
    )

    verify_parser = subparsers.add_parser("verify", help="Verify an existing evidence artifact set.")
    verify_parser.add_argument("--artifacts", required=True)
    verify_parser.add_argument("--store", default=None)
    verify_parser.add_argument("--august-predecessor", required=True)
    verify_parser.add_argument("--spike-001-predecessor", required=True)
    verify_parser.add_argument("--spike-002-predecessor", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "derive":
            return _run_derive(args)
        return _run_verify(args)
    except EvidenceRepairError as exc:
        print(f"evidence-repair: {exc.code}", file=sys.stderr)
        return 1
    except (StoreError, CandidateError):
        print("evidence-repair: store_error", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 -- fixed safe message only; never the exception object
        print("evidence-repair: internal_error", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
