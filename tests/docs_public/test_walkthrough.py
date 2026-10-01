"""The owner script is timed, evidence-bound and safe to record."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "docs/walkthrough.md"
IDS = ("finding", "sql", "parser", "non-claim")
MEDIA = {".mp4", ".mov", ".webm", ".avi", ".mkv", ".gif", ".png", ".jpg", ".jpeg", ".wav", ".mp3"}
SAFE_SOURCES = {
    "README.md",
    "docs/walkthrough.md",
    "docs/evidence/public-readme-inputs-v1.json",
    "docs/evidence/sql-excerpts-v1.json",
    "docs/evidence/gate-a/correction-index-v1.json",
    "docs/evidence/gate-a/spike-001-successor-v1.json",
    "docs/powerbi-refresh-runbook.md",
    "powerbi/Calico.Report/definition/pages/cohort_persistence/visuals/persistence_support/visual.json",
}


def segments(text):
    """Parse the human script's exact four timed sections without echoing content."""
    blocks = re.findall(r"^## (finding|sql|parser|non-claim) — ([^\n]+) seconds\n(.*?)(?=^## |\Z)",
                        text, re.M | re.S)
    if tuple(block[0] for block in blocks) != IDS:
        raise ValueError("walkthrough.segments")
    result = []
    for identifier, duration, body in blocks:
        if not re.fullmatch(r"[1-9][0-9]*", duration):
            raise ValueError("walkthrough.duration")
        narration = re.search(r"\*\*Narration:\*\*\n\n(.*?)\n\n", body, re.S)
        shot_blocks = re.findall(r"```json\n(.*?)\n```", body, re.S)
        if narration is None or not narration[1].strip() or len(shot_blocks) != 1:
            raise ValueError("walkthrough.content")
        try:
            shots = json.loads(shot_blocks[0])
        except (ValueError, TypeError):
            raise ValueError("walkthrough.shots") from None
        result.append({"id": identifier, "duration_seconds": int(duration),
                       "narration": narration[1], "shots": shots})
    return result


def validate_segments(rows, root=ROOT):
    if tuple(row["id"] for row in rows) != IDS:
        raise ValueError("walkthrough.segments")
    if any(type(row["duration_seconds"]) is not int or row["duration_seconds"] <= 0 for row in rows):
        raise ValueError("walkthrough.duration")
    if sum(row["duration_seconds"] for row in rows) > 180:
        raise ValueError("walkthrough.duration")
    for row in rows:
        if not isinstance(row["shots"], list) or not row["shots"]:
            raise ValueError("walkthrough.shots")
        for shot in row["shots"]:
            if set(shot) != {"application", "page", "source", "evidence", "bounds", "hide"}:
                raise ValueError("walkthrough.shot_fields")
            for field in ("application", "page", "bounds", "hide"):
                if not isinstance(shot[field], str) or not shot[field].strip():
                    raise ValueError("walkthrough.shot_fields")
            if not isinstance(shot["evidence"], list) or not shot["evidence"]:
                raise ValueError("walkthrough.evidence")
            for path in [shot["source"], *shot["evidence"]]:
                if not isinstance(path, str) or path not in SAFE_SOURCES:
                    raise ValueError("walkthrough.unsafe_source")
                relative = PurePosixPath(path)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("walkthrough.unsafe_source")
                destination = root / path
                if destination.is_symlink() or not destination.is_file() or not destination.resolve().is_relative_to(root.resolve()):
                    raise ValueError("walkthrough.evidence")
            # Positive recording instructions may not ask for private/raw or dossier views.
            shown = " ".join(shot[field] for field in ("application", "page", "bounds")).lower()
            forbidden = ("raw row", "raw registry", "private", "credential", "terminal", "dossier",
                         "organization_lookup", "personal", "source pdf", "local path")
            if any(token in shown for token in forbidden):
                raise ValueError("walkthrough.unsafe_shot")


class WalkthroughContracts(unittest.TestCase):
    def script(self):
        self.assertTrue(SCRIPT.is_file(), "A committed timed owner script must exist")
        return SCRIPT.read_text(encoding="utf-8")

    def test_required_script_exists_with_four_evidence_bound_segments(self):
        rows = segments(self.script())
        validate_segments(rows)
        tracked = set(subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines())
        # The newly authored script is admitted by the scoped candidate/HEAD proof.
        for row in rows:
            for shot in row["shots"]:
                for path in [shot["source"], *shot["evidence"]]:
                    self.assertTrue(path == "docs/walkthrough.md" or path in tracked,
                                    "Every evidence source must be committed")

    def test_exactly_180_passes_and_181_fails(self):
        rows = segments(self.script())
        rows[0]["duration_seconds"] += 180 - sum(r["duration_seconds"] for r in rows)
        validate_segments(rows)
        rows[0]["duration_seconds"] += 1
        with self.assertRaisesRegex(ValueError, "walkthrough.duration"):
            validate_segments(rows)

    def test_zero_negative_boolean_and_fractional_durations_fail(self):
        rows = segments(self.script())
        for invalid in (0, -1, True, 1.5, "45"):
            changed = copy.deepcopy(rows)
            changed[0]["duration_seconds"] = invalid
            with self.assertRaisesRegex(ValueError, "walkthrough.duration"):
                validate_segments(changed)

    def test_duplicate_missing_segment_or_malformed_heading_fails(self):
        text = self.script()
        for changed in (text.replace("## sql —", "## finding —"),
                        text.replace("## sql —", "## missing —"),
                        text.replace("## sql — 45 seconds", "## sql — 0 seconds")):
            with self.assertRaises(ValueError):
                segments(changed)

    def test_missing_narration_shot_bounds_or_evidence_fails(self):
        rows = segments(self.script())
        for field in ("application", "page", "source", "evidence", "bounds", "hide"):
            changed = copy.deepcopy(rows)
            del changed[0]["shots"][0][field]
            with self.assertRaises(ValueError):
                validate_segments(changed)
        with self.assertRaisesRegex(ValueError, "walkthrough.content"):
            segments(self.script().replace("**Narration:**", "**Notes:**", 1))

    def test_missing_traversal_private_media_and_symlink_sources_fail(self):
        rows = segments(self.script())
        for bad in ("docs/missing.md", "../outside.md", ".planning/STATE.md", "recording.mp4"):
            changed = copy.deepcopy(rows)
            changed[0]["shots"][0]["source"] = bad
            with self.assertRaisesRegex(ValueError, "walkthrough.unsafe_source"):
                validate_segments(changed)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "README.md").symlink_to(ROOT / "README.md")
            with self.assertRaisesRegex(ValueError, "walkthrough.evidence"):
                validate_segments(rows, root)

    def test_unsafe_positive_shot_instructions_fail(self):
        rows = segments(self.script())
        for bad in ("Show raw registry rows", "Open private planning", "Show terminal",
                    "Display an organization dossier", "Personal windows", "Credentials"):
            changed = copy.deepcopy(rows)
            changed[0]["shots"][0]["bounds"] = bad
            with self.assertRaisesRegex(ValueError, "walkthrough.unsafe_shot"):
                validate_segments(changed)

    def test_finding_is_bound_to_corrected_claim_and_observed_persistence(self):
        rows = segments(self.script())
        text = rows[0]["narration"]
        inputs = json.loads((ROOT / "docs/evidence/public-readme-inputs-v1.json").read_text())
        self.assertEqual(inputs["claim"]["sql_values"]["support_count"], "7737")
        for required in ("7,737", "7,733", "published delinquent population", "three accepted releases",
                         "not observed", "SQL"):
            self.assertIn(required, text)
        sources = [shot["source"] for shot in rows[0]["shots"]]
        self.assertIn("README.md", sources)
        self.assertIn("powerbi/Calico.Report/definition/pages/cohort_persistence/visuals/persistence_support/visual.json", sources)

    def test_sql_shot_names_exact_committed_transition_excerpt(self):
        row = segments(self.script())[1]
        evidence = json.loads((ROOT / "docs/evidence/sql-excerpts-v1.json").read_text())
        excerpt = next(e for e in evidence["excerpts"] if e["source_path"].endswith("int_entity_transitions.sql"))
        source = (ROOT / excerpt["source_path"]).read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(), excerpt["source_sha256"])
        for required in ("exact-key", "anti-join", "release", "absence"):
            self.assertIn(required, row["narration"])
        self.assertIn("int_entity_transitions", row["shots"][0]["page"])
        self.assertIn("docs/evidence/sql-excerpts-v1.json", row["shots"][0]["evidence"])

    def test_parser_retains_supersedes_pair_and_inverted_guidance(self):
        row = segments(self.script())[2]
        for required in ("557,065", "557,067", "two records", "CP1252", "QUOTE_NONE",
                         "no embedded record newlines", "newline-aware", "inverted", "default quote handling"):
            self.assertIn(required, row["narration"])
        successor = json.loads((ROOT / "docs/evidence/gate-a/spike-001-successor-v1.json").read_text())
        self.assertEqual(successor["release_total"], 557067)
        self.assertIs(successor["embedded_newline_explanation_retracted"], True)
        paths = {p for shot in row["shots"] for p in shot["evidence"]}
        self.assertIn("docs/evidence/gate-a/correction-index-v1.json", paths)

    def test_non_claim_and_manual_refresh_are_consistent(self):
        from tools.docs_public.readme import MANUAL_DISCLOSURE
        row = segments(self.script())[3]
        for required in ("disappearance is not cure", "outside-in", "workload", "enforcement", "cause",
                         "no organization score, ranking or partner recommendation",
                         "no organization was interviewed about willingness to pay", "Phase 10"):
            self.assertIn(required, row["narration"])
        self.assertIn(MANUAL_DISCLOSURE, row["narration"])
        self.assertIn(MANUAL_DISCLOSURE, (ROOT / "README.md").read_text())
        self.assertIn(MANUAL_DISCLOSURE, (ROOT / "docs/powerbi-refresh-runbook.md").read_text())

    def test_owner_privacy_frame_review_and_anonymous_host_checklist_exist(self):
        text = self.script()
        for required in ("## Before recording", "## After recording", "## Host and verify anonymously",
                         "close private planning", "local paths", "secrets", "personal windows",
                         "organization dossier", "every frame", "logged-out", "incognito", "without credentials",
                         "outside Git", "unlisted YouTube", "GitHub Release asset", "180 seconds",
                         "duration", "privacy review", "anonymous viewing", "external URL"):
            self.assertIn(required, text)

    def test_script_links_resolve_and_no_media_path_is_in_main_tree(self):
        text = self.script()
        for target in re.findall(r"\]\(([^)]+)\)", text):
            self.assertFalse("://" in target, "The script links committed evidence only")
            path = (SCRIPT.parent / target.split("#", 1)[0]).resolve()
            self.assertTrue(path.is_relative_to(ROOT) and path.is_file(), "Evidence links must resolve")
        tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
        self.assertFalse(any(PurePosixPath(p).suffix.lower() in MEDIA for p in tracked),
                         "No recording, screenshot or media binary belongs in the product tree")


if __name__ == "__main__":
    unittest.main()
