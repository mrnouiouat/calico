"""Exit-code and non-echo tests for the publication CLI (07-04)."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from calico_publish.allowlist import AllowlistError, load_allowlist
from calico_publish.cli import main
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


if __name__ == "__main__":
    unittest.main()
