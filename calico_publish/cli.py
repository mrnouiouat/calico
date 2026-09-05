"""Value-free CLI for build-once, gate, scan, and atomic publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Callable

from calico_capture.archive import Archive, ArchiveError
from calico_capture.b2 import B2ReadOnlyArchive
from calico_capture.cli import _resolve_local_manifest_path, _restore_build
from calico_dbt.catalog import InputCatalog, load_and_verify_revision_manifest, load_input_catalog
from calico_dbt.runner import BuildOutcome, build
from calico_publish.allowlist import Allowlist, AllowlistError, load_allowlist
from calico_publish.export import StagedExport, export_all
from calico_publish.gate import GateError, verify
from calico_publish.inventory import InventoryError, check_inventory, load_inventory_document
from calico_publish.manifest import AcceptedRelease, ManifestError, SourceObjectRecord, project_published_manifest
from calico_publish.transaction import TransactionError, publish_tree
from tools.privacy_scan.policy import PolicyError, load_policy
from tools.privacy_scan.scanner import ScanPathError, scan_paths

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REAL_ALLOWLIST_PATH = _REPO_ROOT / "contracts" / "publication-exports-v1.json"
_REAL_CATALOG_PATH = _REPO_ROOT / "contracts" / "dbt-input-catalog-v1.json"
_POLICY_PATH = _REPO_ROOT / "policies" / "publishable-tree.json"
_MANIFEST_RELATIVE_PATH = Path("manifest") / "published-manifest-v1.json"
_MODES = ("fixture", "real")
_PUBLISH_KEY_ID_ENV = "CALICO_B2_PUBLISH_KEY_ID"
_PUBLISH_KEY_ENV = "CALICO_B2_PUBLISH_KEY"
_TARGET_REF = "published-data"
_TOOLCHAIN = {"python": "3.13.15", "dbt_core": "1.10.23", "dbt_duckdb": "1.10.1", "duckdb": "1.5.5"}


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        self.exit(2, "calico_publish: usage error\n")


def _dict_json(document: dict[str, object]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _allowlist_path(args: argparse.Namespace) -> Path:
    if getattr(args, "mode", "real") == "real":
        return _REAL_ALLOWLIST_PATH
    fixture_path = Path(args.staging) / "publication-exports-v1.json"
    if fixture_path.is_symlink() or (fixture_path.exists() and not fixture_path.is_file()):
        raise AllowlistError("allowlist.invalid_schema")
    return fixture_path if fixture_path.exists() else _REAL_ALLOWLIST_PATH


def _default_publication_archive_factory() -> Archive:
    key_id = os.environ.get(_PUBLISH_KEY_ID_ENV)
    key = os.environ.get(_PUBLISH_KEY_ENV)
    if not key_id or not key:
        raise ArchiveError("archive.credential_missing")
    return B2ReadOnlyArchive.authorize(key_id, key)


def _accepted_releases(
    mode: str, store: Path, catalog_loader: Callable[[], InputCatalog]
) -> tuple[tuple[AcceptedRelease, ...], str]:
    if mode == "fixture":
        release = AcceptedRelease(
            "2026-01-01", 1, "0" * 64,
            (SourceObjectRecord("registry", "0" * 64, 0, 0),),
        )
        return (release,), "registry-csv-contract-v1"
    catalog = catalog_loader()
    releases: list[AcceptedRelease] = []
    parser_versions: set[int] = set()
    for anchor in sorted(catalog.releases, key=lambda item: (item.as_of_date, item.release_revision)):
        verified = load_and_verify_revision_manifest(_resolve_local_manifest_path(store, anchor), anchor)
        parser_versions.add(verified.parser_contract_version)
        releases.append(AcceptedRelease(
            verified.as_of_date,
            verified.release_revision,
            verified.revision_fingerprint,
            tuple(
                SourceObjectRecord(name, record.raw_sha256, record.raw_byte_count, record.parsed_record_count)
                for name, record in sorted(verified.logical_lists)
            ),
        ))
    if len(parser_versions) != 1:
        raise ManifestError("manifest.invalid_schema")
    return tuple(releases), f"registry-csv-contract-v{next(iter(parser_versions))}"


def _prepare_publication(
    args: argparse.Namespace,
    *,
    allowlist_loader: Callable[[str | Path], Allowlist],
    build_runner: Callable[..., BuildOutcome],
    exporter: Callable[[str | Path, Allowlist, str | Path], tuple[StagedExport, ...]],
    archive_factory: Callable[[], Archive] | None,
    catalog_loader: Callable[[], InputCatalog],
) -> tuple[Path, Allowlist, tuple[StagedExport, ...], tuple[str, ...]]:
    staging = Path(args.staging)
    staging.mkdir(parents=True, exist_ok=True)
    allowlist = allowlist_loader(_allowlist_path(args))
    staged: tuple[StagedExport, ...] = ()

    def final_build(store: Path | None = None) -> BuildOutcome:
        def export_hook(database: Path) -> None:
            nonlocal staged
            staged = exporter(database, allowlist, staging)
        return build_runner(
            mode=args.mode, store=store if args.mode == "real" else None,
            select=None, export=export_hook,
        )

    if args.mode == "real":
        if not args.store:
            raise ManifestError("manifest.missing_input")
        document, code = _restore_build(
            args.store,
            archive_factory=archive_factory or _default_publication_archive_factory,
            catalog_loader=catalog_loader,
            final_build=final_build,
        )
        if code != 0 or document.get("category") != "restore_build.completed":
            raise ManifestError("manifest.missing_input")
    else:
        if args.store:
            raise ManifestError("manifest.invalid_schema")
        outcome = final_build()
        if not outcome.succeeded:
            raise ManifestError("manifest.missing_input")
    if not staged:
        raise ManifestError("manifest.empty_exports")

    releases, parser_version = _accepted_releases(args.mode, Path(args.store or staging), catalog_loader)
    eligible_export = next(
        (item for item in staged if item.export_name == "dim_public_organizations"),
        staged[0] if args.mode == "fixture" else None,
    )
    if eligible_export is None:
        raise ManifestError("manifest.missing_input")
    eligible = eligible_export.row_count
    manifest = project_published_manifest(
        allowlist=allowlist, staged_exports=staged, accepted_releases=releases,
        eligible_key_count=eligible, parser_contract_version=parser_version, toolchain=_TOOLCHAIN,
    )
    manifest_path = staging / _MANIFEST_RELATIVE_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest.to_json(), encoding="utf-8", newline="\n")
    paths = tuple(sorted([*(item.relative_path for item in staged), _MANIFEST_RELATIVE_PATH.as_posix()]))
    return staging, allowlist, staged, paths


def _run_verify(args: argparse.Namespace, *, runtime: dict[str, object]) -> int:
    loader = runtime["allowlist_loader"]
    assert callable(loader)
    allowlist = loader(_allowlist_path(args))
    staging = Path(args.staging)
    result = verify(staging, allowlist, staging / _MANIFEST_RELATIVE_PATH)
    if result.passed:
        print(_dict_json({"category": "gate.verified", "violation_count": 0}))
        return 0
    for finding in result.violations:
        print(finding.render())
    print("gate.violations_found", file=sys.stderr)
    return 1


def _run_export(args: argparse.Namespace, *, runtime: dict[str, object]) -> int:
    _prepare_publication(args, **runtime["prepare_kwargs"])
    print(_dict_json({"category": "export.completed"}))
    return 0


def _run_publish(args: argparse.Namespace, *, runtime: dict[str, object]) -> int:
    if args.target_ref != _TARGET_REF:
        raise TransactionError("transaction.parent_not_found")
    staging, allowlist, staged, paths = _prepare_publication(args, **runtime["prepare_kwargs"])
    before_scan = _hash_explicit_files(staging, paths)
    result = verify(staging, allowlist, staging / _MANIFEST_RELATIVE_PATH)
    if not result.passed:
        for finding in result.violations:
            print(finding.render())
        print("gate.violations_found", file=sys.stderr)
        return 1
    findings = scan_paths(staging, paths, load_policy(_POLICY_PATH))
    if findings:
        for finding in findings:
            print(finding.render())
        print("privacy_scan.findings", file=sys.stderr)
        return 1
    after_scan = _hash_explicit_files(staging, paths)
    if after_scan != before_scan:
        raise ScanPathError("privacy_scan.file_changed")
    if args.dry_run:
        print(_dict_json({"category": "publish.dry_run_verified"}))
        return 0
    publisher = runtime["transaction_publisher"]
    assert callable(publisher)
    published = publisher(
        repo_dir=_REPO_ROOT, staging_dir=staging, staged_files=paths,
        remote=args.remote, target_ref=args.target_ref,
        commit_subject="Publish accepted registry release",
        author_name="Calico Publication Bot",
        author_email="calico-publish-bot" + "@" + "users.noreply.github.com",
        expected_sha256=after_scan,
    )
    print(_dict_json({"category": f"publish.{published.status}"}))
    return 0


def _hash_explicit_files(root: Path, paths: tuple[str, ...]) -> dict[str, str]:
    """Hash the explicit set with no traversal or value-bearing failures."""

    hashes: dict[str, str] = {}
    for relative in paths:
        candidate = root.joinpath(*relative.split("/"))
        if candidate.is_symlink() or not candidate.is_file():
            raise ScanPathError("privacy_scan.non_regular_file")
        digest = hashlib.sha256()
        try:
            with candidate.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise ScanPathError("privacy_scan.unreadable_file") from exc
        hashes[relative] = digest.hexdigest()
    return hashes


def _run_inventory(args: argparse.Namespace, *, runtime: dict[str, object]) -> int:
    loader = runtime["allowlist_loader"]
    assert callable(loader)
    findings = check_inventory(load_inventory_document(args.inventory), loader(_REAL_ALLOWLIST_PATH))
    if findings:
        for finding in findings:
            print(finding.render())
        print("inventory.findings", file=sys.stderr)
        return 1
    print(_dict_json({"category": "inventory.verified"}))
    return 0


def _add_build_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", required=True, choices=_MODES)
    parser.add_argument("--store")
    parser.add_argument("--staging", required=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="calico_publish", description="Build and verify closed publication bytes.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--mode", required=True, choices=_MODES)
    verify_parser.add_argument("--staging", required=True)
    _add_build_arguments(subparsers.add_parser("export"))
    publish = subparsers.add_parser("publish")
    _add_build_arguments(publish)
    publish.add_argument("--remote", required=True)
    publish.add_argument("--target-ref", required=True)
    publish.add_argument("--dry-run", action="store_true")
    inventory = subparsers.add_parser("check-inventory")
    inventory.add_argument("--inventory", required=True)
    return parser


_COMMANDS = {"check-inventory": _run_inventory, "export": _run_export, "publish": _run_publish, "verify": _run_verify}


def main(
    argv: list[str] | None = None,
    *,
    allowlist_loader: Callable[[str | Path], Allowlist] = load_allowlist,
    build_runner: Callable[..., BuildOutcome] = build,
    exporter: Callable[[str | Path, Allowlist, str | Path], tuple[StagedExport, ...]] = export_all,
    archive_factory: Callable[[], Archive] | None = None,
    catalog_loader: Callable[[], InputCatalog] = lambda: load_input_catalog(_REAL_CATALOG_PATH),
    transaction_publisher: Callable[..., object] = publish_tree,
) -> int:
    args = _build_parser().parse_args(argv)
    runtime = {
        "allowlist_loader": allowlist_loader,
        "transaction_publisher": transaction_publisher,
        "prepare_kwargs": {
            "allowlist_loader": allowlist_loader, "build_runner": build_runner, "exporter": exporter,
            "archive_factory": archive_factory, "catalog_loader": catalog_loader,
        },
    }
    try:
        return _COMMANDS[args.command](args, runtime=runtime)
    except (GateError, AllowlistError, ManifestError, InventoryError, TransactionError,
            PolicyError, ScanPathError, ArchiveError) as exc:
        print(_dict_json({"category": exc.category}))
        print(exc.category, file=sys.stderr)
        return 1
    except Exception:
        print(_dict_json({"category": "cli.unexpected_error"}))
        print("cli.unexpected_error", file=sys.stderr)
        return 3


__all__ = ["main"]
