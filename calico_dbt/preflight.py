"""Verify-copy-bind trust boundary between an admitted store and dbt (T-03-01..T-03-08).

`prepare_runtime_input` is the sole path from a caller-supplied store root
plus a closed `catalog.InputCatalog` to a populated, on-disk DuckDB database
dbt can read. For every catalog-anchored revision it: resolves the revision
directory at its exact, deterministic path (never a glob); rejects any
symlink/reparse alias anywhere along that path; verifies the revision's
`manifest.json` against the catalog anchor
(`catalog.load_and_verify_revision_manifest`); verifies every one of the
revision's four canonical Parquet objects against that now-trusted
manifest's own recorded hash, schema, and row count; copies each verified
object's bytes to an opaque, runner-owned alias; and rehashes the copy
before it is ever bound. Only after every revision passes does this module
load the runner-owned on-disk DuckDB database, close its connection, and
return -- proving a fresh process can already see the populated relations
before dbt (a subprocess) ever starts.

This module never opens, trusts, or forwards a caller path, glob, or
`union_by_name` into SQL/Jinja -- every relation is built from an explicit,
individually verified, individually bound file path. It never deletes
anything outside the temporary root the caller supplies; cleanup of that
root is the caller's (`runner.py`'s) responsibility on every exit path.

Every failure crosses this module's boundary as a `PreflightError` carrying
only a fixed safe `category` -- never an offending path, byte, or parsed
value (mirrored from `catalog.CatalogError` and `calico_landing.store
.StoreError`'s non-echo exception discipline).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import duckdb

from calico_dbt.catalog import (
    CatalogError,
    InputCatalog,
    VerifiedRevisionManifest,
    load_and_verify_revision_manifest,
)
from calico_landing.candidate import CandidateError, reject_store_in_git_worktree
from calico_landing.contracts import LOGICAL_LIST_ORDER, load_csv_contract
from calico_landing.store import PromotedRevision, StoreError, read_promoted_releases

#: Resolved the same way `calico_landing.admission` resolves it -- the one
#: locked current-release CSV contract, used here only to read the fixed
#: eleven safe column names; never to reparse a raw payload.
_CSV_CONTRACT_PATH = (
    Path(__file__).resolve().parent.parent / "contracts" / "ag-registry-csv-v1.json"
)

#: Fixed structural provenance columns `calico_landing.parquet` appends
#: after every contract source header (mirrors `parquet._PROVENANCE_COLUMNS`).
_PROVENANCE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("source_list", "VARCHAR"),
    ("source_line_no", "BIGINT"),
)

#: Fixed schema name every runtime relation is created under (D-03).
RUNTIME_SCHEMA = "runtime_input"

#: Fixed on-disk database filename inside the runner-owned temporary root.
DUCKDB_FILENAME = "calico-runtime.duckdb"

#: Fixed opaque-copy subdirectory name inside the runner-owned temporary root.
_OPAQUE_INPUTS_DIRNAME = "inputs"

_RELEASES_DIRNAME = "releases"
_CANONICAL_DIRNAME = "canonical"
_MANIFEST_FILENAME = "manifest.json"

_HASH_CHUNK_BYTES = 1024 * 1024


class PreflightError(Exception):
    """Raised on any containment, hash, schema, count, or pointer-consistency
    failure. Carries only a fixed safe `category` -- never an offending
    path, byte, or parsed value.
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _relation_name(logical_list: str) -> str:
    return logical_list.replace("-", "_")


@dataclass(frozen=True)
class RuntimeInputBinding:
    """Safe outcome metadata for one completed preflight pass.

    Never carries a source path -- only the runner-owned temp-root and
    on-disk database paths the caller already knows, plus safe verification
    counts for `SafeBuildProof`.
    """

    temp_root: Path
    duckdb_path: Path
    verified_release_count: int
    verified_object_count: int


def _resolve_store_root(store_root: str | Path) -> Path:
    try:
        reject_store_in_git_worktree(store_root)
    except CandidateError as exc:
        raise PreflightError("preflight.store_in_worktree") from exc

    raw = Path(store_root)
    if raw.is_symlink():
        raise PreflightError("preflight.invalid_store_root")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise PreflightError("preflight.invalid_store_root") from exc
    if not resolved.is_dir():
        raise PreflightError("preflight.invalid_store_root")
    return resolved


def _resolve_revision_dir(store_root: Path, anchor) -> Path:
    """Resolve one revision's directory at its exact, deterministic path.

    Never globs -- the directory name is fully determined by the anchor's
    own `release_revision` and the first eight hex characters of its
    `revision_fingerprint`, exactly mirroring
    `calico_landing.store`'s own naming convention. Rejects a symlink or
    reparse alias at any path component, and rejects any resolution that
    would escape the expected parent directory.
    """

    date_dir = store_root / _RELEASES_DIRNAME / anchor.as_of_date
    if date_dir.is_symlink():
        raise PreflightError("preflight.link_rejected")

    expected_name = f"rev-{anchor.release_revision:04d}-{anchor.revision_fingerprint[:8]}"
    candidate = date_dir / expected_name
    if candidate.is_symlink():
        raise PreflightError("preflight.link_rejected")

    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise PreflightError("preflight.revision_not_found") from exc

    try:
        resolved_date_dir = date_dir.resolve(strict=True)
    except OSError as exc:
        raise PreflightError("preflight.revision_not_found") from exc

    if resolved.parent != resolved_date_dir or resolved.name != expected_name:
        raise PreflightError("preflight.link_rejected")
    if not resolved.is_dir():
        raise PreflightError("preflight.revision_not_found")

    return resolved


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while chunk := handle.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
    except OSError as exc:
        raise PreflightError("preflight.object_not_found") from exc
    return digest.hexdigest()


def _expected_schema(contract_headers: tuple[str, ...]) -> dict[str, str]:
    columns: dict[str, str] = {name: "VARCHAR" for name in contract_headers}
    for name, sql_type in _PROVENANCE_COLUMNS:
        columns[name] = sql_type
    return columns


def _verify_and_copy_object(
    revision_dir: Path,
    logical_list: str,
    manifest: VerifiedRevisionManifest,
    opaque_dir: Path,
    expected_schema: dict[str, str],
    seen_real_paths: set[Path],
) -> Path:
    entry = manifest.logical_list_entry(logical_list)

    source_path = revision_dir / _CANONICAL_DIRNAME / f"{logical_list}.parquet"
    if source_path.is_symlink():
        raise PreflightError("preflight.link_rejected")
    try:
        resolved_source = source_path.resolve(strict=True)
    except OSError as exc:
        raise PreflightError("preflight.object_not_found") from exc
    if resolved_source.parent != (revision_dir / _CANONICAL_DIRNAME).resolve(strict=True):
        raise PreflightError("preflight.link_rejected")
    if not resolved_source.is_file():
        raise PreflightError("preflight.object_not_found")
    if resolved_source in seen_real_paths:
        raise PreflightError("preflight.duplicate_object_target")
    seen_real_paths.add(resolved_source)

    if _hash_file(resolved_source) != entry.parquet_sha256:
        raise PreflightError("preflight.object_hash_mismatch")

    try:
        with duckdb.connect(":memory:") as connection:
            relation = connection.read_parquet(str(resolved_source))
            actual_columns = tuple(relation.columns)
            actual_types = tuple(str(sql_type) for sql_type in relation.types)
            actual_row_count = len(relation.fetchall())
    except duckdb.Error as exc:
        raise PreflightError("preflight.object_schema_invalid") from exc

    expected_columns = tuple(expected_schema.keys())
    expected_types = tuple(expected_schema.values())
    if actual_columns != expected_columns or actual_types != expected_types:
        raise PreflightError("preflight.object_schema_invalid")
    if actual_row_count != entry.parquet_row_count:
        raise PreflightError("preflight.object_count_mismatch")

    opaque_path = opaque_dir / f"{len(seen_real_paths):04d}.parquet"
    try:
        opaque_path.write_bytes(resolved_source.read_bytes())
    except OSError as exc:
        raise PreflightError("preflight.copy_failed") from exc

    if _hash_file(opaque_path) != entry.parquet_sha256:
        raise PreflightError("preflight.copy_hash_mismatch")

    return opaque_path


def _configure_connection(connection: "duckdb.DuckDBPyConnection", temp_root: Path) -> None:
    """Apply bounded, safe DuckDB settings. Every statement is wrapped so an
    unsupported setting on the pinned DuckDB build degrades gracefully
    rather than failing the whole preflight (T-03-01 defense in depth,
    'where supported by pinned DuckDB').
    """

    safe_pragmas = (
        "SET autoinstall_known_extensions=false",
        "SET autoload_known_extensions=false",
        "SET allow_community_extensions=false",
        "SET allow_unsigned_extensions=false",
        "SET threads=2",
        "SET memory_limit='512MB'",
        f"SET temp_directory='{(temp_root / 'duckdb_tmp').as_posix()}'",
    )
    for statement in safe_pragmas:
        try:
            connection.execute(statement)
        except duckdb.Error:
            continue


def _validate_pointer_consistency(
    store_root: Path, catalog: InputCatalog
) -> dict[str, PromotedRevision]:
    """Cross-check the store's atomic promotion pointer against the closed
    catalog anchors (D-06). A present pointer entry naming a release the
    catalog does not anchor for that exact date, or naming a fingerprint
    that disagrees with the catalog anchor for that same
    `(as_of_date, release_revision)`, fails closed. An absent pointer entry
    for an otherwise-anchored date is never treated as a failure here --
    which revision counts as "the" promotion when no pointer exists is a
    dbt/SQL decision (D-06), never a Python fallback.
    """

    try:
        promotions = read_promoted_releases(store_root)
    except StoreError as exc:
        raise PreflightError("preflight.malformed_pointer") from exc

    for as_of_date, promoted in promotions.items():
        anchor = catalog.anchor_for(as_of_date, promoted.release_revision)
        if anchor is None or anchor.revision_fingerprint != promoted.revision_fingerprint:
            raise PreflightError("preflight.pointer_inconsistent")

    return promotions


def prepare_runtime_input(
    *,
    store_root: str | Path,
    catalog: InputCatalog,
    temp_root: str | Path,
) -> RuntimeInputBinding:
    """Verify every catalog-anchored revision and bind it into one on-disk
    DuckDB database under `temp_root`, then close the connection.

    `temp_root` must already exist and be owned exclusively by the caller
    for the duration of this build; this function only ever writes beneath
    it. Raises `PreflightError` on the first containment, hash, schema,
    count, or pointer-consistency failure -- no partial database is ever
    left in a state a later step could mistake for a fully verified bind
    (a failure here is expected to be followed by whole-root cleanup by the
    caller).
    """

    resolved_store_root = _resolve_store_root(store_root)
    resolved_temp_root = Path(temp_root).resolve(strict=True)

    contract = load_csv_contract(_CSV_CONTRACT_PATH)
    expected_schema = _expected_schema(contract.headers)

    opaque_dir = resolved_temp_root / _OPAQUE_INPUTS_DIRNAME
    opaque_dir.mkdir(parents=True, exist_ok=True)
    (resolved_temp_root / "duckdb_tmp").mkdir(parents=True, exist_ok=True)

    verified_manifests: list[VerifiedRevisionManifest] = []
    opaque_paths: dict[tuple[str, int, str], Path] = {}
    seen_real_paths: set[Path] = set()

    for anchor in catalog.releases:
        revision_dir = _resolve_revision_dir(resolved_store_root, anchor)

        manifest_path = revision_dir / _MANIFEST_FILENAME
        if manifest_path.is_symlink():
            raise PreflightError("preflight.link_rejected")
        try:
            verified_manifest = load_and_verify_revision_manifest(manifest_path, anchor)
        except CatalogError as exc:
            raise PreflightError("preflight.manifest_verification_failed") from exc
        verified_manifests.append(verified_manifest)

        for logical_list in LOGICAL_LIST_ORDER:
            opaque_path = _verify_and_copy_object(
                revision_dir,
                logical_list,
                verified_manifest,
                opaque_dir,
                expected_schema,
                seen_real_paths,
            )
            opaque_paths[(logical_list, anchor.release_revision, anchor.as_of_date)] = opaque_path

    promotions = _validate_pointer_consistency(resolved_store_root, catalog)

    duckdb_path = resolved_temp_root / DUCKDB_FILENAME
    connection = duckdb.connect(str(duckdb_path))
    try:
        _configure_connection(connection, resolved_temp_root)
        connection.execute(f"CREATE SCHEMA IF NOT EXISTS {RUNTIME_SCHEMA}")

        for logical_list in LOGICAL_LIST_ORDER:
            union_parts = []
            for anchor in catalog.releases:
                opaque_path = opaque_paths[(logical_list, anchor.release_revision, anchor.as_of_date)]
                union_parts.append(
                    "SELECT *, "
                    f"'{anchor.as_of_date}' AS as_of_date, "
                    f"{anchor.release_revision} AS release_revision, "
                    f"'{anchor.revision_fingerprint}' AS revision_fingerprint "
                    f"FROM read_parquet('{opaque_path.as_posix()}')"
                )
            union_sql = " UNION ALL ".join(union_parts) if union_parts else _empty_select(
                expected_schema
            )
            relation_name = _relation_name(logical_list)
            connection.execute(
                f'CREATE TABLE {RUNTIME_SCHEMA}."{relation_name}" AS {union_sql}'
            )

        connection.execute(
            f"CREATE TABLE {RUNTIME_SCHEMA}.revision_catalog "
            "(as_of_date VARCHAR, release_revision BIGINT, revision_fingerprint VARCHAR, "
            "parser_contract_version BIGINT)"
        )
        for manifest in verified_manifests:
            connection.execute(
                f"INSERT INTO {RUNTIME_SCHEMA}.revision_catalog VALUES (?, ?, ?, ?)",
                [
                    manifest.as_of_date,
                    manifest.release_revision,
                    manifest.revision_fingerprint,
                    manifest.parser_contract_version,
                ],
            )

        connection.execute(
            f"CREATE TABLE {RUNTIME_SCHEMA}.promotion_catalog "
            "(as_of_date VARCHAR, release_revision BIGINT, revision_fingerprint VARCHAR)"
        )
        for as_of_date, promoted in sorted(promotions.items()):
            connection.execute(
                f"INSERT INTO {RUNTIME_SCHEMA}.promotion_catalog VALUES (?, ?, ?)",
                [as_of_date, promoted.release_revision, promoted.revision_fingerprint],
            )
    except duckdb.Error as exc:
        raise PreflightError("preflight.database_load_failed") from exc
    finally:
        connection.close()

    return RuntimeInputBinding(
        temp_root=resolved_temp_root,
        duckdb_path=duckdb_path,
        verified_release_count=len(catalog.releases),
        verified_object_count=len(opaque_paths),
    )


def _empty_select(expected_schema: dict[str, str]) -> str:
    columns = ", ".join(f"NULL::{sql_type} AS \"{name}\"" for name, sql_type in expected_schema.items())
    extra = (
        "NULL::VARCHAR AS as_of_date, NULL::BIGINT AS release_revision, "
        "NULL::VARCHAR AS revision_fingerprint"
    )
    return f"SELECT {columns}, {extra} WHERE FALSE"


__all__ = [
    "RUNTIME_SCHEMA",
    "DUCKDB_FILENAME",
    "PreflightError",
    "RuntimeInputBinding",
    "prepare_runtime_input",
]
