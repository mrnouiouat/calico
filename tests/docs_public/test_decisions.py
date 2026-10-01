"""Exact cited authority, generation stability and public boundary tests."""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
import unittest

from tests.docs_public.test_citations import Candidate, PRODUCT, scanner


class DecisionTests(unittest.TestCase):
    def test_write_then_check_is_exact_and_byte_stable(self):
        s = scanner()
        self.assertTrue(callable(getattr(s, "write_repository", None)),
                        "Citation write/check must generate an exact candidate contract")
        with Candidate() as c:
            c.put("README.md", "D-007 `private.md`\n")
            c.put(s.BOUNDARY_PATH, "# Planning boundary\n")
            s.write_repository(c.root)
            paths = (s.INVENTORY_PATH, s.TRANSITIONS_PATH, s.REGISTER_PATH)
            before = {p: (c.root / p).read_bytes() for p in paths}
            result = s.check_repository(c.root)
            self.assertEqual(result["decisions"], 1)
            self.assertEqual(result["unresolved"], 1)
            s.write_repository(c.root)
            self.assertEqual(before, {p: (c.root / p).read_bytes() for p in paths})

    def test_missing_extra_duplicate_and_self_referencing_ids_fail(self):
        with Candidate() as c:
            s = scanner()
            c.put("README.md", "D-007\n")
            c.put(s.BOUNDARY_PATH, "# Boundary\n")
            s.write_repository(c.root)
            valid = (c.root / s.REGISTER_PATH).read_text()
            for bad in (valid.replace("## D-007", "## D-008"), valid + "\n## D-008\n\nExtra authority.\n",
                        valid + "\n## D-007\n\nDuplicate authority.\n",
                        valid.replace("V1 intentionally", "D-008 V1 intentionally")):
                with self.assertRaises(s.CitationError):
                    s.check_decision_register(c.root, bad)

    def test_historical_unpadded_tokens_are_distinct(self):
        with Candidate() as c:
            c.put("README.md", "D2 D5 D-02 D-05 D-002 D-005 I-016 C-020 W-002\n")
            self.assertEqual(scanner().scan_decision_ids(c.root), ["C-020", "D-002", "D-005", "I-016", "W-002"])

    def test_register_itself_does_not_expand_required_ids(self):
        with Candidate() as c:
            s = scanner()
            c.put("README.md", "D-007\n")
            c.put(s.REGISTER_PATH, "## D-999\n\nUncited authority.\n")
            self.assertEqual(s.scan_decision_ids(c.root), ["D-007"])

    def test_unknown_cited_id_fails_without_fabricating_summary(self):
        with Candidate() as c:
            s = scanner()
            c.put("README.md", "D-999\n")
            with self.assertRaises(s.CitationError):
                s.write_repository(c.root)
            self.assertFalse((c.root / s.REGISTER_PATH).exists())

    def test_removing_citation_changes_register_once(self):
        with Candidate() as c:
            s = scanner()
            c.put("README.md", "D-003 D-007\n")
            c.put(s.BOUNDARY_PATH, "# Boundary\n")
            s.write_repository(c.root)
            c.put("README.md", "D-007\n")
            s.write_repository(c.root)
            text = (c.root / s.REGISTER_PATH).read_text()
            self.assertNotIn("D-003", text)
            self.assertEqual(s.check_repository(c.root)["decisions"], 1)

    def test_private_summary_and_unknown_summary_fields_fail(self):
        with Candidate() as c:
            s = scanner()
            c.put("README.md", "D-007\n")
            for summaries in ({"D-007": "/" + "Users/synthetic/private-authority"},
                              {"D-007": "D-999 invented authority"}, {"D-007": "two\nlines"},
                              {"D-007": ""}, {"D-007": None}):
                with self.assertRaises(s.CitationError):
                    s.write_repository(c.root, summaries=summaries)

    def test_check_never_rewrites_drifting_inventory(self):
        with Candidate() as c:
            s = scanner()
            c.put("README.md", "`absent.md`\n")
            c.put(s.BOUNDARY_PATH, "# Boundary\n")
            s.write_repository(c.root)
            c.put(s.INVENTORY_PATH, "{}\n")
            before = (c.root / s.INVENTORY_PATH).read_bytes()
            with self.assertRaises(s.CitationError):
                s.check_repository(c.root)
            self.assertEqual((c.root / s.INVENTORY_PATH).read_bytes(), before)

    def test_generated_documents_do_not_recursively_cite_themselves(self):
        with Candidate() as c:
            s = scanner()
            c.put("README.md", "`absent.md`\n")
            c.put(s.BOUNDARY_PATH, "# Boundary\n")
            s.write_repository(c.root)
            first = s.scan_citations(c.root)
            self.assertTrue(all(o.source not in s.GENERATED for o in first))
            self.assertEqual(len(first), 1)

    def test_actual_current_private_references_have_public_pointers(self):
        s = scanner()
        rows = s.scan_citations(PRODUCT)
        private = [o for o in rows if o.kind == "private_path"]
        self.assertGreater(len(private), 0)
        self.assertTrue(all((PRODUCT / o.successor).is_file() for o in private))

    def test_all_ten_successors_resolve_decision_register(self):
        s = scanner()
        index = json.loads((PRODUCT / "docs/provenance/index-v1.json").read_text())
        self.assertEqual(len(index["entries"]), 10)
        rows = s.scan_citations(PRODUCT)
        for entry in index["entries"]:
            cited = [o for o in rows if o.source == entry["destination"] and o.target == s.REGISTER_PATH]
            self.assertGreaterEqual(len(cited), 1)
            self.assertTrue(all(o.resolution == "resolved" for o in cited))

    def test_boundary_explains_private_process_and_curated_replacements(self):
        text = (PRODUCT / scanner().BOUNDARY_PATH).read_text()
        for phrase in ("founder-private", "process", "separate", "README", "provenance", "register"):
            self.assertIn(phrase, text)
        self.assertNotIn("credential names", text)
        self.assertNotIn("@", text)

    def test_cli_round_trip_is_value_free(self):
        with Candidate() as c:
            s = scanner()
            c.put("README.md", "`absent.md`\n")
            c.put(s.BOUNDARY_PATH, "# Boundary\n")
            env = dict(os.environ, PYTHONPATH=str(PRODUCT))
            for flag in ("--write", "--check"):
                p = subprocess.run([sys.executable, "-m", "tools.citation_scan", flag], cwd=c.root,
                                   env=env, capture_output=True)
                self.assertEqual(p.returncode, 0)
                self.assertNotIn(b"absent.md", p.stdout + p.stderr)

    def test_parallel_writes_leave_complete_valid_documents(self):
        with Candidate() as c:
            s = scanner()
            c.put("README.md", "D-007 `absent.md`\n")
            c.put(s.BOUNDARY_PATH, "# Boundary\n")
            def run():
                try:
                    s.write_repository(c.root)
                    return True
                except s.CitationError:
                    return False
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _: run(), range(2)))
            self.assertTrue(any(outcomes))
            self.assertEqual(s.check_repository(c.root)["unresolved"], 1)

    def test_no_spike_four_or_unanchored_transition_is_invented(self):
        s = scanner()
        self.assertFalse((PRODUCT / "docs/provenance/spikes/004").exists())
        transitions = s.load_document(PRODUCT / s.TRANSITIONS_PATH)
        s.check_transitions(PRODUCT, transitions)


if __name__ == "__main__":
    unittest.main()
