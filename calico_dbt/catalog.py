"""Closed manifest-anchor-only Gate B input catalog (D-02/D-16, T-03-01/T-03-02).

`load_input_catalog` loads a committed, closed-schema trust catalog that
pins exactly the safe release identity (`as_of_date`, `release_revision`)
plus two SHA-256 anchors per admitted revision: the ordered-source
`revision_fingerprint` already recorded by `calico_landing.store`
(`ordered-source-sha256-json-v1`), and `revision_manifest_sha256`, the hash
of that revision's own `manifest.json`. This is deliberately the *only*
information this module trusts before touching the owner-controlled store --
it never carries a canonical object hash, byte size, schema, row count, or
filesystem path, so the catalog itself is safe to commit to a public
repository.

`load_and_verify_revision_manifest` is the second half of the trust chain:
given a catalog anchor and the manifest bytes found on disk at the exact,
deterministic revision-directory path the anchor implies, it verifies the
manifest's own SHA-256 against the anchor before ever parsing or trusting a
single field inside it, then re-derives the `ordered-source-sha256-json-v1`
fingerprint from the manifest's own recorded raw object hashes and proves it
matches both the manifest's self-reported fingerprint and the catalog
anchor. Only after every one of these checks passes does this module return
a frozen, safe `VerifiedRevisionManifest` -- the sole source of truth
`preflight.py` may use to locate and verify the revision's canonical Parquet
objects.

Every failure crosses this module's boundary as a `CatalogError` carrying
only a fixed safe `category` -- never an offending path, byte, or parsed
value (mirrored from `calico_landing.store.StoreError` and
`calico_landing.contracts.ContractError`'s non-echo exception discipline).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from calico_landing.contracts import LOGICAL_LIST_ORDER

#: Locked D-08 fingerprint algorithm identifier -- must match the constant
#: independently recorded by `calico_landing.store` and
#: `calico_landing.admission` in every revision manifest.
FINGERPRINT_ALGORITHM = "ordered-source-sha256-json-v1"

_SUPPORTED_CONTRACT_VERSION = 1

_CATALOG_TOP_LEVEL_KEYS = frozenset({"contract_version", "releases"})
_RELEASE_ANCHOR_KEYS = frozenset(
    {"as_of_date", "release_revision", "revision_fingerprint", "revision_manifest_sha256"}
)

#: Exact closed keys `calico_landing.store` writes into every `manifest.json`
#: (mirrored from `calico_landing.store._MANIFEST_TOP_LEVEL_KEYS`). Anything
#: else present, missing, or misordered fails closed before a single field
#: is trusted.
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
_MANIFEST_SCHEMA_VERSION = 1

#: Exact closed keys `calico_landing.admission` writes into every manifest's
#: `metadata` object (mirrored from `admission.py`'s `manifest_metadata`).
_METADATA_KEYS = frozenset(
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

#: Exact closed keys for one logical list's manifest-recorded object facts
#: (mirrored from `admission.py`'s per-list `manifest_metadata` entry).
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

_AS_OF_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_HASH_CHUNK_BYTES = 1024 * 1024


class CatalogError(Exception):
    """Raised on any malformed catalog document, manifest-anchor mismatch,
    or fingerprint-recomputation failure. Carries only a fixed safe
    `category` -- never an offending path, byte, or parsed value.
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class CatalogReleaseAnchor:
    """One committed, manifest-anchor-only trust entry (D-02).

    Carries exactly safe release identity plus the two SHA-256 anchors this
    module trusts -- never a canonical object hash, size, schema, row
    count, or path.
    """

    as_of_date: str
    release_revision: int
    revision_fingerprint: str
    revision_manifest_sha256: str


@dataclass(frozen=True)
class InputCatalog:
    """The complete, closed, validated dbt input catalog (D-02/D-16)."""

    contract_version: int
    releases: tuple[CatalogReleaseAnchor, ...]

    def anchor_for(self, as_of_date: str, release_revision: int) -> CatalogReleaseAnchor | None:
        for anchor in self.releases:
            if anchor.as_of_date == as_of_date and anchor.release_revision == release_revision:
                return anchor
        return None


@dataclass(frozen=True)
class LogicalListManifestEntry:
    """One logical list's manifest-recorded, anchor-verified object facts.

    Trusted only because the enclosing manifest's own SHA-256 already
    matched its catalog anchor -- never independently trusted from any
    other source.
    """

    raw_sha256: str
    raw_byte_count: int
    parsed_record_count: int
    line_record_reconciled: bool
    parquet_sha256: str
    parquet_row_count: int


@dataclass(frozen=True)
class VerifiedRevisionManifest:
    """A revision manifest that has passed every anchor/fingerprint check.

    The sole trusted source `preflight.py` uses to locate and verify a
    revision's four canonical Parquet objects.
    """

    as_of_date: str
    release_revision: int
    revision_fingerprint: str
    fingerprint_algorithm: str
    parser_contract_version: int
    logical_lists: tuple[tuple[str, LogicalListManifestEntry], ...]

    def logical_list_entry(self, logical_list: str) -> LogicalListManifestEntry:
        for name, entry in self.logical_lists:
            if name == logical_list:
                return entry
        raise CatalogError("catalog.unknown_logical_list")


def _read_document(path: Path, *, not_found_category: str, decode_category: str) -> tuple[bytes, dict]:
    if path.is_symlink():
        raise CatalogError(not_found_category)
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise CatalogError(not_found_category) from exc

    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError(decode_category) from exc

    if not isinstance(document, dict):
        raise CatalogError(decode_category)
    return raw_bytes, document


def _require_str(value: object, *, category: str) -> str:
    if not isinstance(value, str) or not value:
        raise CatalogError(category)
    return value


def _require_int(value: object, *, category: str, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CatalogError(category)
    if minimum is not None and value < minimum:
        raise CatalogError(category)
    return value


def _require_bool(value: object, *, category: str) -> bool:
    if not isinstance(value, bool):
        raise CatalogError(category)
    return value


def _parse_release_anchor(raw: object) -> CatalogReleaseAnchor:
    if not isinstance(raw, dict) or set(raw.keys()) != _RELEASE_ANCHOR_KEYS:
        raise CatalogError("catalog.invalid_release_anchor")

    as_of_date = _require_str(raw.get("as_of_date"), category="catalog.invalid_release_anchor")
    if not _AS_OF_DATE_PATTERN.match(as_of_date):
        raise CatalogError("catalog.invalid_release_anchor")

    release_revision = _require_int(
        raw.get("release_revision"), category="catalog.invalid_release_anchor", minimum=1
    )

    revision_fingerprint = _require_str(
        raw.get("revision_fingerprint"), category="catalog.invalid_release_anchor"
    )
    if not _SHA256_PATTERN.match(revision_fingerprint):
        raise CatalogError("catalog.invalid_release_anchor")

    revision_manifest_sha256 = _require_str(
        raw.get("revision_manifest_sha256"), category="catalog.invalid_release_anchor"
    )
    if not _SHA256_PATTERN.match(revision_manifest_sha256):
        raise CatalogError("catalog.invalid_release_anchor")

    return CatalogReleaseAnchor(
        as_of_date=as_of_date,
        release_revision=release_revision,
        revision_fingerprint=revision_fingerprint,
        revision_manifest_sha256=revision_manifest_sha256,
    )


def load_input_catalog(path: str | Path) -> InputCatalog:
    """Load and strictly validate the closed, manifest-anchor-only catalog.

    Fails closed with `CatalogError` on any missing document, unsupported
    version, unknown/missing top-level or per-entry field, duplicate
    `(as_of_date, release_revision)` anchor, or any canonical object hash,
    size, schema, row count, or path smuggled into an entry (rejected
    automatically by the exact per-entry key set).
    """

    catalog_path = Path(path)
    _, document = _read_document(
        catalog_path,
        not_found_category="catalog.not_found",
        decode_category="catalog.invalid_json",
    )

    if set(document.keys()) != _CATALOG_TOP_LEVEL_KEYS:
        raise CatalogError("catalog.invalid_schema")

    contract_version = _require_int(document.get("contract_version"), category="catalog.invalid_schema")
    if contract_version != _SUPPORTED_CONTRACT_VERSION:
        raise CatalogError("catalog.unsupported_version")

    raw_releases = document.get("releases")
    if not isinstance(raw_releases, list):
        raise CatalogError("catalog.invalid_schema")

    releases = tuple(_parse_release_anchor(raw) for raw in raw_releases)

    seen: set[tuple[str, int]] = set()
    for anchor in releases:
        key = (anchor.as_of_date, anchor.release_revision)
        if key in seen:
            raise CatalogError("catalog.duplicate_release_anchor")
        seen.add(key)

    return InputCatalog(contract_version=contract_version, releases=releases)


def _hash_bytes(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def _recompute_revision_fingerprint(logical_lists: tuple[tuple[str, LogicalListManifestEntry], ...]) -> str:
    """Recompute the `ordered-source-sha256-json-v1` fingerprint from the
    manifest's own recorded raw object hashes -- mirrors
    `calico_landing.admission._revision_fingerprint` exactly so a
    substituted or reordered raw hash in the manifest cannot escape
    detection.
    """

    by_name = dict(logical_lists)
    ordered = [[name, by_name[name].raw_sha256] for name in LOGICAL_LIST_ORDER]
    framed = json.dumps(ordered, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(framed).hexdigest()


def _parse_logical_list_entry(raw: object) -> LogicalListManifestEntry:
    if not isinstance(raw, dict) or set(raw.keys()) != _LOGICAL_LIST_ENTRY_KEYS:
        raise CatalogError("catalog.malformed_manifest")

    raw_sha256 = _require_str(raw.get("raw_sha256"), category="catalog.malformed_manifest")
    if not _SHA256_PATTERN.match(raw_sha256):
        raise CatalogError("catalog.malformed_manifest")

    raw_byte_count = _require_int(raw.get("raw_byte_count"), category="catalog.malformed_manifest", minimum=0)
    parsed_record_count = _require_int(
        raw.get("parsed_record_count"), category="catalog.malformed_manifest", minimum=0
    )
    line_record_reconciled = _require_bool(
        raw.get("line_record_reconciled"), category="catalog.malformed_manifest"
    )

    parquet_sha256 = _require_str(raw.get("parquet_sha256"), category="catalog.malformed_manifest")
    if not _SHA256_PATTERN.match(parquet_sha256):
        raise CatalogError("catalog.malformed_manifest")

    parquet_row_count = _require_int(
        raw.get("parquet_row_count"), category="catalog.malformed_manifest", minimum=0
    )

    return LogicalListManifestEntry(
        raw_sha256=raw_sha256,
        raw_byte_count=raw_byte_count,
        parsed_record_count=parsed_record_count,
        line_record_reconciled=line_record_reconciled,
        parquet_sha256=parquet_sha256,
        parquet_row_count=parquet_row_count,
    )


def load_and_verify_revision_manifest(
    manifest_path: str | Path, anchor: CatalogReleaseAnchor
) -> VerifiedRevisionManifest:
    """Load, hash-verify, and strictly parse one revision's `manifest.json`.

    Fails closed with `CatalogError` if: the manifest file is a symlink or
    unreadable; its raw SHA-256 does not exactly equal
    `anchor.revision_manifest_sha256`; its decoded JSON is malformed or
    carries an unknown/missing top-level, metadata, or per-logical-list
    key; its self-reported `as_of_date`/`release_revision`/
    `revision_fingerprint` disagree with `anchor`; or the fingerprint
    recomputed from its own recorded raw object hashes disagrees with
    either the manifest's self-reported fingerprint or `anchor`'s.

    Only a manifest that survives every one of these checks is returned as
    a frozen `VerifiedRevisionManifest`.
    """

    path = Path(manifest_path)
    if path.is_symlink():
        raise CatalogError("catalog.manifest_not_found")
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise CatalogError("catalog.manifest_not_found") from exc

    if _hash_bytes(raw_bytes) != anchor.revision_manifest_sha256:
        raise CatalogError("catalog.manifest_hash_mismatch")

    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError("catalog.malformed_manifest") from exc

    if not isinstance(document, dict) or set(document.keys()) != _MANIFEST_TOP_LEVEL_KEYS:
        raise CatalogError("catalog.malformed_manifest")

    if document.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
        raise CatalogError("catalog.malformed_manifest")

    as_of_date = document.get("as_of_date")
    release_revision = document.get("release_revision")
    revision_fingerprint = document.get("revision_fingerprint")
    fingerprint_algorithm = document.get("fingerprint_algorithm")

    if (
        as_of_date != anchor.as_of_date
        or release_revision != anchor.release_revision
        or revision_fingerprint != anchor.revision_fingerprint
    ):
        raise CatalogError("catalog.manifest_anchor_mismatch")

    if not isinstance(fingerprint_algorithm, str) or fingerprint_algorithm != FINGERPRINT_ALGORITHM:
        raise CatalogError("catalog.malformed_manifest")

    metadata = document.get("metadata")
    if not isinstance(metadata, dict) or set(metadata.keys()) != _METADATA_KEYS:
        raise CatalogError("catalog.malformed_manifest")

    if metadata.get("fingerprint_algorithm") != FINGERPRINT_ALGORITHM:
        raise CatalogError("catalog.malformed_manifest")

    parser_contract_version = _require_int(
        metadata.get("parser_contract_version"), category="catalog.malformed_manifest", minimum=1
    )

    raw_logical_lists = metadata.get("logical_lists")
    if not isinstance(raw_logical_lists, dict) or set(raw_logical_lists.keys()) != set(
        LOGICAL_LIST_ORDER
    ):
        raise CatalogError("catalog.malformed_manifest")

    logical_lists = tuple(
        (name, _parse_logical_list_entry(raw_logical_lists[name])) for name in LOGICAL_LIST_ORDER
    )

    recomputed_fingerprint = _recompute_revision_fingerprint(logical_lists)
    if recomputed_fingerprint != revision_fingerprint or recomputed_fingerprint != anchor.revision_fingerprint:
        raise CatalogError("catalog.fingerprint_mismatch")

    return VerifiedRevisionManifest(
        as_of_date=as_of_date,
        release_revision=release_revision,
        revision_fingerprint=revision_fingerprint,
        fingerprint_algorithm=fingerprint_algorithm,
        parser_contract_version=parser_contract_version,
        logical_lists=logical_lists,
    )


def build_catalog_from_manifests(
    manifests: "list[tuple[str, int, str, bytes]]",
) -> InputCatalog:
    """Build an in-memory `InputCatalog` from already-hashed manifest bytes.

    `manifests` is a list of `(as_of_date, release_revision,
    revision_fingerprint, manifest_raw_bytes)` tuples -- exactly what a
    caller (fixture mode) already has in hand after admitting a revision
    through `calico_landing.admission.admit()` and reading its written
    `manifest.json` back. This function only hashes those bytes; it never
    reads or trusts any other field, mirroring exactly what a committed
    real-mode catalog would pin.
    """

    releases = tuple(
        CatalogReleaseAnchor(
            as_of_date=as_of_date,
            release_revision=release_revision,
            revision_fingerprint=revision_fingerprint,
            revision_manifest_sha256=_hash_bytes(manifest_raw_bytes),
        )
        for as_of_date, release_revision, revision_fingerprint, manifest_raw_bytes in manifests
    )
    return InputCatalog(contract_version=_SUPPORTED_CONTRACT_VERSION, releases=releases)


__all__ = [
    "FINGERPRINT_ALGORITHM",
    "CatalogError",
    "CatalogReleaseAnchor",
    "InputCatalog",
    "LogicalListManifestEntry",
    "VerifiedRevisionManifest",
    "load_input_catalog",
    "load_and_verify_revision_manifest",
    "build_catalog_from_manifests",
]
