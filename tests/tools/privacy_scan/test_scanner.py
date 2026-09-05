"""Tests for tools.privacy_scan.scanner.

D-007 scope (corrected 2026-08-24): organization names and `State Charity
Reg#` values (all three classified families) are ALLOWED published fields
and MUST produce zero findings -- these negative cases are mandatory because
Phase 8's REQ-org-lookup publishes them. Only synthetic/reserved values are
used; nothing here is copied from Calico-build's private source.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.privacy_scan.git_objects import GitObjectError
from tools.privacy_scan.policy import Policy, PathRule, PolicyError
from tools.privacy_scan.scanner import Finding, ScanPathError, scan, scan_paths


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )


class TempGitRepo:
    def __init__(self) -> None:
        self._dir: tempfile.TemporaryDirectory[str] | None = None
        self.path: Path | None = None

    def __enter__(self) -> "TempGitRepo":
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name)
        _run(["init", "--initial-branch=main"], self.path)
        _run(["config", "user.email", "synthetic" + "@example.invalid"], self.path)
        _run(["config", "user.name", "Synthetic Test User"], self.path)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._dir is not None:
            self._dir.cleanup()

    def write_file(self, relative_path: str, content: bytes) -> None:
        assert self.path is not None
        target = self.path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def add(self, relative_path: str) -> None:
        _run(["add", "--", relative_path], self.path)

    def commit(self, message: str) -> str:
        _run(["commit", "-m", message, "--allow-empty"], self.path)
        result = _run(["rev-parse", "HEAD"], self.path)
        return result.stdout.decode("ascii").strip()


def _default_policy() -> Policy:
    return Policy(
        policy_version=1,
        max_blob_bytes=1048576,
        forbidden_paths=(
            PathRule(kind="prefix", value="data/raw/", category="raw_source_data"),
            PathRule(kind="suffix", value=".duckdb", category="database_file"),
            PathRule(kind="exact", value="data/mitos.db", category="private_database"),
        ),
    )


class TestScannerPositiveDetection(unittest.TestCase):
    def test_canonical_fein_is_detected(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("notes.txt", b"FEIN on file: 94-" + b"1234567\n")
            repo.add("notes.txt")
            commit = repo.commit("add notes")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())

        categories = {f.category for f in findings}
        self.assertIn("fein", categories)

    def test_separated_fein_with_label_is_detected(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("notes.txt", b"Federal Employer ID Number: 941" + b"234567\n")
            repo.add("notes.txt")
            commit = repo.commit("add notes")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())

        categories = {f.category for f in findings}
        self.assertIn("fein", categories)

    def test_street_address_is_detected(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("notes.txt", b"Mailing address: 4210" + b" Placeholder Ave\n")
            repo.add("notes.txt")
            commit = repo.commit("add notes")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())

        categories = {f.category for f in findings}
        self.assertIn("street_address", categories)

    def test_phone_contact_form_is_detected(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("notes.txt", b"Officer phone: 555" + b"-010-2000\n")
            repo.add("notes.txt")
            commit = repo.commit("add notes")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())

        categories = {f.category for f in findings}
        self.assertIn("contact_info", categories)

    def test_email_contact_form_is_detected(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("notes.txt", b"Officer email: synthetic.officer" + b"@example.invalid\n")
            repo.add("notes.txt")
            commit = repo.commit("add notes")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())

        categories = {f.category for f in findings}
        self.assertIn("contact_info", categories)

    def test_unapproved_join_query_url_is_detected(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file(
                "notes.txt",
                b"See https://example-lookup.invalid/org?fe" + b"in=941234567\n",
            )
            repo.add("notes.txt")
            commit = repo.commit("add notes")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())

        categories = {f.category for f in findings}
        self.assertIn("unapproved_join_field", categories)

    def test_windows_absolute_path_is_detected(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("notes.txt", b"Local file: C:" + b"\\Users\\synthetic\\evidence.csv\n")
            repo.add("notes.txt")
            commit = repo.commit("add notes")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())

        categories = {f.category for f in findings}
        self.assertIn("absolute_local_path", categories)

    def test_posix_absolute_path_is_detected(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("notes.txt", b"Local file: /User" + b"s/synthetic/evidence.csv\n")
            repo.add("notes.txt")
            commit = repo.commit("add notes")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())

        categories = {f.category for f in findings}
        self.assertIn("absolute_local_path", categories)

    def test_forbidden_path_rule_is_detected(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("data/raw/rows.csv", b"harmless content\n")
            repo.add("data/raw/rows.csv")
            commit = repo.commit("add raw rows")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())

        categories = {f.category for f in findings}
        self.assertIn("raw_source_data", categories)


class TestScannerNegativeD007AllowedFields(unittest.TestCase):
    """D-007 allowed published fields must never be flagged (scope corrected 2026-08-24)."""

    def test_organization_name_is_not_detected(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("notes.txt", b"Organization: Synthetic Placeholder Charity Fund\n")
            repo.add("notes.txt")
            commit = repo.commit("add notes")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())

        self.assertEqual(findings, [])

    def test_unicode_case_punctuation_organization_variant_is_not_detected(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file(
                "notes.txt",
                "Organization: SYNTHETIC   Plácëhōldér, Charity–Fund!!\n".encode("utf-8"),
            )
            repo.add("notes.txt")
            commit = repo.commit("add notes")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())

        self.assertEqual(findings, [])

    def test_bare_digit_registration_number_is_not_detected(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("notes.txt", b"State Charity Reg#: 0123456\n")
            repo.add("notes.txt")
            commit = repo.commit("add notes")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())

        self.assertEqual(findings, [])

    def test_ct_prefixed_registration_number_is_not_detected(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("notes.txt", b"State Charity Reg#: CT0123456\n")
            repo.add("notes.txt")
            commit = repo.commit("add notes")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())

        self.assertEqual(findings, [])

    def test_ex_prefixed_registration_number_is_not_detected(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("notes.txt", b"State Charity Reg#: EX0123456\n")
            repo.add("notes.txt")
            commit = repo.commit("add notes")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())

        self.assertEqual(findings, [])

    def test_official_verification_url_is_not_detected(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file(
                "notes.txt",
                b"Verify at https://rct.doj.ca.gov/Verification/Web.aspx?id=0123456\n",
            )
            repo.add("notes.txt")
            commit = repo.commit("add notes")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())

        self.assertEqual(findings, [])


class TestScannerSafeLookalikes(unittest.TestCase):
    def test_bare_nine_digit_number_without_label_is_not_detected(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("notes.txt", b"Reference count: 123456789 total rows\n")
            repo.add("notes.txt")
            commit = repo.commit("add notes")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())

        self.assertEqual(findings, [])

    def test_safe_url_without_join_query_is_not_detected(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file(
                "notes.txt",
                b"See https://example.invalid/docs/overview?page=2\n",
            )
            repo.add("notes.txt")
            commit = repo.commit("add notes")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())

        self.assertEqual(findings, [])


class TestScannerHistoryDeduplication(unittest.TestCase):
    def test_duplicate_blob_across_commits_does_not_duplicate_findings(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("notes.txt", b"FEIN on file: 94-" + b"1234567\n")
            repo.add("notes.txt")
            repo.commit("first commit")

            repo.write_file("other.txt", b"unrelated\n")
            repo.add("other.txt")
            repo.commit("second commit, unchanged notes.txt")

            findings = scan(treeish=None, history_all=True, repo_dir=repo.path, policy=_default_policy())

        fein_findings = [f for f in findings if f.category == "fein"]
        self.assertEqual(len(fein_findings), 1)


class TestStreamingScanner(unittest.TestCase):
    def test_clean_named_history_larger_than_one_mebibyte_passes(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("large.csv", b"organization_name,city,state\n" + (b"Synthetic Org,Sample,CA\n" * 50000))
            repo.add("large.csv")
            commit = repo.commit("add large clean blob")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())
        self.assertEqual(findings, [])

    def test_large_git_blob_is_streamed_and_violation_after_one_mebibyte_is_found(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("large.txt", (b"safe line\n" * 110000) + b"FEIN on file: 94-" + b"1234567\n")
            repo.add("large.txt")
            commit = repo.commit("add large blob")
            findings = scan(treeish=commit, history_all=False, repo_dir=repo.path, policy=_default_policy())
        self.assertIn("fein", {finding.category for finding in findings})
        self.assertNotIn("oversize_blob", {finding.category for finding in findings})
        rendered = "\n".join(finding.render() for finding in findings)
        self.assertNotIn("94-" + "1234567", rendered)

    def test_detector_survives_tiny_arbitrary_chunk_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.txt").write_bytes(b"Federal Employer ID Number: 941" + b"234567\n")
            with unittest.mock.patch("tools.privacy_scan.scanner._STREAM_CHUNK_BYTES", 3):
                findings = scan_paths(root, ("notes.txt",), _default_policy())
        self.assertIn("fein", {finding.category for finding in findings})

    def test_long_record_is_scanned_whole_and_overlong_record_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "long.txt").write_bytes(
                b"x" * 70000 + b" https://example.invalid/lookup?fe" + b"in=941" + b"234567\n"
            )
            findings = scan_paths(root, ("long.txt",), _default_policy())
            self.assertIn("unapproved_join_field", {finding.category for finding in findings})
            (root / "long.txt").write_bytes(b"x" * 1048577 + b"\n")
            findings = scan_paths(root, ("long.txt",), _default_policy())
        self.assertEqual(findings, [Finding("oversize_record", "long.txt", "line 1")])

    def test_explicit_sorted_path_list_is_required_and_unlisted_files_are_not_walked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("safe\n", encoding="utf-8")
            (root / "unlisted.txt").write_bytes(b"FEIN: 94-" + b"1234567\n")
            self.assertEqual(scan_paths(root, ("a.txt",), _default_policy()), [])
            for paths in (("b.txt", "a.txt"), ("a.txt", "a.txt"), ("../a.txt",), ("C:" + "/a.txt",)):
                with self.subTest(paths=paths), self.assertRaises(ScanPathError):
                    scan_paths(root, paths, _default_policy())


class TestFindingShape(unittest.TestCase):
    def test_finding_has_no_matched_value_field(self) -> None:
        field_names = set(Finding.__dataclass_fields__.keys())
        self.assertEqual(field_names, {"category", "path", "locator"})

    def test_finding_render_contains_no_extra_data(self) -> None:
        finding = Finding(category="fein", path="notes.txt", locator="line 1")
        rendered = finding.render()
        self.assertEqual(rendered, "notes.txt:line 1: fein")


if __name__ == "__main__":
    unittest.main()
