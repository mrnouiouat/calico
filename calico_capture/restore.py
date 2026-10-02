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

`restore_catalog_with_private_policy` is the production restore boundary for
restore-build, capture and publication. It verifies the complete catalog and
exact privately bound policy before materializing a caller-visible store.
`restore_latest_known_transaction` remains a single-transaction compatibility
helper; production build paths use the full catalog boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
import os
import stat
import subprocess
import tempfile
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


def _unique_manifest_fields(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise RestoreError("restore.malformed_transaction_manifest")
        document[key] = value
    return document


def _fetch_object(archive: Archive, key: str, *, category: str) -> bytes:
    try:
        versions = archive.list_versions(key)
        if len(versions) != 1 or versions[0].action != "upload":
            raise RestoreError(category)
        version = versions[0]
        data = archive.get_object(key, version_id=version.version_id)
        if not isinstance(data, bytes) or len(data) != version.content_length or _sha256_bytes(data) != version.sha256:
            raise RestoreError("restore.object_hash_mismatch")
        return data
    except RestoreError:
        raise
    except Exception as exc:
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
    if type(document.get("schema_version")) is not int or document.get("schema_version") != _TRANSACTION_SCHEMA_VERSION:
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
        manifest_document = json.loads(manifest_bytes.decode("utf-8"), object_pairs_hook=_unique_manifest_fields)
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
    "restore_catalog_with_private_policy",
    "restore_private_policy_bundle",
    "RestoredPrivatePolicy",
]


@dataclass(frozen=True)
class RestoredPrivatePolicy:
    """Positive proof projection; never contains sidecar bytes or paths."""

    expected_sha256: str
    actual_sha256: str
    classification_version: str
    prior_publication_manifest_sha256: str
    prior_publication_commit: str
    readback_verified: bool = True

    def to_dict(self):
        from dataclasses import asdict
        return asdict(self)


@dataclass(frozen=True)
class RestoredCatalog:
    restored_transaction_count: int
    object_count: int
    release_manifest_sha256s: tuple[str, ...]
    private_policy: RestoredPrivatePolicy


def _default_catalog_loader():
    from calico_dbt.catalog import load_input_catalog
    return load_input_catalog(Path(__file__).resolve().parents[1] / "contracts/dbt-input-catalog-v1.json")


def load_prior_publication_binding(*, remote="origin", target_ref="published-data"):
    """Read the current public binding, with Git output captured and never echoed.

    The public manifest has no classification-version field. Its content hash
    selects the immutable private manifest, whose version is the owner's seed
    attestation of the last-used version, not a version invented by restoration.
    """
    from calico_publish.transaction import _tip
    if not isinstance(remote, str) or not remote or remote.startswith("-") or target_ref != "published-data":
        raise RestoreError("preflight.public_eligibility_invalid")
    try:
        repo = Path(__file__).resolve().parents[1]
        commit = _tip(repo, remote, "refs/heads/published-data")
        raw = subprocess.run(["git", "-C", str(repo), "show",
                              f"{commit}:manifest/published-manifest-v1.json"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, check=True).stdout
        if not re.fullmatch(r"[0-9a-f]{40}", commit) or not raw:
            raise ValueError
        return _sha256_bytes(raw), commit
    except Exception:
        raise RestoreError("preflight.public_eligibility_invalid") from None


def restore_private_policy_bundle(archive, *, binding, expected_classification_version=None):
    """Validate exact archived versions in memory before installing anything."""
    from calico_capture.private_policy import load_private_policy_manifest, _read_exact, PrivatePolicyError
    try:
        digest, commit = binding
        manifest = load_private_policy_manifest(
            archive, prior_publication_manifest_sha256=digest, prior_publication_commit=commit,
            expected_classification_version=expected_classification_version)
        raw = _read_exact(archive, manifest.objects[0])
        if _sha256_bytes(raw) != manifest.sidecar_sha256 or len(raw) != manifest.sidecar_length_bytes:
            raise PrivatePolicyError("private_policy.readback_mismatch")
        proof = RestoredPrivatePolicy(manifest.sidecar_sha256, _sha256_bytes(raw),
                                      manifest.classification_version, digest, commit)
        return proof, raw
    except PrivatePolicyError as exc:
        missing = exc.category in {"private_policy.manifest_missing", "private_policy.object_missing"}
        raise RestoreError("preflight.public_eligibility_missing" if missing
                           else "preflight.public_eligibility_invalid") from None
    except Exception:
        raise RestoreError("preflight.public_eligibility_invalid") from None


def _check_unlinked_path(path):
    """Reject symlink/reparse aliases in every existing component."""
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    try:
        for part in absolute.parts[1:]:
            current = current / part
            try:
                metadata = os.lstat(current)
            except FileNotFoundError:
                continue
            reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if stat.S_ISLNK(metadata.st_mode) or (reparse and getattr(metadata, "st_file_attributes", 0) & reparse):
                raise RestoreError("preflight.public_eligibility_invalid")
    except OSError:
        raise RestoreError("preflight.public_eligibility_invalid") from None
    return absolute


def _check_destination(path, raw):
    from calico_capture.private_policy import _read_local_file, PrivatePolicyError
    _check_unlinked_path(path)
    try:
        if path.exists() and _read_local_file(path) != raw:
            raise RestoreError("restore.pre_existing_conflict")
    except PrivatePolicyError:
        raise RestoreError("restore.pre_existing_conflict") from None


def _install_verified(path, raw):
    _check_destination(path, raw)
    if path.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _check_unlinked_path(path)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        _check_destination(path, raw)
    except OSError:
        raise RestoreError("restore.write_failed") from None


def restore_catalog_with_private_policy(archive, destination_root, *, catalog=None,
                                        binding_loader=None, expected_classification_version=None,
                                        allow_empty_archive=False):
    """Verify the complete ordered catalog and policy before exposing any bytes.

    Each transaction is checked in an owned temporary store. Promotion snapshots
    are successive historical states; only the final verified snapshot is
    installed. Existing caller files must match and are never overwritten.
    This seam does no build: every caller owns its one final production build.
    """
    from calico_dbt.catalog import load_and_verify_revision_manifest, CatalogError
    from calico_landing.candidate import reject_store_in_git_worktree, CandidateError
    root = _check_unlinked_path(destination_root)
    try:
        reject_store_in_git_worktree(root)
        if not root.is_dir():
            raise RestoreError("restore.invalid_destination_root")
    except CandidateError:
        raise RestoreError("restore.invalid_destination_root") from None
    if allow_empty_archive:
        try:
            pointer = read_latest_transaction_pointer(archive)
        except Exception:
            raise RestoreError("restore.latest_pointer_read_failed") from None
        if pointer is None:
            ensure_store_layout(root)
            return None
    catalog = catalog if catalog is not None else _default_catalog_loader()
    anchors = sorted(catalog.releases, key=lambda anchor: (anchor.as_of_date, anchor.release_revision))
    if not anchors:
        raise RestoreError("restore_build.empty_catalog")
    content = {}
    object_count = 0
    class SkipBuild:
        succeeded = True
    with tempfile.TemporaryDirectory(prefix="calico-verified-restore-") as temporary:
        scratch = Path(temporary).resolve()
        for index, anchor in enumerate(anchors):
            staging = scratch / str(index)
            staging.mkdir()
            restored = restore_verified_transaction(
                archive, staging, as_of_date=anchor.as_of_date, release_revision=anchor.release_revision,
                revision_fingerprint=anchor.revision_fingerprint, build=lambda _: SkipBuild())
            manifest_path = staging / "releases" / anchor.as_of_date / f"rev-{anchor.release_revision:04d}-{anchor.revision_fingerprint[:8]}" / "manifest.json"
            try:
                load_and_verify_revision_manifest(manifest_path, anchor)
            except CatalogError:
                raise RestoreError("restore.manifest_verification_failed") from None
            from calico_landing.store import read_promoted_releases
            try:
                promotions = read_promoted_releases(staging)
                for date, promoted in promotions.items():
                    pinned = catalog.anchor_for(date, promoted.release_revision)
                    if pinned is None or pinned.revision_fingerprint != promoted.revision_fingerprint:
                        raise RestoreError("restore.promotion_snapshot_invalid")
            except StoreError:
                raise RestoreError("restore.promotion_snapshot_invalid") from None
            for key in restored.object_keys:
                relative = key[len(_STORE_PREFIX):]
                raw = (staging / relative).read_bytes()
                if relative in content and content[relative] != raw:
                    raise RestoreError("restore.pre_existing_conflict")
                content[relative] = raw
            content[_PROMOTION_SNAPSHOT_FILENAME] = (staging / _PROMOTION_SNAPSHOT_FILENAME).read_bytes()
            # Match SafeBuildProof's input-object count (four canonical
            # Parquets per release), not archive metadata/raw/attempt files.
            object_count += sum("/canonical/" in key and key.endswith(".parquet")
                                for key in restored.object_keys)
    binding = (binding_loader or load_prior_publication_binding)()
    policy, sidecar = restore_private_policy_bundle(
        archive, binding=binding, expected_classification_version=expected_classification_version)
    content["public-eligibility-v1.json"] = sidecar
    # Check all local conflicts before making the first caller-visible write.
    for relative, raw in content.items():
        try:
            _check_destination(root / relative, raw)
        except RestoreError:
            if relative == "public-eligibility-v1.json":
                raise RestoreError("preflight.public_eligibility_invalid") from None
            raise
    for relative, raw in content.items():
        _install_verified(root / relative, raw)
    return RestoredCatalog(len(anchors), object_count,
                           tuple(anchor.revision_manifest_sha256 for anchor in anchors), policy)
