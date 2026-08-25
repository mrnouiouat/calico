"""Contract tests for the redacted successor document and its supersession record.

Verifies structural properties only -- headings, banner placement/text, typed
placeholder tokens, record schema/categories, and hashes. Never quotes or
reproduces literal identity content (organization FEIN/EIN values, addresses,
contact details) from the private original; org names and registration
numbers that legitimately survive verbatim under D-007 are likewise never
hardcoded here, only proven indirectly via hash/scanner/absence checks
(D-10, per-task instruction to use structure/headings/placeholders/hashes/
reserved synthetic values only).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SUCCESSOR_PATH = REPO_ROOT / "docs" / "ag-registry-migration-2026-08.md"
RECORD_PATH = REPO_ROOT / "docs" / "redactions" / "ag-registry-migration-2026-08.json"
POLICY_PATH = REPO_ROOT / "policies" / "publishable-tree.json"

BANNERED_SECTIONS = ["1", "2", "3", "4", "5", "6", "7", "9"]

SECTION_HEADINGS = {
    "1": "## 1. The finding in one line",
    "2": "## 2. How it surfaced",
    "3": "## 3. What was ruled out",
    "4": "## 4. What is actually there",
    "5": "## 5. Why one query returned a wrong organization",
    "6": "## 6. The replacement portal's API",
    "7": "## 7. Consequences",
    "8": "## 8. What to re-check after the cutover",
    "8a": "## 8a. CONTRADICTION",
    "9": "## 9. Confidence and limits",
    "8b": "## 8b. CORRECTION",
}

#: Locked banner text -- identical under every bannered section (D-06).
BANNER_TEXT = "Correction — see §8a and §8b."

#: Locked placeholder vocabulary. Organization-name/registration-number/URL
#: placeholders are retired by the D-007 scope correction (2026-08-24) and
#: must never appear.
RETIRED_PLACEHOLDER_TOKENS = [
    "[ORGANIZATION_NAME_REDACTED]",
    "[REGISTRATION_NUMBER_REDACTED]",
    "[ORGANIZATION_URL_REDACTED]",
]

ALLOWED_PLACEHOLDER_TOKENS = [
    "[FEIN_REDACTED]",
    "[ADDRESS_REDACTED]",
    "[CONTACT_REDACTED]",
    "[LOCAL_PATH_REDACTED]",
]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _cli_env() -> dict[str, str]:
    env = {"PYTHONPATH": str(REPO_ROOT)}
    for key in ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP"):
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    return env


class RedactedDocumentTests(unittest.TestCase):
    """Task 1: successor document structural contract."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = _read_text(SUCCESSOR_PATH)

    def test_successor_document_exists(self) -> None:
        self.assertTrue(SUCCESSOR_PATH.is_file())

    def test_top_level_correction_notice_present_before_section_1(self) -> None:
        self.assertIn("CORRECTION NOTICE", self.text)
        notice_index = self.text.index("CORRECTION NOTICE")
        first_section_index = self.text.index(SECTION_HEADINGS["1"])
        self.assertLess(notice_index, first_section_index)

    def test_top_notice_links_redaction_record(self) -> None:
        self.assertIn("docs/redactions/ag-registry-migration-2026-08.json", self.text)

    def test_top_notice_references_both_governing_sections(self) -> None:
        notice_index = self.text.index("CORRECTION NOTICE")
        first_section_index = self.text.index(SECTION_HEADINGS["1"])
        notice_block = self.text[notice_index:first_section_index]
        self.assertIn("§8a", notice_block)
        self.assertIn("§8b", notice_block)

    def test_every_required_heading_present_in_source_order(self) -> None:
        positions = []
        for key in ["1", "2", "3", "4", "5", "6", "7", "8", "8a", "9", "8b"]:
            heading = SECTION_HEADINGS[key]
            self.assertIn(heading, self.text, f"missing heading for section {key}")
            positions.append(self.text.index(heading))
        self.assertEqual(positions, sorted(positions), "section headings out of source order")

    def test_banner_immediately_follows_each_required_heading(self) -> None:
        for key in BANNERED_SECTIONS:
            heading = SECTION_HEADINGS[key]
            idx = self.text.index(heading)
            window = self.text[idx : idx + len(heading) + 400]
            self.assertIn(BANNER_TEXT, window, f"banner missing immediately under section {key}")

    def test_banner_references_both_governing_sections(self) -> None:
        for key in BANNERED_SECTIONS:
            heading = SECTION_HEADINGS[key]
            idx = self.text.index(heading)
            window = self.text[idx : idx + len(heading) + 400]
            self.assertIn("§8a", window)
            self.assertIn("§8b", window)

    def test_section_8_carries_no_banner(self) -> None:
        # Section 8 is the pivot directly into 8a/8b and is deliberately
        # excluded from the banner set (D-06 covers only sections 1-7, 9).
        heading = SECTION_HEADINGS["8"]
        idx = self.text.index(heading)
        window = self.text[idx : idx + len(heading) + 200]
        self.assertNotIn(BANNER_TEXT, window)

    def test_visible_correction_surface_count_is_nine(self) -> None:
        # One top-level notice + one banner per bannered section (8) = 9.
        self.assertEqual(self.text.count(BANNER_TEXT), len(BANNERED_SECTIONS))
        self.assertEqual(self.text.count("CORRECTION NOTICE"), 1)

    def test_fein_placeholder_occurrence_count_is_locked(self) -> None:
        # 5 real FEIN replacements (4 in the section-2 table, 1 in the
        # section-8 positive-control example) + 1 explanatory mention in the
        # top-level correction notice.
        self.assertEqual(self.text.count("[FEIN_REDACTED]"), 6)

    def test_retired_placeholder_tokens_never_appear(self) -> None:
        for token in RETIRED_PLACEHOLDER_TOKENS:
            self.assertNotIn(token, self.text)

    def test_only_locked_placeholder_tokens_appear(self) -> None:
        bracket_tokens = set(re.findall(r"\[[A-Z_]+_REDACTED\]", self.text))
        self.assertTrue(bracket_tokens.issubset(set(ALLOWED_PLACEHOLDER_TOKENS)))

    def test_no_canonical_fein_literal_survives(self) -> None:
        self.assertEqual(re.findall(r"\b\d{2}-\d{7}\b", self.text), [])

    def test_no_bare_nine_digit_literal_survives(self) -> None:
        self.assertEqual(re.findall(r"\b\d{9}\b", self.text), [])

    def test_document_ends_with_single_terminal_newline(self) -> None:
        raw = SUCCESSOR_PATH.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertFalse(raw.endswith(b"\n\n"))


class RedactionRecordTests(unittest.TestCase):
    """Task 2: named-owner supersession record contract."""

    REQUIRED_KEYS = {
        "schema_version",
        "artifact",
        "owner",
        "executed_at",
        "disposition",
        "replacement_categories",
        "private_source_provenance",
        "supersedes",
        "successor_sha256",
    }

    REQUIRED_CATEGORIES = {
        "fein",
        "street_address",
        "officer_detail",
        "contact_detail",
        "local_path",
    }

    @classmethod
    def setUpClass(cls) -> None:
        cls.record = json.loads(_read_text(RECORD_PATH))

    def test_record_is_a_json_object(self) -> None:
        self.assertIsInstance(self.record, dict)

    def test_record_key_set_is_exact_and_closed(self) -> None:
        self.assertEqual(set(self.record.keys()), self.REQUIRED_KEYS)

    def test_schema_version_is_one(self) -> None:
        self.assertEqual(self.record["schema_version"], 1)

    def test_artifact_path_matches_successor(self) -> None:
        self.assertEqual(self.record["artifact"], "docs/ag-registry-migration-2026-08.md")

    def test_owner_is_named(self) -> None:
        self.assertEqual(self.record["owner"], "mrnouiouat")

    def test_disposition_is_exact(self) -> None:
        self.assertEqual(self.record["disposition"], "redacted_successor_published_private")

    def test_executed_at_is_utc_iso8601(self) -> None:
        self.assertRegex(self.record["executed_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_replacement_categories_match_closed_schema(self) -> None:
        self.assertEqual(set(self.record["replacement_categories"]), self.REQUIRED_CATEGORIES)
        self.assertEqual(len(self.record["replacement_categories"]), 5)

    def test_private_source_provenance_is_exact(self) -> None:
        self.assertEqual(
            self.record["private_source_provenance"],
            "owner-controlled private Calico-build workspace",
        )

    def test_supersedes_is_present_and_carries_no_literal_identifier(self) -> None:
        supersedes = self.record["supersedes"]
        self.assertIsInstance(supersedes, str)
        self.assertGreater(len(supersedes), 0)
        self.assertEqual(re.findall(r"\b\d{2}-\d{7}\b", supersedes), [])
        self.assertEqual(re.findall(r"\b\d{9}\b", supersedes), [])

    def test_successor_sha256_is_lowercase_64_hex(self) -> None:
        self.assertRegex(self.record["successor_sha256"], r"^[0-9a-f]{64}$")

    def test_successor_sha256_matches_exact_committed_successor_bytes(self) -> None:
        digest = hashlib.sha256(SUCCESSOR_PATH.read_bytes()).hexdigest()
        self.assertEqual(self.record["successor_sha256"], digest)


class ScannerCleanTreeTests(unittest.TestCase):
    """Both artifacts scan clean via a temporary candidate Git tree.

    The target repository has no commit yet (Plan 04 owns the first commit),
    so this proves publishability without staging/committing anything here
    (D-08/D-11); the temporary tree is fully disposed of at the end of the
    test.
    """

    def _run_git(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )

    def test_candidate_tree_with_successor_and_record_has_zero_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._run_git(["init", "--initial-branch=main"], tmp_path)
            self._run_git(["config", "user.email", "synthetic" + "@example.invalid"], tmp_path)
            self._run_git(["config", "user.name", "Synthetic Test User"], tmp_path)

            doc_target = tmp_path / "docs" / "ag-registry-migration-2026-08.md"
            doc_target.parent.mkdir(parents=True, exist_ok=True)
            doc_target.write_bytes(SUCCESSOR_PATH.read_bytes())

            record_target = tmp_path / "docs" / "redactions" / "ag-registry-migration-2026-08.json"
            record_target.parent.mkdir(parents=True, exist_ok=True)
            record_target.write_bytes(RECORD_PATH.read_bytes())

            self._run_git(["add", "--", "docs"], tmp_path)
            tree_result = self._run_git(["write-tree"], tmp_path)
            treeish = tree_result.stdout.decode("ascii").strip()

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.privacy_scan",
                    "--tree",
                    treeish,
                    "--policy",
                    str(POLICY_PATH),
                ],
                cwd=tmp_path,
                env=_cli_env(),
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(completed.stdout, b"")
        self.assertEqual(completed.stderr, b"")


if __name__ == "__main__":
    unittest.main()
