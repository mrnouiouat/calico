"""Manifest-driven, path-safe archive restore into a fresh external store
(06-03-PLAN.md Task 2; D-13/D-14; 06-RESEARCH.md Pattern 2 "Restore Before
Capture, Archive Before Success").

`restore_verified_transaction` is the exact inverse of
`calico_capture.archive.synchronize_verified_transaction`: given the safe
release identity of one already-archived transaction
(`as_of_date`/`release_revision`/`revision_fingerprint`), it derives the
same deterministic `transaction_id`, fetches and closed-schema-verifies the
transaction manifest, verifies every referenced object's SHA-256 entirely
in memory, and -- only after every single object and the promotion
snapshot pass -- materializes them beneath a caller-owned fresh external
store root in the same layout `calico_landing.store` already understands.
After a full, verified restore it invokes the existing real-mode
`calico_dbt.runner.build(mode="real", store=...)` seam (or an injected
spy); this module never teaches dbt to read the archive directly and never
performs an analytical calculation of its own.

Every archive key this module ever turns into a filesystem path is
normalized and containment-checked the same way
`calico_landing.candidate.resolve_and_stage_candidate` already checks a
candidate manifest's relative paths: reject absolute paths, `..`
segments, empty segments, symlink/reparse aliases at any path component,
and two distinct keys that resolve to the same destination (T-06-03B).
Repeated restoration of the same transaction into an already-populated
destination is a true byte-identical no-op; a destination path that
already holds *different* bytes fails closed as a pre-existing conflict
rather than silently overwriting it (T-06-03C, must_haves truth 4).

Every failure crosses this module's boundary as a `RestoreError` carrying
only a fixed safe `category` -- never an offending key, path, byte, or
provider exception text (mirrors `calico_capture.archive.ArchiveError`'s
non-echo discipline).

`restore_latest_known_transaction` is the actual production restore-before-
capture boundary `calico_capture.orchestrator.capture()` now wires by
default (2026-09-03 code review, CR-01 fix): it discovers the single most
recently archived transaction, if any, via
`calico_capture.archive.read_latest_transaction_pointer` and restores only
that one transaction with `restore_verified_transaction`. Only the single
latest transaction is restored, not the full historical catalog --
`calico_landing.admission.admit()`'s own `no_new_release`/next-revision-
number decision (`calico_landing.store.commit_revision`) only ever inspects
the current attempt's expected `as_of_date` and the promotion pointer
snapshot, both of which the latest transaction's own restored
`promoted-releases.json` already carries, so restoring every earlier date's
history is unnecessary for that comparison to be correct. A caller needing
the *complete* historical catalog restored (e.g. a from-scratch warehouse
rebuild) uses `calico_capture.cli`'s separate `restore-build` operator
command instead, which loops every catalog anchor explicitly.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from calico_capture.archive import Archive, ArchiveError, read_latest_transaction_pointer
from calico_landing.store import StoreError, ensure_store_layout

#: Fixed versioned archive prefix and object families -- must match
#: `calico_capture.archive`'s own private constants exactly, since this
#: module derives the identical keys the writer side already produced
#: (mirrored, not imported, per this project's established local-constant-
#: duplication precedent -- e.g. `calico_dbt.catalog`'s own mirror of
#: `calico_landing.store`'s manifest key set).
_ARCHIVE_PREFIX = "archive/v1"
_STORE_PREFIX = f"{_ARCHIVE_PREFIX}/store/"
_TRANSACTIONS_PREFIX = f"{_ARCHIVE_PREFIX}/transactions"
_TRANSACTION_MANIFEST_FILENAME = "archive-transaction.json"
_PROMOTION_SNAPSHOT_FILENAME = "promoted-releases.json"

_TRANSACTION_SCHEMA_VERSION = 1

_TRANSACTION_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "transaction_id",
        "as_of_date",
        "release_revision",
        "revision_fingerprint",
        "object_keys",
        "object_sha256",
        "promotion_snapshot_key",
        "promotion_snapshot_sha256",
    }
)

#: Exact mirror of `contracts/private-archive-v1.schema.json`'s
#: `object_keys` item pattern -- every content-object key this module ever
#: turns into a filesystem write must match this closed prefix/character
#: family before any further path-safety check is even attempted.
_OBJECT_KEY_PATTERN = re.compile(r"^archive/v1/store/(releases|attempts)/[A-Za-z0-9._/-]+$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

#: The injected real-build boundary, matching
#: `calico_capture.orchestrator.BuildFn` exactly -- duplicated locally so
#: this module has no import-time dependency on `calico_capture.orchestrator`
#: (this plan's own file-ownership split keeps the two modules independent;
#: `capture()` in a later plan wave may compose them).
BuildFn = Callable[[Path], object]


class RestoreError(Exception):
    """Raised on any restore failure. Carries only a fixed safe `category`
    -- never an offending key, path, byte, or provider exception text
    (mirrors `calico_capture.archive.ArchiveError`).
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class RestoredTransaction:
    """Safe outcome metadata for one completed, verified restore."""

    transaction_id: str
    as_of_date: str
    release_revision: int
    object_keys: tuple[str, ...]
    build_outcome: object


def _default_build(store_root: Path) -> object:
    from calico_dbt.runner import build as dbt_build

    return dbt_build(mode="real", store=store_root)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch_object(archive: Archive, key: str, *, category: str) -> bytes:
    try:
        return archive.get_object(key)
    except ArchiveError as exc:
        raise RestoreError(category) from exc


def _validate_transaction_manifest_document(
    document: object, *, expected_transaction_id: str
) -> dict[str, object]:
    """Closed-schema validation mirroring
    `contracts/private-archive-v1.schema.json`, plus the identity match a
    restorer additionally requires: the manifest fetched at the
    deterministic key derived from the caller-supplied identity must also
    *self-report* that same identity (T-06-03A/B; never trust a manifest
    whose own recorded identity disagrees with the key it was found at).
    """

    if not isinstance(document, dict) or set(document.keys()) != _TRANSACTION_MANIFEST_KEYS:
        raise RestoreError("restore.malformed_transaction_manifest")
    if document.get("schema_version") != _TRANSACTION_SCHEMA_VERSION:
        raise RestoreError("restore.malformed_transaction_manifest")

    if document.get("transaction_id") != expected_transaction_id:
        raise RestoreError("restore.transaction_identity_mismatch")

    object_keys = document.get("object_keys")
    object_sha256 = document.get("object_sha256")
    if not isinstance(object_keys, list) or not object_keys:
        raise RestoreError("restore.malformed_transaction_manifest")
    if object_keys != sorted(object_keys) or len(set(object_keys)) != len(object_keys):
        raise RestoreError("restore.malformed_transaction_manifest")
    if not isinstance(object_sha256, dict) or set(object_sha256.keys()) != set(object_keys):
        raise RestoreError("restore.malformed_transaction_manifest")
    for key, digest in object_sha256.items():
        if not isinstance(key, str) or not _OBJECT_KEY_PATTERN.match(key):
            raise RestoreError("restore.invalid_object_key")
        if not isinstance(digest, str) or not _SHA256_PATTERN.match(digest):
            raise RestoreError("restore.malformed_transaction_manifest")

    for field_name in ("as_of_date", "revision_fingerprint"):
        if not isinstance(document.get(field_name), str) or not document.get(field_name):
            raise RestoreError("restore.malformed_transaction_manifest")

    release_revision = document.get("release_revision")
    if (
        not isinstance(release_revision, int)
        or isinstance(release_revision, bool)
        or release_revision < 1
    ):
        raise RestoreError("restore.malformed_transaction_manifest")

    promotion_key = document.get("promotion_snapshot_key")
    promotion_sha256 = document.get("promotion_snapshot_sha256")
    if not isinstance(promotion_key, str) or not promotion_key:
        raise RestoreError("restore.malformed_transaction_manifest")
    if not isinstance(promotion_sha256, str) or not _SHA256_PATTERN.match(promotion_sha256):
        raise RestoreError("restore.malformed_transaction_manifest")

    return document


def _resolve_destination_path(
    store_root: Path, key: str, *, seen_destinations: set[Path]
) -> Path:
    """Turn one already pattern-validated archive key into a safe
    destination path beneath `store_root`, mirroring
    `calico_landing.candidate._resolve_object_path`'s containment
    discipline exactly, but working against a path that does not yet
    exist on disk (a restore write target, not an existing candidate
    file).
    """

    relative = key[len(_STORE_PREFIX) :]
    normalized = relative.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized:
        raise RestoreError("restore.invalid_object_key")

    parts = [part for part in normalized.split("/") if part != "."]
    if not parts or any(part in ("", "..") for part in parts):
        raise RestoreError("restore.invalid_object_key")

    current = store_root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise RestoreError("restore.invalid_object_key")

    resolved = current.resolve(strict=False)
    try:
        resolved.relative_to(store_root)
    except ValueError as exc:
        raise RestoreError("restore.invalid_object_key") from exc

    if resolved in seen_destinations:
        raise RestoreError("restore.duplicate_destination")
    seen_destinations.add(resolved)

    return resolved


def _write_verified_bytes(destination_path: Path, data: bytes) -> None:
    """Write `data` to `destination_path`, idempotently.

    A destination that does not yet exist is created (including any
    missing parent directories). A destination that already holds the
    exact same bytes is a no-op (byte-identical repeated restore). A
    destination that already holds *different* bytes fails closed as a
    pre-existing conflict -- this module never overwrites unrelated
    existing content (must_haves truth 4).
    """

    if destination_path.exists():
        if destination_path.is_symlink():
            raise RestoreError("restore.invalid_object_key")
        try:
            existing = destination_path.read_bytes()
        except OSError as exc:
            raise RestoreError("restore.pre_existing_conflict") from exc
        if existing == data:
            return
        raise RestoreError("restore.pre_existing_conflict")

    try:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        destination_path.write_bytes(data)
    except OSError as exc:
        raise RestoreError("restore.write_failed") from exc


def restore_verified_transaction(
    archive: Archive,
    destination_root: str | Path,
    *,
    as_of_date: str,
    release_revision: int,
    revision_fingerprint: str,
    build: BuildFn | None = None,
) -> RestoredTransaction:
    """Restore exactly one fully verified archive transaction into
    `destination_root` and run the existing real-mode build (D-13/D-14).

    `destination_root` must already be a caller-owned, existing, non-
    Git-worktree directory intended to become a fresh external store (the
    same contract `calico_landing.store.ensure_store_layout` already
    enforces). `as_of_date`/`release_revision`/`revision_fingerprint`
    identify exactly which prior `synchronize_verified_transaction` call
    this restores -- the same deterministic identity that call used to
    derive its own `transaction_id`.

    Fetches and closed-schema-verifies the transaction manifest, then
    fetches and SHA-256-verifies every listed object plus the promotion
    snapshot *entirely in memory* before writing anything to disk --
    `destination_root` is materialized only after every single byte has
    already passed verification (must_haves truth 3: "exposes a fresh
    external store only after full success"). `build` defaults to the
    real `calico_dbt.runner.build(mode="real", ...)` seam; tests inject a
    spy instead.

    Raises `RestoreError` -- and never partially materializes
    `destination_root` -- on any malformed/unknown manifest, identity
    mismatch, missing/extra/invalid object key, hash mismatch, path-
    containment/symlink/duplicate-destination violation, incomplete
    transaction, pre-existing byte conflict, or build failure.
    """

    try:
        layout = ensure_store_layout(destination_root)
    except StoreError as exc:
        raise RestoreError("restore.invalid_destination_root") from exc
    store_root = layout.store_root

    revision_prefix = f"rev-{release_revision:04d}-{revision_fingerprint[:8]}"
    transaction_id = f"{as_of_date}-{revision_prefix}"
    manifest_key = f"{_TRANSACTIONS_PREFIX}/{transaction_id}/{_TRANSACTION_MANIFEST_FILENAME}"
    expected_promotion_key = (
        f"{_TRANSACTIONS_PREFIX}/{transaction_id}/{_PROMOTION_SNAPSHOT_FILENAME}"
    )

    manifest_bytes = _fetch_object(
        archive, manifest_key, category="restore.transaction_not_found"
    )
    try:
        manifest_document = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RestoreError("restore.malformed_transaction_manifest") from exc

    document = _validate_transaction_manifest_document(
        manifest_document, expected_transaction_id=transaction_id
    )
    if (
        document.get("as_of_date") != as_of_date
        or document.get("release_revision") != release_revision
        or document.get("revision_fingerprint") != revision_fingerprint
    ):
        raise RestoreError("restore.transaction_identity_mismatch")
    if document.get("promotion_snapshot_key") != expected_promotion_key:
        raise RestoreError("restore.malformed_transaction_manifest")

    object_keys: list[str] = document["object_keys"]
    object_sha256: dict[str, str] = document["object_sha256"]

    # Resolve and containment-check every destination path *before*
    # fetching any bytes -- a structural attack (traversal, symlink,
    # duplicate destination) fails closed without any network read.
    seen_destinations: set[Path] = set()
    destinations: dict[str, Path] = {
        key: _resolve_destination_path(store_root, key, seen_destinations=seen_destinations)
        for key in object_keys
    }

    # Fetch and verify every object entirely in memory first -- nothing is
    # written to `destination_root` until every single object and the
    # promotion snapshot have already passed hash verification.
    verified_content: dict[Path, bytes] = {}
    for key in object_keys:
        data = _fetch_object(archive, key, category="restore.incomplete_transaction")
        if _sha256_bytes(data) != object_sha256[key]:
            raise RestoreError("restore.object_hash_mismatch")
        verified_content[destinations[key]] = data

    promotion_bytes = _fetch_object(
        archive, expected_promotion_key, category="restore.incomplete_transaction"
    )
    if _sha256_bytes(promotion_bytes) != document["promotion_snapshot_sha256"]:
        raise RestoreError("restore.object_hash_mismatch")

    for destination_path, data in verified_content.items():
        _write_verified_bytes(destination_path, data)
    _write_verified_bytes(store_root / _PROMOTION_SNAPSHOT_FILENAME, promotion_bytes)

    build_fn = build if build is not None else _default_build
    try:
        build_outcome = build_fn(store_root)
    except Exception as exc:
        raise RestoreError("restore.build_failed") from exc
    if not getattr(build_outcome, "succeeded", False):
        raise RestoreError("restore.build_failed")

    return RestoredTransaction(
        transaction_id=transaction_id,
        as_of_date=as_of_date,
        release_revision=release_revision,
        object_keys=tuple(object_keys),
        build_outcome=build_outcome,
    )


def restore_latest_known_transaction(
    archive: Archive,
    destination_root: str | Path,
    *,
    build: BuildFn | None = None,
) -> RestoredTransaction | None:
    """Restore the single most recently archived transaction, if any, into
    `destination_root` (CR-01 fix; module docstring) -- the real production
    restore-before-capture boundary
    `calico_capture.orchestrator.capture()` now wires by default.

    Discovers the latest transaction via
    `calico_capture.archive.read_latest_transaction_pointer` -- the fixed,
    well-known discovery pointer every `synchronize_verified_transaction`
    call maintains -- rather than looping the full historical catalog: see
    the module docstring for why restoring only the single latest
    transaction is sufficient for `calico_landing.admission.admit()`'s own
    revision-sequencing/`no_new_release` comparison to be correct.

    Returns `None` -- and still leaves `destination_root` an established
    (still empty) store layout, exactly like a plain `ensure_store_layout`
    call -- if no transaction has ever been archived (a genuinely
    first-ever capture into a never-before-archived history). Otherwise
    restores that one transaction with `restore_verified_transaction` and
    returns its `RestoredTransaction`.

    Raises `RestoreError` on any pointer-discovery or restore failure --
    never partially materializes `destination_root` (the same guarantee
    `restore_verified_transaction` itself already provides).
    """

    try:
        pointer = read_latest_transaction_pointer(archive)
    except ArchiveError as exc:
        raise RestoreError("restore.latest_pointer_read_failed") from exc

    if pointer is None:
        try:
            ensure_store_layout(destination_root)
        except StoreError as exc:
            raise RestoreError("restore.invalid_destination_root") from exc
        return None

    return restore_verified_transaction(
        archive,
        destination_root,
        as_of_date=pointer["as_of_date"],
        release_revision=pointer["release_revision"],
        revision_fingerprint=pointer["revision_fingerprint"],
        build=build,
    )


__all__ = [
    "BuildFn",
    "RestoreError",
    "RestoredTransaction",
    "restore_latest_known_transaction",
    "restore_verified_transaction",
]
