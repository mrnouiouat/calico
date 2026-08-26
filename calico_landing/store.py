"""Recoverable, cross-platform atomic revision store (D-07/D-08/D-09).

Owns the two-operation commit that makes a release visible: an immutable
same-store directory rename publishes one release revision, and a flushed,
fsynced, sibling-temp `os.replace` atomically selects the one promoted
revision per as-of date. These are two individually atomic filesystem
operations, never one fictional cross-file transaction -- a crash between
them is recoverable, and this module's own re-entrant `commit_revision`
call is the recovery path (mirrors `02-RESEARCH.md` Pattern 3).

Store layout, all rooted at a caller-supplied external store directory that
must never be inside a Git worktree (D-11):

    <store>/.admission.lock            -- cross-platform writer lock file
    <store>/.staging/<run-id>/...      -- caller-built staged revision dirs
    <store>/attempts/<attempt-id>.json -- best-effort safe attempt trail
    <store>/releases/<date>/rev-NNNN-<fingerprint-prefix>/...
    <store>/promoted-releases.json     -- closed-schema promotion pointer

This module never opens, parses, or echoes a raw row, source field, or
absolute caller path -- every failure crosses its boundary as one fixed
safe `StoreError` category (mirrored from `tools/privacy_scan/git_objects.py`
and `calico_landing.parquet`'s value-free exception discipline). It never
deletes a caller-supplied root; the only directory it ever removes is the
one resolved, contained staging child passed to the current call once that
staged content turns out to be redundant (D-07's "cleanup only the staging
directory" boundary).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

#: Fixed store layout names (`the agent's discretion`, 02-CONTEXT.md).
_LOCK_FILENAME = ".admission.lock"
_STAGING_DIRNAME = ".staging"
_ATTEMPTS_DIRNAME = "attempts"
_RELEASES_DIRNAME = "releases"
_POINTER_FILENAME = "promoted-releases.json"

_POINTER_SCHEMA_VERSION = 1
_MANIFEST_SCHEMA_VERSION = 1
_ATTEMPT_SCHEMA_VERSION = 1

#: Locked D-08 fingerprint algorithm identifier (02-RESEARCH.md "Revision
#: Fingerprint"). Recorded in every revision manifest, never inferred.
_FINGERPRINT_ALGORITHM = "ordered-source-sha256-json-v1"

_AS_OF_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REVISION_DIR_PATTERN = re.compile(r"^rev-(\d{4,})-([0-9a-f]{8})$")

_POINTER_TOP_LEVEL_KEYS = frozenset({"schema_version", "promotions"})
_POINTER_ENTRY_KEYS = frozenset({"release_revision", "revision_fingerprint", "revision_dir"})
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

_LOCK_RETRY_TIMEOUT_SECONDS = 5.0
_LOCK_RETRY_INTERVAL_SECONDS = 0.05


class StoreError(Exception):
    """Raised on any store failure. Carries only a fixed safe `category` --
    never a path, raw value, or exception text (mirrors
    `tools/privacy_scan/git_objects.py`'s `GitObjectError`).
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class StoreLayout:
    """Resolved, established store paths. `staging_root` is the correct
    `dir=` argument for `tempfile.mkdtemp` when a caller builds a new
    staged revision directory.
    """

    store_root: Path
    staging_root: Path


@dataclass(frozen=True)
class PromotedRevision:
    """One closed-schema entry from `promoted-releases.json` (D-09)."""

    release_revision: int
    revision_fingerprint: str
    revision_dir: str


@dataclass(frozen=True)
class RevisionCommit:
    """Safe outcome metadata for one `commit_revision` call (D-08).

    `status` is `"accepted"` or `"no_new_release"`; this module never
    returns a rejection -- staged content is assumed to have already passed
    every structural check before reaching the store. `recovered` is `True`
    only when this call completed a promotion left unfinished by an earlier
    process that renamed a revision directory and then crashed before the
    pointer replacement.
    """

    status: str
    as_of_date: str
    release_revision: int
    revision_fingerprint: str
    recovered: bool


def _validate_as_of_date(as_of_date: str) -> None:
    if not isinstance(as_of_date, str) or not _AS_OF_DATE_PATTERN.match(as_of_date):
        raise StoreError("store.invalid_as_of_date")
    try:
        date.fromisoformat(as_of_date)
    except ValueError as exc:
        raise StoreError("store.invalid_as_of_date") from exc


def _validate_fingerprint(revision_fingerprint: str) -> None:
    if not isinstance(revision_fingerprint, str) or not _FINGERPRINT_PATTERN.match(
        revision_fingerprint
    ):
        raise StoreError("store.invalid_fingerprint")


def _resolve_store_root(store_root: str | Path) -> Path:
    raw = Path(store_root)
    if raw.is_symlink():
        raise StoreError("store.link_rejected")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise StoreError("store.invalid_store_root") from exc
    if not resolved.is_dir():
        raise StoreError("store.invalid_store_root")
    return resolved


def _ensure_staging_root(store_root: Path) -> Path:
    staging_root = store_root / _STAGING_DIRNAME
    try:
        staging_root.mkdir(parents=True, exist_ok=True)
        (store_root / _ATTEMPTS_DIRNAME).mkdir(parents=True, exist_ok=True)
        (store_root / _RELEASES_DIRNAME).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StoreError("store.permission_denied") from exc
    if staging_root.is_symlink():
        raise StoreError("store.link_rejected")
    return staging_root.resolve(strict=True)


def ensure_store_layout(store_root: str | Path) -> StoreLayout:
    """Idempotently establish and return the resolved store/staging roots.

    Rejects a symlinked/reparse-point store root and fails closed with
    `StoreError("store.permission_denied")` if the fixed layout directories
    cannot be created.
    """

    resolved_store_root = _resolve_store_root(store_root)
    staging_root = _ensure_staging_root(resolved_store_root)
    return StoreLayout(store_root=resolved_store_root, staging_root=staging_root)


def _resolve_staged_revision_dir(staged_revision_dir: str | Path, staging_root: Path) -> Path:
    raw = Path(staged_revision_dir)
    if raw.is_symlink():
        raise StoreError("store.link_rejected")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise StoreError("store.invalid_staging_root") from exc
    if not resolved.is_dir():
        raise StoreError("store.invalid_staging_root")
    if resolved.parent != staging_root:
        raise StoreError("store.invalid_staging_root")
    return resolved


def _require_same_filesystem(store_root: Path, staged_dir: Path) -> None:
    try:
        store_stat = os.stat(store_root)
        staged_stat = os.stat(staged_dir)
    except OSError as exc:
        raise StoreError("store.invalid_store_root") from exc
    if store_stat.st_dev != staged_stat.st_dev:
        raise StoreError("store.cross_filesystem")


def _fsync_dir_best_effort(path: Path) -> None:
    """POSIX-only best-effort directory fsync after a rename/replace.

    Windows has no equivalent portable directory-fsync guarantee, so this
    is a no-op there (02-RESEARCH.md Pitfall 6); failures are swallowed
    because this is durability hardening, not the primary atomicity proof.
    """

    if os.name == "nt":
        return
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _lock_bytes(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_bytes(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class _StoreLock:
    """One nonblocking cross-platform exclusive writer lock, retried with a
    bounded spin-wait so genuinely concurrent writers serialize instead of
    failing on ordinary scheduling jitter (`fcntl.flock` / `msvcrt.locking`,
    02-RESEARCH.md Standard Stack). Exhausting the retry budget fails closed
    with the locked D-05 `store.busy` reason code.
    """

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._handle = None

    def __enter__(self) -> "_StoreLock":
        try:
            handle = open(self._lock_path, "a+b")
        except OSError as exc:
            raise StoreError("store.lock_failed") from exc

        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
        except OSError as exc:
            handle.close()
            raise StoreError("store.lock_failed") from exc

        deadline = time.monotonic() + _LOCK_RETRY_TIMEOUT_SECONDS
        while True:
            try:
                _lock_bytes(handle)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise StoreError("store.busy")
                time.sleep(_LOCK_RETRY_INTERVAL_SECONDS)

        self._handle = handle
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if self._handle is not None:
            try:
                _unlock_bytes(self._handle)
            except OSError:
                pass
            finally:
                self._handle.close()
                self._handle = None
        return False


@dataclass(frozen=True)
class _ExistingRevision:
    revision_number: int
    fingerprint: str
    dir_path: Path


def _read_manifest(manifest_path: Path) -> dict:
    if manifest_path.is_symlink():
        raise StoreError("store.link_rejected")
    try:
        raw_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise StoreError("store.malformed_manifest") from exc
    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StoreError("store.malformed_manifest") from exc
    if not isinstance(document, dict) or set(document.keys()) != _MANIFEST_TOP_LEVEL_KEYS:
        raise StoreError("store.malformed_manifest")
    if document.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
        raise StoreError("store.malformed_manifest")
    fingerprint = document.get("revision_fingerprint")
    if not isinstance(fingerprint, str) or not _FINGERPRINT_PATTERN.match(fingerprint):
        raise StoreError("store.malformed_manifest")
    return document


def _list_revisions_for_date(store_root: Path, as_of_date: str) -> list[_ExistingRevision]:
    date_dir = store_root / _RELEASES_DIRNAME / as_of_date
    if not date_dir.exists():
        return []
    if date_dir.is_symlink():
        raise StoreError("store.link_rejected")

    revisions: list[_ExistingRevision] = []
    for child in date_dir.iterdir():
        if child.is_symlink() or not child.is_dir():
            continue
        match = _REVISION_DIR_PATTERN.match(child.name)
        if match is None:
            continue
        manifest = _read_manifest(child / "manifest.json")
        revisions.append(
            _ExistingRevision(
                revision_number=int(match.group(1)),
                fingerprint=manifest["revision_fingerprint"],
                dir_path=child,
            )
        )
    revisions.sort(key=lambda revision: revision.revision_number)
    return revisions


def _read_promoted_releases_resolved(store_root: Path) -> dict[str, PromotedRevision]:
    pointer_path = store_root / _POINTER_FILENAME
    if not pointer_path.exists():
        return {}
    if pointer_path.is_symlink():
        raise StoreError("store.link_rejected")

    try:
        raw_bytes = pointer_path.read_bytes()
    except OSError as exc:
        raise StoreError("store.malformed_pointer") from exc
    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StoreError("store.malformed_pointer") from exc

    if not isinstance(document, dict) or set(document.keys()) != _POINTER_TOP_LEVEL_KEYS:
        raise StoreError("store.malformed_pointer")
    if document.get("schema_version") != _POINTER_SCHEMA_VERSION:
        raise StoreError("store.malformed_pointer")

    promotions_raw = document.get("promotions")
    if not isinstance(promotions_raw, dict):
        raise StoreError("store.malformed_pointer")

    promotions: dict[str, PromotedRevision] = {}
    for date_key, entry in promotions_raw.items():
        if not isinstance(date_key, str) or not _AS_OF_DATE_PATTERN.match(date_key):
            raise StoreError("store.malformed_pointer")
        if not isinstance(entry, dict) or set(entry.keys()) != _POINTER_ENTRY_KEYS:
            raise StoreError("store.malformed_pointer")

        revision_number = entry.get("release_revision")
        fingerprint = entry.get("revision_fingerprint")
        revision_dir = entry.get("revision_dir")
        if (
            not isinstance(revision_number, int)
            or isinstance(revision_number, bool)
            or revision_number <= 0
        ):
            raise StoreError("store.malformed_pointer")
        if not isinstance(fingerprint, str) or not _FINGERPRINT_PATTERN.match(fingerprint):
            raise StoreError("store.malformed_pointer")
        if not isinstance(revision_dir, str) or not revision_dir:
            raise StoreError("store.malformed_pointer")

        promotions[date_key] = PromotedRevision(
            release_revision=revision_number,
            revision_fingerprint=fingerprint,
            revision_dir=revision_dir,
        )
    return promotions


def read_promoted_releases(store_root: str | Path) -> dict[str, PromotedRevision]:
    """Read and strictly validate the closed-schema promotion pointer.

    Returns `{}` if no promotion has ever been made. Every consumer must
    read only this document -- never enumerate `releases/` directly -- so
    it observes at most one promoted revision per date (D-09).
    """

    resolved_store_root = _resolve_store_root(store_root)
    return _read_promoted_releases_resolved(resolved_store_root)


def _replace_pointer(store_root: Path, promotions: dict[str, PromotedRevision]) -> None:
    document = {
        "schema_version": _POINTER_SCHEMA_VERSION,
        "promotions": {
            promotion_date: {
                "release_revision": entry.release_revision,
                "revision_fingerprint": entry.revision_fingerprint,
                "revision_dir": entry.revision_dir,
            }
            for promotion_date, entry in sorted(promotions.items())
        },
    }

    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{_POINTER_FILENAME}.", suffix=".tmp", dir=store_root
        )
    except OSError as exc:
        raise StoreError("store.replace_failed") from exc

    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, store_root / _POINTER_FILENAME)
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise StoreError("store.replace_failed") from exc
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    _fsync_dir_best_effort(store_root)


def _write_manifest(
    staged_dir: Path,
    *,
    as_of_date: str,
    release_revision: int,
    revision_fingerprint: str,
    manifest_metadata: dict[str, object],
) -> None:
    document = {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "as_of_date": as_of_date,
        "release_revision": release_revision,
        "revision_fingerprint": revision_fingerprint,
        "fingerprint_algorithm": _FINGERPRINT_ALGORITHM,
        "metadata": manifest_metadata,
    }
    manifest_path = staged_dir / "manifest.json"
    try:
        with open(manifest_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except (OSError, TypeError, ValueError) as exc:
        raise StoreError("store.invalid_manifest_metadata") from exc


def _write_attempt_record(
    store_root: Path,
    *,
    as_of_date: str,
    revision_fingerprint: str,
    status: str,
    release_revision: int | None,
    recovered: bool,
) -> None:
    """Best-effort durable attempt trail (T-02-08). A failure here never
    masks an otherwise-successful or otherwise-failed commit outcome.
    """

    attempts_dir = store_root / _ATTEMPTS_DIRNAME
    attempt_id = uuid.uuid4().hex
    document = {
        "schema_version": _ATTEMPT_SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "as_of_date": as_of_date,
        "revision_fingerprint": revision_fingerprint,
        "status": status,
        "release_revision": release_revision,
        "recovered": recovered,
    }
    try:
        fd, temp_name = tempfile.mkstemp(prefix=".attempt.", suffix=".tmp", dir=attempts_dir)
    except OSError:
        return
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, attempts_dir / f"{attempt_id}.json")
    except OSError:
        temp_path.unlink(missing_ok=True)


def _cleanup_staging_child(staged_dir: Path, staging_root: Path) -> None:
    """Remove exactly the one resolved staging child this call was given.

    Refuses to act on anything that is not a direct, resolved child of
    `staging_root` -- this is the only directory `commit_revision` is ever
    permitted to delete (never a caller-supplied store/candidate root).
    """

    if staged_dir.parent != staging_root:
        raise StoreError("store.invalid_staging_root")
    try:
        shutil.rmtree(staged_dir)
    except OSError as exc:
        raise StoreError("store.cleanup_failed") from exc


def _finish(
    store_root: Path,
    *,
    as_of_date: str,
    revision_fingerprint: str,
    status: str,
    release_revision: int,
    recovered: bool,
) -> RevisionCommit:
    result = RevisionCommit(
        status=status,
        as_of_date=as_of_date,
        release_revision=release_revision,
        revision_fingerprint=revision_fingerprint,
        recovered=recovered,
    )
    _write_attempt_record(
        store_root,
        as_of_date=as_of_date,
        revision_fingerprint=revision_fingerprint,
        status=status,
        release_revision=release_revision,
        recovered=recovered,
    )
    return result


def _commit_revision_locked(
    store_root: Path,
    staged_dir: Path,
    staging_root: Path,
    as_of_date: str,
    revision_fingerprint: str,
    manifest_metadata: dict[str, object],
    failure_hook: Callable[[str], None],
) -> RevisionCommit:
    existing = _list_revisions_for_date(store_root, as_of_date)
    promoted = _read_promoted_releases_resolved(store_root)
    current_promotion = promoted.get(as_of_date)

    matching = [revision for revision in existing if revision.fingerprint == revision_fingerprint]

    if matching:
        match = matching[0]
        already_promoted = (
            current_promotion is not None
            and current_promotion.release_revision == match.revision_number
            and current_promotion.revision_fingerprint == revision_fingerprint
        )
        # This call's staged content is redundant either way: the exact
        # same revision already exists on disk. Only this resolved staging
        # child is ever removed (D-07).
        _cleanup_staging_child(staged_dir, staging_root)

        if already_promoted:
            return _finish(
                store_root,
                as_of_date=as_of_date,
                revision_fingerprint=revision_fingerprint,
                status="no_new_release",
                release_revision=match.revision_number,
                recovered=False,
            )

        # A complete, unpromoted revision exists: an earlier process
        # finished the rename but crashed before the pointer replacement.
        # Finish that same promotion; never allocate another revision.
        relative_dir = match.dir_path.relative_to(store_root).as_posix()
        new_promotions = dict(promoted)
        new_promotions[as_of_date] = PromotedRevision(
            release_revision=match.revision_number,
            revision_fingerprint=revision_fingerprint,
            revision_dir=relative_dir,
        )
        _replace_pointer(store_root, new_promotions)
        failure_hook("after_replace")
        return _finish(
            store_root,
            as_of_date=as_of_date,
            revision_fingerprint=revision_fingerprint,
            status="accepted",
            release_revision=match.revision_number,
            recovered=True,
        )

    # No existing revision for this date carries this fingerprint: allocate
    # the next immutable revision number for this date.
    next_revision_number = max((revision.revision_number for revision in existing), default=0) + 1

    _write_manifest(
        staged_dir,
        as_of_date=as_of_date,
        release_revision=next_revision_number,
        revision_fingerprint=revision_fingerprint,
        manifest_metadata=manifest_metadata,
    )

    date_dir = store_root / _RELEASES_DIRNAME / as_of_date
    try:
        date_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StoreError("store.permission_denied") from exc

    final_dir = date_dir / f"rev-{next_revision_number:04d}-{revision_fingerprint[:8]}"
    if final_dir.exists():
        # Revision numbers are allocated monotonically under this same
        # lock, so a pre-existing target here means store state is
        # inconsistent with what `_list_revisions_for_date` just read.
        # Fail closed rather than overwrite an immutable revision (D-09).
        raise StoreError("store.duplicate_revision_dir")

    failure_hook("before_rename")
    try:
        os.rename(staged_dir, final_dir)
    except OSError as exc:
        raise StoreError("store.rename_failed") from exc
    _fsync_dir_best_effort(date_dir)
    failure_hook("after_rename")

    relative_dir = final_dir.relative_to(store_root).as_posix()
    new_promotions = dict(promoted)
    new_promotions[as_of_date] = PromotedRevision(
        release_revision=next_revision_number,
        revision_fingerprint=revision_fingerprint,
        revision_dir=relative_dir,
    )
    _replace_pointer(store_root, new_promotions)
    failure_hook("after_replace")

    return _finish(
        store_root,
        as_of_date=as_of_date,
        revision_fingerprint=revision_fingerprint,
        status="accepted",
        release_revision=next_revision_number,
        recovered=False,
    )


def commit_revision(
    store_root: str | Path,
    staged_revision_dir: str | Path,
    as_of_date: str,
    revision_fingerprint: str,
    manifest_metadata: dict[str, object] | None = None,
    *,
    failure_hook: Callable[[str], None] | None = None,
) -> RevisionCommit:
    """Commit or recover one immutable release revision (D-07/D-08/D-09).

    `staged_revision_dir` must already be a complete, resolved, unique
    directory directly beneath `<store_root>/.staging` (built and hashed by
    the caller -- this module never receives or inspects a raw row).
    `manifest_metadata` must be a JSON-serializable dict of already-safe
    fields; it is recorded verbatim in the revision's `manifest.json` and
    never interpreted.

    Re-reads store state and the ordered-hash fingerprint under one
    cross-platform exclusive writer lock before deciding an outcome:

    - An existing *promoted* revision with the same date and fingerprint
      returns `no_new_release` (D-08) and removes the now-redundant staged
      directory.
    - An existing but *unpromoted* revision with the same date and
      fingerprint means an earlier process renamed the revision directory
      and then crashed before the pointer replacement; this call finishes
      that exact promotion (`recovered=True`) rather than allocating a new
      revision.
    - Otherwise this call allocates, writes, and renames the next immutable
      revision for the date, then atomically replaces the promotion
      pointer.

    `failure_hook`, if given, is called with one of `"before_rename"`,
    `"after_rename"`, or `"after_replace"` at the matching boundary --
    solely for deterministic test failure injection; production callers
    must not pass one.

    Raises `StoreError` on any lock, containment, rename, replacement,
    malformed-document, or permission failure. Never leaves the promotion
    pointer partially written, and never overwrites an existing revision
    directory.
    """

    manifest_metadata = {} if manifest_metadata is None else manifest_metadata
    if not isinstance(manifest_metadata, dict):
        raise StoreError("store.invalid_manifest_metadata")
    hook = failure_hook if failure_hook is not None else (lambda stage: None)

    _validate_as_of_date(as_of_date)
    _validate_fingerprint(revision_fingerprint)

    layout = ensure_store_layout(store_root)
    resolved_staged = _resolve_staged_revision_dir(staged_revision_dir, layout.staging_root)
    _require_same_filesystem(layout.store_root, resolved_staged)

    lock_path = layout.store_root / _LOCK_FILENAME
    with _StoreLock(lock_path):
        return _commit_revision_locked(
            layout.store_root,
            resolved_staged,
            layout.staging_root,
            as_of_date,
            revision_fingerprint,
            manifest_metadata,
            hook,
        )


__all__ = [
    "PromotedRevision",
    "RevisionCommit",
    "StoreError",
    "StoreLayout",
    "commit_revision",
    "ensure_store_layout",
    "read_promoted_releases",
]
