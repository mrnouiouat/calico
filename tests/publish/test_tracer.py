"""End-to-end tracer for the bounded, atomic publication path."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from calico_dbt import runner
from calico_landing.contracts import LOGICAL_LIST_ORDER
from calico_publish.allowlist import Allowlist, AllowlistError, load_allowlist
from calico_publish.export import StagedExport, export_all
from calico_publish.gate import verify
from calico_publish.manifest import (
    MANIFEST_DOCUMENT_KEYS,
    AcceptedRelease,
    SourceObjectRecord,
    compute_revision_fingerprint,
    project_published_manifest,
)
from calico_publish.transaction import CARRIED_FORWARD_PATHS, TransactionError, publish_tree

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALLOWLIST_PATH = _REPO_ROOT / "contracts" / "publication-exports-v1.json"
_TARGET_REF = "refs/heads/published-data"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _safe_release() -> AcceptedRelease:
    source_objects = tuple(
        SourceObjectRecord(
            source_list=name,
            sha256=str(index) * 64,
            byte_size=1,
            row_count=0,
        )
        for index, name in enumerate(sorted(LOGICAL_LIST_ORDER), start=1)
    )
    return AcceptedRelease(
        as_of_date="2026-01-01",
        release_revision=1,
        revision_fingerprint=compute_revision_fingerprint(
            {item.source_list: item.sha256 for item in source_objects}
        ),
        source_objects=source_objects,
    )


def _manifest(allowlist: Allowlist, staged: tuple[StagedExport, ...]):
    return project_published_manifest(
        allowlist=allowlist,
        staged_exports=staged,
        accepted_releases=(_safe_release(),),
        eligible_key_count=0,
        parser_contract_version="registry-csv-contract-v1",
        toolchain={
            "python": "3.13.15",
            "dbt_core": "1.10.23",
            "dbt_duckdb": "1.10.1",
            "duckdb": "1.5.5",
        },
    )


class PublicationTracerTests(unittest.TestCase):
    def test_transaction_rejects_paths_outside_publication_layout_before_git_io(self) -> None:
        for path in (
            "private.duckdb",
            "capture-status.json",
            "exports/../private.csv",
            "exports\\..\\private.csv",
            "C:" + "/private.csv",
        ):
            with self.subTest(case=path):
                with patch("calico_publish.transaction._run") as git_io:
                    with self.assertRaises(TransactionError) as raised:
                        publish_tree(
                            repo_dir=_REPO_ROOT,
                            staging_dir=_REPO_ROOT,
                            staged_files=(path, "manifest/published-manifest-v1.json"),
                            remote="origin",
                            target_ref=_TARGET_REF,
                            commit_subject="Rejected fixture publication",
                            author_name="Fixture Publisher",
                            author_email="fixture" + "@" + "example.invalid",
                        )
                    git_io.assert_not_called()
                self.assertEqual(raised.exception.category, "transaction.staged_file_missing")

    def test_allowlist_rejects_wrong_json_types_with_safe_category(self) -> None:
        for value in (True, [], {}):
            with self.subTest(case=type(value).__name__):
                document = json.loads(_ALLOWLIST_PATH.read_text(encoding="utf-8"))
                if isinstance(value, bool):
                    document["schema_version"] = value
                else:
                    document["exports"][0]["export_class"] = value
                with tempfile.TemporaryDirectory(prefix="calico-allowlist-") as temp_dir:
                    path = Path(temp_dir) / "allowlist.json"
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(AllowlistError) as raised:
                        load_allowlist(path)
                self.assertEqual(raised.exception.category, "allowlist.invalid_schema")

    def test_real_fixture_dag_exports_gates_manifests_and_publishes_once(self) -> None:
        allowlist = load_allowlist(_ALLOWLIST_PATH)
        captured: dict[str, tuple[StagedExport, ...]] = {}

        with tempfile.TemporaryDirectory(prefix="calico-publish-tracer-") as temp_dir:
            root = Path(temp_dir)
            staging = root / "staging"
            repeat_staging = root / "repeat-staging"

            def export(duckdb_path: Path) -> None:
                captured["first"] = export_all(duckdb_path, allowlist, staging)
                captured["second"] = export_all(duckdb_path, allowlist, repeat_staging)

            outcome = runner.build(mode="fixture", export=export)
            self.assertEqual(outcome.status, "success", outcome.category)
            staged = captured["first"]
            self.assertEqual(len(staged), 11)
            self.assertEqual(
                [item.sha256 for item in staged],
                [item.sha256 for item in captured["second"]],
            )
            for item in staged:
                first = (staging / item.relative_path).read_bytes()
                second = (repeat_staging / item.relative_path).read_bytes()
                entry = next(entry for entry in allowlist.exports if entry.export_name == item.export_name)
                self.assertEqual(first, second)
                self.assertFalse(first.startswith(b"\xef\xbb\xbf"))
                self.assertNotIn(b"\r", first)
                self.assertEqual(first.split(b"\n", 1)[0].decode("utf-8"), ",".join(entry.columns))

            manifest = _manifest(allowlist, staged)
            manifest_document = json.loads(manifest.to_json())
            self.assertEqual(set(manifest_document), MANIFEST_DOCUMENT_KEYS)
            self.assertFalse(any("time" in key.lower() for key in manifest_document))
            manifest_path = staging / "manifest" / "published-manifest-v1.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(manifest.to_json(), encoding="ascii", newline="\n")
            result = verify(staging, allowlist, manifest_path)
            self.assertTrue(result.passed, [item.render() for item in result.violations])

            bare = root / "published.git"
            publisher = root / "publisher"
            _git(root, "init", "--bare", str(bare))
            publisher.mkdir()
            _git(publisher, "init")
            _git(publisher, "config", "user.name", "Fixture Publisher")
            _git(publisher, "config", "user.email", "fixture" + "@" + "example.invalid")
            for path in CARRIED_FORWARD_PATHS:
                (publisher / path).write_text('{"status":"seeded"}\n', encoding="ascii", newline="\n")
            _git(publisher, "add", *CARRIED_FORWARD_PATHS)
            _git(publisher, "commit", "-m", "Seed publication status documents")
            _git(publisher, "branch", "-M", "published-data")
            _git(publisher, "remote", "add", "origin", str(bare))
            _git(publisher, "push", "origin", f"HEAD:{_TARGET_REF}")
            seeded_tip = _git(publisher, "rev-parse", "HEAD")
            seeded_blobs = {
                path: _git(publisher, "rev-parse", f"{seeded_tip}:{path}")
                for path in CARRIED_FORWARD_PATHS
            }

            staged_files = tuple(item.relative_path for item in staged) + (
                "manifest/published-manifest-v1.json",
            )
            published = publish_tree(
                repo_dir=publisher,
                staging_dir=staging,
                staged_files=staged_files,
                remote="origin",
                target_ref=_TARGET_REF,
                commit_subject="Publish fixture tracer",
                author_name="Fixture Publisher",
                author_email="fixture" + "@" + "example.invalid",
            )
            self.assertEqual(published.status, "published")
            tip = _git(publisher, "ls-remote", "origin", _TARGET_REF).split()[0]
            self.assertEqual(_git(publisher, "rev-list", "--count", tip), "2")
            self.assertEqual(
                set(_git(publisher, "ls-tree", "-r", "--name-only", tip).splitlines()),
                {
                    "authorization-probe-status.json",
                    "capture-status.json",
                    *(item.relative_path for item in staged),
                    "manifest/published-manifest-v1.json",
                },
            )
            for path, blob in seeded_blobs.items():
                self.assertEqual(_git(publisher, "rev-parse", f"{tip}:{path}"), blob)

            unchanged = publish_tree(
                repo_dir=publisher,
                staging_dir=staging,
                staged_files=staged_files,
                remote="origin",
                target_ref=_TARGET_REF,
                commit_subject="Publish fixture tracer",
                author_name="Fixture Publisher",
                author_email="fixture" + "@" + "example.invalid",
            )
            self.assertEqual(unchanged.status, "no_change")
            self.assertIsNone(unchanged.commit_sha)
            self.assertEqual(_git(publisher, "ls-remote", "origin", _TARGET_REF).split()[0], tip)

            interrupted_staging = root / "interrupted-staging"
            shutil.copytree(staging, interrupted_staging)
            export_path = interrupted_staging / staged[0].relative_path
            export_path.write_bytes(export_path.read_bytes() + b"\n")

            def interrupt(stage: str) -> None:
                if stage == "before_push":
                    raise RuntimeError("simulated interruption")

            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                publish_tree(
                    repo_dir=publisher,
                    staging_dir=interrupted_staging,
                    staged_files=staged_files,
                    remote="origin",
                    target_ref=_TARGET_REF,
                    commit_subject="Interrupted fixture tracer",
                    author_name="Fixture Publisher",
                    author_email="fixture" + "@" + "example.invalid",
                    failure_hook=interrupt,
                )
            self.assertEqual(_git(publisher, "ls-remote", "origin", _TARGET_REF).split()[0], tip)

    def test_header_only_named_export_passes(self) -> None:
        allowlist = load_allowlist(_ALLOWLIST_PATH)
        entry = next(item for item in allowlist.exports if item.export_class == "named_history")
        subset = Allowlist(allowlist.schema_version, allowlist.allowlist_version, (entry,))
        with tempfile.TemporaryDirectory(prefix="calico-empty-publish-") as temp_dir:
            export_dir = Path(temp_dir) / "exports"
            export_dir.mkdir()
            (export_dir / entry.file_name).write_text(
                ",".join(entry.columns) + "\n", encoding="utf-8", newline="\n"
            )
            result = verify(temp_dir, subset)
        self.assertTrue(result.passed)

    def test_allowlist_rejects_aggregate_identity_column(self) -> None:
        document = json.loads(_ALLOWLIST_PATH.read_text(encoding="utf-8"))
        aggregate = next(item for item in document["exports"] if item["export_class"] == "aggregate")
        aggregate["columns"].append("organization_name")
        with tempfile.TemporaryDirectory(prefix="calico-allowlist-") as temp_dir:
            path = Path(temp_dir) / "allowlist.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(AllowlistError) as raised:
                load_allowlist(path)
        self.assertEqual(raised.exception.category, "allowlist.aggregate_identity_column")

    def test_allowlist_rejects_duplicate_file_name(self) -> None:
        document = json.loads(_ALLOWLIST_PATH.read_text(encoding="utf-8"))
        document["exports"][1]["file_name"] = document["exports"][0]["file_name"]
        with tempfile.TemporaryDirectory(prefix="calico-allowlist-") as temp_dir:
            path = Path(temp_dir) / "allowlist.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(AllowlistError) as raised:
                load_allowlist(path)
        self.assertEqual(raised.exception.category, "allowlist.duplicate_file_name")


if __name__ == "__main__":
    unittest.main()
