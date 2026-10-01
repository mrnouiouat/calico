"""Written walkthrough claims and excerpts stay bound to committed evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
DOCUMENT = ROOT / "docs/walkthrough.md"


class WrittenWalkthroughContracts(unittest.TestCase):
    def document(self):
        return DOCUMENT.read_text(encoding="utf-8")

    def test_four_topics_exist_without_recording_or_hosting_prerequisites(self):
        text = self.document()
        headings = re.findall(r"^## (.+)$", text, re.M)
        self.assertEqual(len(headings), 4)
        for heading, topic in zip(headings, ("One finding:", "One SQL transformation:",
                                            "One source defect:", "One deliberate non-claim:")):
            self.assertTrue(heading.startswith(topic), "All four substantive topics must exist")
        for stale in ("Narration:", "Shots, in order", "Before recording", "After recording",
                      "owner recording pending", "unlisted YouTube", "180 seconds"):
            self.assertNotIn(stale, text)

    def test_finding_and_panel_match_governed_evidence(self):
        text = self.document()
        inputs = json.loads((ROOT / "docs/evidence/public-readme-inputs-v1.json").read_text())
        self.assertEqual(inputs["claim"]["sql_values"]["support_count"], "7737")
        self.assertIn("7,737", text)
        self.assertIn("7,733", text)
        self.assertIn("superseded", text)
        for release in inputs["accepted_releases"]:
            self.assertIn(release["as_of_date"], text)
        for required in ("published delinquent population", "not observed", "cohort denominator"):
            self.assertIn(required, text)

    def test_sql_excerpt_is_exact_and_bound_to_source_hashes(self):
        text = self.document()
        evidence = json.loads((ROOT / "docs/evidence/sql-excerpts-v1.json").read_text())
        excerpt = next(e for e in evidence["excerpts"] if e["source_path"].endswith("int_entity_transitions.sql"))
        blocks = re.findall(r"```sql\n(.*?)```", text, re.S)
        self.assertEqual(blocks, [excerpt["selected_text"]], "Walkthrough must use the source-exact selection")
        raw = (ROOT / excerpt["source_path"]).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), excerpt["source_sha256"])
        selected = b"".join(b"".join(raw.splitlines(keepends=True)[first - 1:last])
                            for first, last in excerpt["ranges"])
        self.assertEqual(hashlib.sha256(selected).hexdigest(), excerpt["selected_sha256"])
        self.assertIn(excerpt["source_sha256"], text)
        self.assertIn(excerpt["selected_sha256"], text)
        for required in ("anti-join", "complete State Charity registration key", "invented status",
                         "excerpt rather than a runnable standalone query"):
            self.assertIn(required, text)

    def test_parser_correction_preserves_superseded_figures_and_current_contract(self):
        text = self.document()
        successor = json.loads((ROOT / "docs/evidence/gate-a/spike-001-successor-v1.json").read_text())
        self.assertEqual(successor["release_total"], 557067)
        self.assertIs(successor["embedded_newline_explanation_retracted"], True)
        for required in ("557,065", "557,067", "two records", "default quote handling", "CP1252",
                         "QUOTE_NONE", "no embedded record newlines", "newline-aware", "inverted"):
            self.assertIn(required, text)

    def test_non_claims_manual_refresh_and_phase_ten_boundary_remain(self):
        from tools.docs_public.readme import MANUAL_DISCLOSURE
        text = self.document()
        for required in ("outside-in", "disappearance is not", "cure", "Attorney General workload",
                         "enforcement", "intent or cause", "no organization score, ranking or",
                         "partner recommendation", "no organization was interviewed", "Phase 10",
                         "A failed capture does not erase accepted release identity"):
            self.assertIn(required, text)
        self.assertIn(MANUAL_DISCLOSURE, text)
        self.assertIn(MANUAL_DISCLOSURE, (ROOT / "README.md").read_text())
        self.assertIn("[Phase 10 report URL slot]", (ROOT / "README.md").read_text())

    def test_document_links_resolve_to_public_committed_evidence(self):
        tracked = set(subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines())
        for target in re.findall(r"\]\(([^)]+)\)", self.document()):
            self.assertNotIn("://", target, "Walkthrough evidence is repository-local")
            path = (DOCUMENT.parent / target.split("#", 1)[0]).resolve()
            self.assertTrue(path.is_relative_to(ROOT) and path.is_file(), "Evidence target must resolve")
            self.assertIn(path.relative_to(ROOT).as_posix(), tracked)

    def test_readme_links_document_and_no_video_is_tracked(self):
        from tools.docs_public.readme import WALKTHROUGH_LINK
        self.assertIn(WALKTHROUGH_LINK, (ROOT / "README.md").read_text())
        paths = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
        media = {".mp4", ".mov", ".webm", ".avi", ".mkv", ".wav", ".mp3"}
        self.assertFalse(any(PurePosixPath(path).suffix.lower() in media for path in paths))

    def test_document_passes_excluded_content_scan(self):
        from tools.privacy_scan.scanner import scan_text
        self.assertFalse(scan_text("docs/walkthrough.md", self.document()),
                         "Document must pass the existing excluded-content policy")


if __name__ == "__main__":
    unittest.main()
