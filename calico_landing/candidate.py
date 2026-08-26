"""Safe candidate manifest resolution and copy-once byte staging (D-05/D-06/D-07).

Loads a strict, closed four-object candidate manifest, resolves every
relative payload path beneath its own manifest root (rejecting absolute
paths, `..` segments, symlink/reparse aliases, and duplicate resolved or
hard-linked targets), and streams each approved payload exactly once into a
unique staging directory while computing its SHA-256 and byte count. No
later stage ever reopens or reinterprets the caller-supplied source path --
`calico_landing.parser` and `calico_landing.parquet` only ever see the
staged copy this module writes (closing the time-of-check/time-of-use gap,
T-02-02/T-02-04).

An in-repository candidate is permitted only when it resolves beneath the
exact committed `tests/fixtures/landing` prefix of its own Git worktree
(identity-free synthetic fixtures only, D-10); a store root is never
permitted inside any Git worktree, regardless of subpath (T-02-07).

Every failure crosses this module's boundary as a `CandidateError` carrying
only a fixed safe `code` (drawn from `calico_landing.result.REASON_RANK`),
an optional `logical_list` identifier, and no offending path, byte, or
manifest value -- mirrored from `calico_landing.parser.StructuralReject`
and `calico_landing.parquet.CanonicalSerializationError`'s non-echo
exception shape, so `calico_landing.admission` can construct an
`AdmissionReason` directly from any caught instance.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from calico_landing.contracts import CsvContract, LOGICAL_LIST_ORDER

#: The closed candidate-manifest filename this module always looks for
#: directly beneath the caller-supplied candidate root.
MANIFEST_FILENAME = "candidate-set.json"

_MANIFEST_TOP_LEVEL_KEYS = frozenset({"manifest_version", "objects"})
_MANIFEST_OBJECT_KEYS = frozenset({"relative_path", "content_length"})
_SUPPORTED_MANIFEST_VERSION = 1

#: The exact committed synthetic-fixture prefix an in-repository candidate
#: root must resolve beneath (D-10). No other in-worktree location is ever
#: permitted, regardless of naming.
_FIXTURE_ROOT_SEGMENTS = ("tests", "fixtures", "landing")

_COPY_CHUNK_BYTES = 1024 * 1024
_CARRIAGE_RETURN = 0x0D
_LINE_FEED = 0x0A


class CandidateError(Exception):
    """Raised on any candidate mapping, transfer, or container failure.

    Carries only a fixed safe `code` and an optional `logical_list`
    identifier -- never the offending path, byte content, or manifest
    value (D-05/D-10 non-echo discipline).
    """

    def __init__(self, code: str, *, logical_list: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.logical_list = logical_list


@dataclass(frozen=True)
class StagedCandidateObject:
    """Safe metadata for one candidate payload staged exactly once.

    Never carries a row or field value -- only the logical identity, the
    staged copy's own path, and its content hash/byte count.
    """

    logical_list: str
    staged_path: Path
    sha256: str
    byte_count: int


def _find_git_root(start: Path) -> Path | None:
    current = start
    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def reject_store_in_git_worktree(store_root: str | Path) -> None:
    """Fail closed if `store_root` resolves inside any Git worktree (D-11).

    Raises `CandidateError` without ever naming the rejected path.
    """

    raw = Path(store_root)
    try:
        resolved = raw.resolve(strict=False)
    except OSError as exc:
        raise CandidateError("candidate.invalid_mapping") from exc
    if _find_git_root(resolved) is not None:
        raise CandidateError("candidate.invalid_mapping")


def _require_fixture_containment(candidate_root: Path) -> None:
    git_root = _find_git_root(candidate_root)
    if git_root is None:
        return
    fixture_root = git_root.joinpath(*_FIXTURE_ROOT_SEGMENTS).resolve(strict=False)
    try:
        candidate_root.relative_to(fixture_root)
    except ValueError as exc:
        raise CandidateError("candidate.invalid_mapping") from exc


def _read_manifest_document(manifest_path: Path) -> dict[str, object]:
    if manifest_path.is_symlink():
        raise CandidateError("candidate.invalid_mapping")
    try:
        raw_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise CandidateError("candidate.invalid_mapping") from exc

    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateError("candidate.invalid_mapping") from exc

    if not isinstance(document, dict) or set(document.keys()) != _MANIFEST_TOP_LEVEL_KEYS:
        raise CandidateError("candidate.invalid_mapping")
    if document.get("manifest_version") != _SUPPORTED_MANIFEST_VERSION:
        raise CandidateError("candidate.invalid_mapping")

    objects = document.get("objects")
    if not isinstance(objects, dict) or set(objects.keys()) != set(LOGICAL_LIST_ORDER):
        raise CandidateError("candidate.invalid_mapping")

    for logical_list in LOGICAL_LIST_ORDER:
        entry = objects[logical_list]
        if not isinstance(entry, dict) or set(entry.keys()) != _MANIFEST_OBJECT_KEYS:
            raise CandidateError("candidate.invalid_mapping", logical_list=logical_list)

    return objects


def _validate_relative_path(raw_path: object, logical_list: str) -> str:
    if not isinstance(raw_path, str) or not raw_path:
        raise CandidateError("candidate.invalid_mapping", logical_list=logical_list)

    normalized = raw_path.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized:
        raise CandidateError("candidate.invalid_mapping", logical_list=logical_list)

    parts = [part for part in normalized.split("/") if part != "."]
    if not parts or any(part in ("", "..") for part in parts):
        raise CandidateError("candidate.invalid_mapping", logical_list=logical_list)

    lowered = raw_path.lower()
    if lowered.endswith(".xlsx"):
        raise CandidateError("contract.unsupported_xlsx", logical_list=logical_list)
    if not lowered.endswith(".csv"):
        raise CandidateError("candidate.invalid_mapping", logical_list=logical_list)

    return raw_path


def _validate_content_length(raw_value: object, logical_list: str) -> int | None:
    if raw_value is None:
        return None
    if not isinstance(raw_value, int) or isinstance(raw_value, bool) or raw_value < 0:
        raise CandidateError("candidate.invalid_mapping", logical_list=logical_list)
    return raw_value


def _resolve_object_path(
    candidate_root: Path,
    relative_path: str,
    logical_list: str,
    seen_real_paths: set[Path],
) -> Path:
    current = candidate_root
    for part in Path(relative_path).parts:
        current = current / part
        if current.is_symlink():
            raise CandidateError("candidate.invalid_mapping", logical_list=logical_list)

    try:
        resolved = current.resolve(strict=True)
    except OSError as exc:
        raise CandidateError("candidate.invalid_mapping", logical_list=logical_list) from exc

    if resolved != current:
        # Some ancestor directory resolved to a different real path than its
        # literal name implies (a reparse-point alias `is_symlink()` above
        # did not already catch); refuse it rather than trust the alias.
        raise CandidateError("candidate.invalid_mapping", logical_list=logical_list)

    try:
        resolved.relative_to(candidate_root)
    except ValueError as exc:
        raise CandidateError("candidate.invalid_mapping", logical_list=logical_list) from exc

    if not resolved.is_file():
        raise CandidateError("candidate.invalid_mapping", logical_list=logical_list)

    if resolved in seen_real_paths:
        raise CandidateError("candidate.invalid_mapping", logical_list=logical_list)
    seen_real_paths.add(resolved)

    return resolved


def _stage_object(
    resolved_source: Path,
    logical_list: str,
    staging_dir: Path,
    contract: CsvContract,
    expected_content_length: int | None,
) -> StagedCandidateObject:
    staged_path = staging_dir / f"{logical_list}.csv"
    digest = hashlib.sha256()
    byte_count = 0
    current_line_bytes = 0

    try:
        with open(resolved_source, "rb") as source_handle, open(staged_path, "wb") as dest_handle:
            while True:
                chunk = source_handle.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break

                byte_count += len(chunk)
                if (
                    byte_count > contract.max_decompressed_payload_bytes
                    or byte_count > contract.max_compressed_payload_bytes
                ):
                    raise CandidateError("container.open_failed", logical_list=logical_list)

                for one_byte in chunk:
                    if one_byte in (_CARRIAGE_RETURN, _LINE_FEED):
                        current_line_bytes = 0
                        continue
                    current_line_bytes += 1
                    if current_line_bytes > contract.max_physical_line_bytes:
                        raise CandidateError("container.open_failed", logical_list=logical_list)

                digest.update(chunk)
                dest_handle.write(chunk)
            dest_handle.flush()
    except OSError as exc:
        staged_path.unlink(missing_ok=True)
        raise CandidateError("container.open_failed", logical_list=logical_list) from exc
    except CandidateError:
        staged_path.unlink(missing_ok=True)
        raise

    if expected_content_length is not None and byte_count != expected_content_length:
        staged_path.unlink(missing_ok=True)
        raise CandidateError("transfer.length_mismatch", logical_list=logical_list)

    return StagedCandidateObject(
        logical_list=logical_list,
        staged_path=staged_path,
        sha256=digest.hexdigest(),
        byte_count=byte_count,
    )


def resolve_and_stage_candidate(
    candidate_input: str | Path,
    staging_dir: str | Path,
    contract: CsvContract,
) -> dict[str, StagedCandidateObject]:
    """Resolve, verify, and copy-once-stage the complete four-object candidate set.

    `candidate_input` must be a directory directly containing
    `candidate-set.json` and every payload its closed manifest maps to.
    `staging_dir` is a caller-owned directory (already created) that
    receives exactly four staged copies, named `<logical_list>.csv`.

    Fails closed on the first structural problem found -- missing/malformed
    manifest, a path outside the candidate root, a symlink/reparse alias, a
    duplicate resolved target, an unapproved extension (`.xlsx` dispatches
    to `contract.unsupported_xlsx` before any CSV parsing is attempted), a
    resource-ceiling breach while copying, or a `Content-Length` mismatch.
    Raises `CandidateError`; never returns a partially staged mapping.
    """

    candidate_root = Path(candidate_input)
    if candidate_root.is_symlink():
        raise CandidateError("candidate.invalid_mapping")
    try:
        candidate_root = candidate_root.resolve(strict=True)
    except OSError as exc:
        raise CandidateError("candidate.invalid_mapping") from exc
    if not candidate_root.is_dir():
        raise CandidateError("candidate.invalid_mapping")

    _require_fixture_containment(candidate_root)

    objects = _read_manifest_document(candidate_root / MANIFEST_FILENAME)

    staging_dir = Path(staging_dir)
    staged: dict[str, StagedCandidateObject] = {}
    seen_real_paths: set[Path] = set()
    seen_inodes: set[tuple[int, int]] = set()

    for logical_list in LOGICAL_LIST_ORDER:
        entry = objects[logical_list]
        relative_path = _validate_relative_path(entry.get("relative_path"), logical_list)
        content_length = _validate_content_length(entry.get("content_length"), logical_list)

        resolved_source = _resolve_object_path(
            candidate_root, relative_path, logical_list, seen_real_paths
        )

        try:
            stat_result = resolved_source.stat()
        except OSError as exc:
            raise CandidateError("candidate.invalid_mapping", logical_list=logical_list) from exc

        inode_key = (stat_result.st_dev, stat_result.st_ino)
        if inode_key != (0, 0) and inode_key in seen_inodes:
            raise CandidateError("candidate.invalid_mapping", logical_list=logical_list)
        seen_inodes.add(inode_key)

        staged[logical_list] = _stage_object(
            resolved_source, logical_list, staging_dir, contract, content_length
        )

    return staged


__all__ = [
    "CandidateError",
    "StagedCandidateObject",
    "MANIFEST_FILENAME",
    "reject_store_in_git_worktree",
    "resolve_and_stage_candidate",
]
