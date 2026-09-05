"""Exit-code and non-echo tests for the publication CLI (07-04)."""

from __future__ import annotations

import io
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from calico_dbt.catalog import load_input_catalog
from calico_publish.allowlist import AllowlistError, load_allowlist
from calico_publish.cli import main
from calico_dbt.runner import BuildOutcome
from calico_landing.contracts import LOGICAL_LIST_ORDER
from calico_publish.export import StagedExport
from calico_publish.manifest import compute_revision_fingerprint
from tests.fixtures.publish.fixture_builder import (
    BASELINE_DIR,
    extra_unapproved_column,
    mutated_publication,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _invoke(argv: list[str], **kwargs) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv, **kwargs)
    return code, stdout.getvalue().replace("\r\n", "\n"), stderr.getvalue().replace("\r\n", "\n")


class PublicationCliTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX symlink semantics required")
    def test_manifest_parent_symlink_is_rejected_before_outside_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            outside = root / "outside"
            staging.mkdir()
            outside.mkdir()
            shutil.copy2(BASELINE_DIR / "publication-exports-v1.json", staging)
            (staging / "manifest").symlink_to(outside, target_is_directory=True)

            def runner(**kwargs):
                kwargs["export"](Path("synthetic.duckdb"))
                return BuildOutcome(status="success", category=None, proof=None)

            def exporter(database, allowlist, output_root):
                del database
                exports = Path(output_root) / "exports"
                exports.mkdir()
                staged = []
                for entry in allowlist.exports:
                    payload = (BASELINE_DIR / "exports" / entry.file_name).read_bytes()
                    (exports / entry.file_name).write_bytes(payload)
                    staged.append(
                        StagedExport(
                            entry.export_name,
                            entry.file_name,
                            f"exports/{entry.file_name}",
                            hashlib.sha256(payload).hexdigest(),
                            payload.count(b"\n") - 1,
                        )
                    )
                return tuple(staged)

            code, stdout, stderr = _invoke(
                ["export", "--mode", "fixture", "--staging", str(staging)],
                build_runner=runner,
                exporter=exporter,
            )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout), {"category": "export.invalid_staging"})
        self.assertEqual(stderr, "export.invalid_staging\n")
        self.assertEqual(list(outside.iterdir()), [])

    def test_real_catalog_projects_the_complete_canonical_source_set(self) -> None:
        from calico_publish.cli import _accepted_releases

        catalog = load_input_catalog(REPO_ROOT / "contracts/dbt-input-catalog-v1.json")
        anchor = catalog.releases[0]
        logical_lists = tuple(
            (
                name,
                SimpleNamespace(
                    raw_sha256=str(index) * 64,
                    raw_byte_count=index,
                    parsed_record_count=index,
                ),
            )
            for index, name in enumerate(LOGICAL_LIST_ORDER, start=1)
        )
        verified = SimpleNamespace(
            as_of_date=anchor.as_of_date,
            release_revision=anchor.release_revision,
            revision_fingerprint=compute_revision_fingerprint(
                {name: record.raw_sha256 for name, record in logical_lists}
            ),
            parser_contract_version=1,
            logical_lists=logical_lists,
        )
        single_release_catalog = type(catalog)(
            contract_version=catalog.contract_version,
            releases=(anchor,),
        )

        with patch(
            "calico_publish.cli.load_and_verify_revision_manifest",
            return_value=verified,
        ):
            releases, parser_version = _accepted_releases(
                "real", Path("unused"), lambda: single_release_catalog
            )

        self.assertEqual(parser_version, "registry-csv-contract-v1")
        self.assertEqual(
            {item.source_list for item in releases[0].source_objects},
            set(LOGICAL_LIST_ORDER),
        )

    def test_command_table_contains_complete_publication_surface(self) -> None:
        from calico_publish.cli import _COMMANDS

        self.assertEqual(sorted(_COMMANDS), ["check-inventory", "export", "publish", "verify"])

    def test_fixture_baseline_returns_zero_with_one_json_document(self) -> None:
        code, stdout, stderr = _invoke(
            ["verify", "--mode", "fixture", "--staging", str(BASELINE_DIR)]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), {"category": "gate.verified", "violation_count": 0})
        self.assertEqual(stderr, "")

    def test_real_mode_uses_only_the_committed_authority(self) -> None:
        observed: list[str] = []

        def loader(path: str | Path):
            observed.append(Path(path).name)
            return load_allowlist(path)

        code, _, _ = _invoke(
            ["verify", "--mode", "real", "--staging", str(BASELINE_DIR)],
            allowlist_loader=loader,
        )
        self.assertEqual(code, 1)
        self.assertEqual(observed, ["publication-exports-v1.json"])

    def test_violation_returns_one_and_value_free_rendered_lines(self) -> None:
        with extra_unapproved_column() as publication:
            code, stdout, stderr = _invoke(
                ["verify", "--mode", "fixture", "--staging", str(publication.root)]
            )
        self.assertEqual(code, 1)
        self.assertEqual(
            stdout,
            "fixture_named_history:unapproved_field: gate.column_set_mismatch\n",
        )
        self.assertEqual(stderr, "gate.violations_found\n")

    def test_gate_boundary_error_is_closed_json_and_category(self) -> None:
        with mutated_publication() as publication:
            publication.write_bytes(Path("exports") / "fixture_named_history.csv", b"")
            code, stdout, stderr = _invoke(
                ["verify", "--mode", "fixture", "--staging", str(publication.root)]
            )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout), {"category": "gate.export_empty_file"})
        self.assertEqual(stderr, "gate.export_empty_file\n")

    def test_allowlist_error_is_closed_json_and_category(self) -> None:
        def rejected_loader(path: str | Path):
            del path
            raise AllowlistError("allowlist.invalid_schema")

        code, stdout, stderr = _invoke(
            ["verify", "--mode", "fixture", "--staging", str(BASELINE_DIR)],
            allowlist_loader=rejected_loader,
        )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout), {"category": "allowlist.invalid_schema"})
        self.assertEqual(stderr, "allowlist.invalid_schema\n")

    def test_unexpected_internal_error_is_fixed_and_returns_three(self) -> None:
        def broken_loader(path: str | Path):
            del path
            raise RuntimeError("private internal detail")

        code, stdout, stderr = _invoke(
            ["verify", "--mode", "fixture", "--staging", str(BASELINE_DIR)],
            allowlist_loader=broken_loader,
        )
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(stdout), {"category": "cli.unexpected_error"})
        self.assertEqual(stderr, "cli.unexpected_error\n")

    def test_unknown_mode_exits_two_without_echoing_value_or_reaching_loader(self) -> None:
        reached = False
        supplied = "unknown" + ":/private-value"

        def loader(path: str | Path):
            nonlocal reached
            reached = True
            return load_allowlist(path)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            main(
                ["verify", "--mode", supplied, "--staging", str(BASELINE_DIR)],
                allowlist_loader=loader,
            )
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(reached)
        self.assertNotIn(supplied, stdout.getvalue() + stderr.getvalue())
        self.assertEqual(stderr.getvalue().replace("\r\n", "\n"), "calico_publish: usage error\n")

    def test_module_entry_point_succeeds_and_stdout_is_one_json_document(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "calico_publish",
                "verify",
                "--mode",
                "fixture",
                "--staging",
                "tests/fixtures/publish/valid",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        )
        stdout = completed.stdout.replace(b"\r\n", b"\n")
        stderr = completed.stderr.replace(b"\r\n", b"\n")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(json.loads(stdout.decode("ascii")), {"category": "gate.verified", "violation_count": 0})
        self.assertEqual(stderr, b"")

    def test_publish_gate_failure_never_reaches_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory)
            shutil.copy2(BASELINE_DIR / "publication-exports-v1.json", staging)

            def runner(**kwargs):
                kwargs["export"](Path("synthetic.duckdb"))
                return BuildOutcome(status="success", category=None, proof=None)

            def exporter(database, allowlist, root):
                del database
                destination_root = Path(root) / "exports"
                destination_root.mkdir(parents=True)
                staged = []
                for entry in allowlist.exports:
                    if entry.export_name == "fixture_named_history":
                        payload = b"registration_key,unapproved_field\nCT0000001,synthetic\n"
                    else:
                        payload = (",".join(entry.columns) + "\n").encode("ascii")
                    destination = destination_root / entry.file_name
                    destination.write_bytes(payload)
                    staged.append(StagedExport(
                        export_name=entry.export_name, file_name=entry.file_name,
                        relative_path=f"exports/{entry.file_name}",
                        sha256=hashlib.sha256(payload).hexdigest(),
                        row_count=1 if entry.export_name == "fixture_named_history" else 0,
                    ))
                return tuple(staged)

            calls: list[dict] = []

            def publisher(**kwargs):
                calls.append(kwargs)
                raise AssertionError("must not be reached")

            code, _, stderr = _invoke(
                ["publish", "--mode", "fixture", "--staging", str(staging),
                 "--remote", "origin", "--target-ref", "published-data"],
                build_runner=runner, exporter=exporter, transaction_publisher=publisher,
            )
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])
        self.assertEqual(stderr, "gate.violations_found\n")

    def test_publish_rejects_nonliteral_target_before_build(self) -> None:
        calls: list[str] = []

        def runner(**kwargs):
            calls.append("build")
            return BuildOutcome(status="success", category=None, proof=None)

        code, stdout, stderr = _invoke(
            ["publish", "--mode", "fixture", "--staging", "unused",
             "--remote", "origin", "--target-ref", "other"],
            build_runner=runner,
        )
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])
        self.assertEqual(json.loads(stdout), {"category": "transaction.parent_not_found"})
        self.assertEqual(stderr, "transaction.parent_not_found\n")

    def test_fixture_dry_run_builds_once_and_stops_before_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory)
            shutil.copy2(BASELINE_DIR / "publication-exports-v1.json", staging)
            builds: list[str] = []
            publishes: list[str] = []

            def runner(**kwargs):
                builds.append(kwargs["mode"])
                kwargs["export"](Path("synthetic.duckdb"))
                return BuildOutcome(status="success", category=None, proof=None)

            def exporter(database, allowlist, root):
                del database
                exports = Path(root) / "exports"
                exports.mkdir()
                staged = []
                for entry in allowlist.exports:
                    payload = (BASELINE_DIR / "exports" / entry.file_name).read_bytes()
                    (exports / entry.file_name).write_bytes(payload)
                    staged.append(StagedExport(
                        entry.export_name, entry.file_name, f"exports/{entry.file_name}",
                        hashlib.sha256(payload).hexdigest(), payload.count(b"\n") - 1,
                    ))
                return tuple(staged)

            code, stdout, stderr = _invoke(
                ["publish", "--mode", "fixture", "--staging", str(staging),
                 "--remote", "origin", "--target-ref", "published-data", "--dry-run"],
                build_runner=runner, exporter=exporter,
                transaction_publisher=lambda **kwargs: publishes.append("publish"),
            )
        self.assertEqual(code, 0)
        self.assertEqual(builds, ["fixture"])
        self.assertEqual(publishes, [])
        self.assertEqual(json.loads(stdout), {"category": "publish.dry_run_verified"})
        self.assertEqual(stderr, "")


if __name__ == "__main__":
    unittest.main()
