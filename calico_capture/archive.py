"""Provider-neutral immutable archive protocol and transaction synchronization
(06-01-PLAN.md D-01/D-02/D-03; 06-RESEARCH.md Pattern 1/2/6; COVERAGE.md
items 3, 4, 6).

Defines the narrow `Archive` boundary a live Backblaze B2 adapter (a later
plan) and the offline `tests.capture.fakes.FakeArchive` double both
implement identically, plus `synchronize_verified_transaction`, the one
production function that mirrors an admitted store revision into that
boundary as one immutable transaction: content objects first, the
promotion snapshot next, and the transaction manifest last -- read back and
schema-validated before this call ever reports success (Pattern 2/6).

Every object this module ever writes lives beneath the fixed versioned
archive prefix `archive/v1/`. Repeated synchronization of the same accepted
revision is a byte-verified idempotent no-op (COVERAGE.md "Coverage
invariants"): the transaction id is derived only from
`(as_of_date, release_revision, revision_fingerprint)`, so a retried call
resolves every key to its own already-uploaded byte-identical version.

This module never opens, prints, or echoes a raw row, source field, or
absolute caller path -- every failure crosses its boundary as one fixed
safe `ArchiveError` category (mirrors `calico_landing.store.StoreError`'s
non-echo discipline). It never deletes, hides, or overwrites an existing
archive object (COVERAGE.md explicit opt-outs 1/2/3).

`read_latest_transaction_pointer` reads (and `synchronize_verified_transaction`
maintains) one additional fixed, well-known object --
`archive/v1/latest-transaction-pointer.json`, matching
`contracts/latest-transaction-pointer-v1.schema.json` -- recording the safe
release identity of the most recently synchronized transaction (2026-09-03
code review, CR-01). The Archive protocol has no prefix-listing capability by
design, so a caller with no local state (a fresh `capture()` working store)
has no other way to discover which transaction, if any, was archived last;
this fixed key is the one deliberately mutable exception to this module's
otherwise strictly immutable, content-addressed key space, and is itself
still append-only -- every write is a brand new provider-kept version, never
a delete/hide/overwrite of a prior one. It is advanced only forward (never
regressed by an out-of-order synchronize call, e.g. a historical `seed`
backfill) and is never the source of truth for any individual transaction's
own validity -- that always remains the transaction's own read-back-verified
manifest; the pointer is purely a best-effort discovery accelerator for
`calico_capture.restore.restore_latest_known_transaction`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from calico_landing.result import AdmissionResult

#: Fixed versioned archive prefix (06-RESEARCH.md Pattern 1). Every object
#: this module ever addresses lives beneath this prefix; no other prefix is
#: ever written.
_ARCHIVE_PREFIX = "archive/v1"
_STORE_PREFIX = f"{_ARCHIVE_PREFIX}/store"
_TRANSACTIONS_PREFIX = f"{_ARCHIVE_PREFIX}/transactions"

_TRANSACTION_MANIFEST_FILENAME = "archive-transaction.json"
_PROMOTION_SNAPSHOT_FILENAME = "promoted-releases.json"

_TRANSACTION_SCHEMA_VERSION = 1

#: Closed transaction-manifest document key set
#: (`contracts/private-archive-v1.schema.json`).
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

_SHA256_PATTERN_LENGTH = 64

#: Fixed, well-known discovery pointer key (CR-01 fix) -- deliberately
#: *not* under `_STORE_PREFIX` or `_TRANSACTIONS_PREFIX`, so it can never
#: collide with any content-object or transaction-manifest key pattern.
_LATEST_TRANSACTION_POINTER_KEY = f"{_ARCHIVE_PREFIX}/latest-transaction-pointer.json"

_LATEST_POINTER_SCHEMA_VERSION = 1

#: Closed key set for the pointer document -- the same safe release
#: identity fields `ArchiveTransaction`/the transaction manifest already
#: carry, nothing else.
_LATEST_POINTER_KEYS = frozenset(
    {"schema_version", "as_of_date", "release_revision", "revision_fingerprint"}
)


class ArchiveError(Exception):
    """Raised on any archive failure. Carries only a fixed safe `category`
    -- never a path, raw value, provider exception text, or credential
    fragment (mirrors `calico_landing.store.StoreError`).
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class ArchiveObjectVersion:
    """One safe, value-free listed version of an archive object key
    (COVERAGE.md item 3).

    `action` is the closed provider-neutral vocabulary `"upload"` (a
    complete, readable object -- the only admissible existing state),
    `"hide"` (a hide marker), or `"start"` (an unfinished/incomplete
    upload). Any `action` other than a lone `"upload"` version is treated
    as a closed collision/ambiguity error by this module's callers.
    """

    version_id: str
    sha256: str
    content_length: int
    action: str = "upload"


class Archive(Protocol):
    """The narrow provider-neutral archive boundary every live adapter and
    the offline fake both implement identically (D-01/D-14). Every method
    addresses exactly one object key; no method ever exposes a raw
    provider response, account identity, or credential.
    """

    def list_versions(self, key: str) -> tuple[ArchiveObjectVersion, ...]:
        """Return every known version of `key` in the fixed order the
        provider records them, oldest first. An absent key returns `()`.
        """
        ...

    def put_object(self, key: str, data: bytes) -> None:
        """Upload one new immutable version at `key`. Never called for a
        key this module has already proven exists with identical bytes.
        """
        ...

    def get_object(self, key: str, *, version_id: str | None = None) -> bytes:
        """Download the exact named version's bytes (or the newest version
        when `version_id` is omitted). Raises `ArchiveError` if the key or
        version is absent.
        """
        ...


@dataclass(frozen=True)
class ArchiveTransaction:
    """One completed, read-back-verified immutable archive transaction."""

    transaction_id: str
    as_of_date: str
    release_revision: int
    object_keys: tuple[str, ...]
    manifest_key: str


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ensure_written(archive: Archive, key: str, data: bytes) -> ArchiveObjectVersion:
    """Write `data` to `key` exactly once, idempotently.

    Absent key: upload, then read back and re-hash the written bytes
    before trusting the new version (Pattern 6 "project hash/read-back
    verification"). Exactly one existing `"upload"` version with the same
    SHA-256 and byte count: a no-op, since a retried synchronize call must
    never re-upload identical content. Anything else -- different bytes,
    more than one version, or a non-`"upload"` action (a hide marker or an
    unfinished upload) -- fails closed as a collision/ambiguity error
    (COVERAGE.md item 3; never delete/hide/hide-detect, only detect).
    """

    existing = archive.list_versions(key)

    if not existing:
        archive.put_object(key, data)
        written = archive.list_versions(key)
        if len(written) != 1 or written[0].action != "upload":
            raise ArchiveError("archive.write_verification_failed")
        version = written[0]
        if version.content_length != len(data) or version.sha256 != _sha256_bytes(data):
            raise ArchiveError("archive.write_verification_failed")
        readback = archive.get_object(key, version_id=version.version_id)
        if readback != data:
            raise ArchiveError("archive.readback_mismatch")
        return version

    if len(existing) == 1 and existing[0].action == "upload":
        version = existing[0]
        if version.content_length == len(data) and version.sha256 == _sha256_bytes(data):
            return version  # idempotent no-op: byte-identical replay
        raise ArchiveError("archive.collision")

    raise ArchiveError("archive.version_ambiguous")


def _collect_release_files(
    store_root: Path, as_of_date: str, release_revision: int, revision_fingerprint: str
) -> list[Path]:
    revision_dir = (
        store_root
        / "releases"
        / as_of_date
        / f"rev-{release_revision:04d}-{revision_fingerprint[:8]}"
    )
    if not revision_dir.is_dir():
        raise ArchiveError("archive.release_dir_missing")

    files = sorted(
        (path for path in revision_dir.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(store_root).as_posix(),
    )
    if not files:
        raise ArchiveError("archive.empty_object_set")
    return files


def _validate_transaction_manifest_document(document: object) -> None:
    """Closed-schema validation mirroring
    `contracts/private-archive-v1.schema.json` -- rejects any document with
    an unexpected type, missing/extra top-level key, wrong schema version,
    or malformed field before this module ever trusts a read-back manifest
    (COVERAGE.md "reject ... unknown keys ... before a transaction becomes
    restorable").
    """

    if not isinstance(document, dict) or set(document.keys()) != _TRANSACTION_MANIFEST_KEYS:
        raise ArchiveError("archive.malformed_transaction_manifest")
    if document.get("schema_version") != _TRANSACTION_SCHEMA_VERSION:
        raise ArchiveError("archive.malformed_transaction_manifest")

    object_keys = document.get("object_keys")
    object_sha256 = document.get("object_sha256")
    if not isinstance(object_keys, list) or not object_keys:
        raise ArchiveError("archive.malformed_transaction_manifest")
    if object_keys != sorted(object_keys):
        raise ArchiveError("archive.malformed_transaction_manifest")
    if not isinstance(object_sha256, dict) or set(object_sha256.keys()) != set(object_keys):
        raise ArchiveError("archive.malformed_transaction_manifest")
    for digest in object_sha256.values():
        if not isinstance(digest, str) or len(digest) != _SHA256_PATTERN_LENGTH:
            raise ArchiveError("archive.malformed_transaction_manifest")

    for field_name in ("transaction_id", "as_of_date", "revision_fingerprint"):
        if not isinstance(document.get(field_name), str) or not document.get(field_name):
            raise ArchiveError("archive.malformed_transaction_manifest")

    release_revision = document.get("release_revision")
    if (
        not isinstance(release_revision, int)
        or isinstance(release_revision, bool)
        or release_revision < 1
    ):
        raise ArchiveError("archive.malformed_transaction_manifest")

    promotion_key = document.get("promotion_snapshot_key")
    promotion_sha256 = document.get("promotion_snapshot_sha256")
    if not isinstance(promotion_key, str) or not promotion_key:
        raise ArchiveError("archive.malformed_transaction_manifest")
    if not isinstance(promotion_sha256, str) or len(promotion_sha256) != _SHA256_PATTERN_LENGTH:
        raise ArchiveError("archive.malformed_transaction_manifest")


def _validate_latest_pointer_document(document: object) -> dict[str, object]:
    """Closed-schema validation for the discovery pointer document -- the
    same safe release-identity field checks
    `_validate_transaction_manifest_document` already applies to the
    equivalent transaction-manifest fields.
    """

    if not isinstance(document, dict) or set(document.keys()) != _LATEST_POINTER_KEYS:
        raise ArchiveError("archive.malformed_latest_pointer")
    if document.get("schema_version") != _LATEST_POINTER_SCHEMA_VERSION:
        raise ArchiveError("archive.malformed_latest_pointer")

    as_of_date = document.get("as_of_date")
    if not isinstance(as_of_date, str) or not as_of_date:
        raise ArchiveError("archive.malformed_latest_pointer")

    release_revision = document.get("release_revision")
    if (
        not isinstance(release_revision, int)
        or isinstance(release_revision, bool)
        or release_revision < 1
    ):
        raise ArchiveError("archive.malformed_latest_pointer")

    revision_fingerprint = document.get("revision_fingerprint")
    if (
        not isinstance(revision_fingerprint, str)
        or len(revision_fingerprint) != _SHA256_PATTERN_LENGTH
    ):
        raise ArchiveError("archive.malformed_latest_pointer")

    return document


def read_latest_transaction_pointer(archive: Archive) -> dict[str, object] | None:
    """Read the fixed, well-known discovery pointer (CR-01 fix; module
    docstring), if one has ever been written.

    Returns `None` if the pointer key has never been written -- the
    correct, safe signal for a genuinely first-ever transaction into a
    never-before-archived history (a caller restoring before capture must
    treat this identically to "nothing to restore", never as a failure).
    Returns the closed-schema-validated pointer document (a plain `dict`
    with `as_of_date`/`release_revision`/`revision_fingerprint`) otherwise.

    Ignores any non-`"upload"` version at the pointer key (this module
    never writes a hide marker or leaves an unfinished upload there) and
    resolves to the newest remaining `"upload"` version, matching
    `list_versions`'s documented oldest-first ordering.

    Raises `ArchiveError` on any list/read failure or malformed document --
    never partially trusts a corrupted or unexpected pointer shape.
    """

    versions = archive.list_versions(_LATEST_TRANSACTION_POINTER_KEY)
    uploads = [version for version in versions if version.action == "upload"]
    if not uploads:
        return None

    newest = uploads[-1]
    raw_bytes = archive.get_object(_LATEST_TRANSACTION_POINTER_KEY, version_id=newest.version_id)
    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveError("archive.malformed_latest_pointer") from exc

    return _validate_latest_pointer_document(document)


def _advance_latest_transaction_pointer(
    archive: Archive,
    *,
    as_of_date: str,
    release_revision: int,
    revision_fingerprint: str,
) -> None:
    """Best-effort-safe advance of the fixed discovery pointer to
    `(as_of_date, release_revision, revision_fingerprint)` -- called only
    after `synchronize_verified_transaction`'s own transaction manifest has
    already been written and read-back verified (module docstring; never
    before, so the pointer can never name a transaction that turns out not
    to exist).

    A no-op if the pointer already names an identity whose
    `(as_of_date, release_revision)` is greater than or equal to this
    call's own identity -- an out-of-order synchronize call (e.g. a
    historical `seed` backfill, or an exact idempotent replay) never
    regresses or redundantly rewrites the pointer. ISO `YYYY-MM-DD` dates
    compare correctly as plain strings, so no date parsing is needed.

    Raises `ArchiveError` on any list/write/read/verification failure.
    Because this call happens only after the transaction's own manifest is
    already durably written, a failure here fails the whole enclosing
    `synchronize_verified_transaction` call closed (matching every other
    write step in that function) but never leaves the just-written
    transaction itself invalid -- a retried `synchronize_verified_transaction`
    call for the same identity is fully idempotent (every content object and
    the manifest are already byte-identical no-ops) and only needs this
    pointer-advance step to succeed.
    """

    current = read_latest_transaction_pointer(archive)
    new_key = (as_of_date, release_revision)
    if current is not None:
        current_key = (current["as_of_date"], current["release_revision"])
        if new_key <= current_key:
            return

    pointer_document = {
        "schema_version": _LATEST_POINTER_SCHEMA_VERSION,
        "as_of_date": as_of_date,
        "release_revision": release_revision,
        "revision_fingerprint": revision_fingerprint,
    }
    pointer_bytes = json.dumps(
        pointer_document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")

    archive.put_object(_LATEST_TRANSACTION_POINTER_KEY, pointer_bytes)

    # Read-back verification (Pattern 6), adapted for an append-only key: a
    # concurrent writer could in principle advance the pointer again between
    # our own `put_object` and this `list_versions` call, in which case the
    # newest version would no longer be the one this call just wrote --
    # this is an accepted, documented limitation of a best-effort discovery
    # accelerator (module docstring), never the source of truth for any
    # individual transaction's own validity.
    uploads = [
        version
        for version in archive.list_versions(_LATEST_TRANSACTION_POINTER_KEY)
        if version.action == "upload"
    ]
    if (
        not uploads
        or uploads[-1].content_length != len(pointer_bytes)
        or uploads[-1].sha256 != _sha256_bytes(pointer_bytes)
    ):
        raise ArchiveError("archive.latest_pointer_write_verification_failed")

    readback = archive.get_object(_LATEST_TRANSACTION_POINTER_KEY, version_id=uploads[-1].version_id)
    if readback != pointer_bytes:
        raise ArchiveError("archive.latest_pointer_readback_mismatch")


def synchronize_verified_transaction(
    archive: Archive, store_root: str | Path, result: AdmissionResult
) -> ArchiveTransaction:
    """Mirror one accepted/no-new-release store revision into `archive` as
    one immutable, read-back-verified transaction (D-01/D-03; Pattern 2/6).

    `result` must carry a complete release identity
    (`as_of_date`/`release_revision`/`revision_fingerprint`) -- callers
    never invoke this for a `rejected` or `operational_error` outcome,
    since neither commits anything to the local store.

    Order is fixed and load-bearing: every content object under the
    revision directory uploads first, the current promotion-pointer
    snapshot uploads next, and the transaction manifest -- the only
    document that makes the transaction restorable -- uploads and is
    read-back schema-verified last. Interrupting this call after content
    objects exist but before the manifest is written leaves no
    authoritative partial transaction: a restorer that only trusts a
    verified manifest observes nothing usable (must_haves truth 3).

    Raises `ArchiveError` on any collision, ambiguity, read-back mismatch,
    or malformed-document failure; never partially completes a manifest.
    """

    if result.status not in ("accepted", "no_new_release"):
        raise ArchiveError("archive.invalid_result_status")
    if (
        result.as_of_date is None
        or result.release_revision is None
        or result.revision_fingerprint is None
    ):
        raise ArchiveError("archive.invalid_result_status")

    resolved_store_root = Path(store_root)
    files = _collect_release_files(
        resolved_store_root, result.as_of_date, result.release_revision, result.revision_fingerprint
    )

    revision_prefix = f"rev-{result.release_revision:04d}-{result.revision_fingerprint[:8]}"
    transaction_id = f"{result.as_of_date}-{revision_prefix}"

    object_keys: list[str] = []
    object_sha256: dict[str, str] = {}
    for file_path in files:
        relative = file_path.relative_to(resolved_store_root).as_posix()
        key = f"{_STORE_PREFIX}/{relative}"
        data = file_path.read_bytes()
        version = _ensure_written(archive, key, data)
        object_keys.append(key)
        object_sha256[key] = version.sha256

    pointer_path = resolved_store_root / "promoted-releases.json"
    try:
        pointer_bytes = pointer_path.read_bytes()
    except OSError as exc:
        raise ArchiveError("archive.promotion_snapshot_missing") from exc

    promotion_key = f"{_TRANSACTIONS_PREFIX}/{transaction_id}/{_PROMOTION_SNAPSHOT_FILENAME}"
    promotion_version = _ensure_written(archive, promotion_key, pointer_bytes)

    sorted_keys = sorted(object_keys)
    manifest_document = {
        "schema_version": _TRANSACTION_SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "as_of_date": result.as_of_date,
        "release_revision": result.release_revision,
        "revision_fingerprint": result.revision_fingerprint,
        "object_keys": sorted_keys,
        "object_sha256": {key: object_sha256[key] for key in sorted_keys},
        "promotion_snapshot_key": promotion_key,
        "promotion_snapshot_sha256": promotion_version.sha256,
    }
    manifest_bytes = json.dumps(
        manifest_document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    manifest_key = f"{_TRANSACTIONS_PREFIX}/{transaction_id}/{_TRANSACTION_MANIFEST_FILENAME}"
    manifest_version = _ensure_written(archive, manifest_key, manifest_bytes)

    readback_manifest = archive.get_object(manifest_key, version_id=manifest_version.version_id)
    if readback_manifest != manifest_bytes:
        raise ArchiveError("archive.readback_mismatch")
    try:
        readback_document = json.loads(readback_manifest.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveError("archive.malformed_transaction_manifest") from exc
    _validate_transaction_manifest_document(readback_document)

    # Only after the transaction manifest itself is durably written and
    # read-back verified -- never before -- advance the fixed discovery
    # pointer (CR-01 fix; module docstring) so a caller with no local state
    # can find this transaction on a later, separate `capture()` call.
    _advance_latest_transaction_pointer(
        archive,
        as_of_date=result.as_of_date,
        release_revision=result.release_revision,
        revision_fingerprint=result.revision_fingerprint,
    )

    return ArchiveTransaction(
        transaction_id=transaction_id,
        as_of_date=result.as_of_date,
        release_revision=result.release_revision,
        object_keys=tuple(sorted_keys),
        manifest_key=manifest_key,
    )


__all__ = [
    "Archive",
    "ArchiveError",
    "ArchiveObjectVersion",
    "ArchiveTransaction",
    "read_latest_transaction_pointer",
    "synchronize_verified_transaction",
]
