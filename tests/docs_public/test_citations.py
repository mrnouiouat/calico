"""Observable citation contracts over disposable candidate repositories."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PRODUCT = Path(__file__).resolve().parents[2]


class Candidate:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "Synthetic citation test")
        self.git("config", "user.email", "synthetic" + "@example.invalid")
        return self

    def __exit__(self, *args):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.PIPE)

    def put(self, path, text):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def commit(self):
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and ".git" not in p.parts:
                self.git("add", "--", p.relative_to(self.root).as_posix())
        self.git("commit", "-m", "Retain synthetic citation anchor")
        return self.git("rev-parse", "HEAD").decode().strip()


def scanner():
    return importlib.import_module("tools.citation_scan.scanner")


class CitationTests(unittest.TestCase):
    def test_supported_occurrences_are_scanned(self):
        try:
            s = scanner()
        except ModuleNotFoundError:
            self.fail("Supported citation occurrences require the citation scanner")
        with Candidate() as c:
            c.put("docs/page.md", "[one](target.md) ![two](target.md) `target.md`\n")
            c.put("docs/target.md", "# Target\n")
            result = s.scan_citations(c.root)
            self.assertEqual(len(result), 3)
            self.assertEqual({o.target for o in result}, {"docs/target.md"})
            self.assertEqual({o.resolution for o in result}, {"resolved"})
            self.assertEqual(len({o.locator for o in result}), 3)

    def test_reference_fragment_encoding_and_historical_separator(self):
        with Candidate() as c:
            c.put("docs/page.md", "[ref][r]\n[r]: target%20file.md#target\n`docs\\target file.md`\n[anchor](#local)\n# Local\n")
            c.put("docs/target file.md", "# Target\n")
            rows = scanner().scan_citations(c.root)
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(o.resolution == "resolved" for o in rows))

    def test_external_sql_and_commands_are_not_missing_files(self):
        with Candidate() as c:
            c.put("README.md", "[web](https://example.invalid/a) `https://example.invalid/a` `schema.table`\n`python -m tool --out <path>/out.json` `scripts/**` `/api/data/...`\n")
            rows = scanner().scan_citations(c.root)
            self.assertEqual(len(rows), 3)
            self.assertEqual({o.resolution for o in rows}, {"syntax"})
            self.assertEqual(scanner().inventory_document(rows)["entries"], [])

    def test_private_absolute_values_never_enter_records(self):
        marker = "/" + "Users/synthetic/citation-secret-zqxx"
        with Candidate() as c:
            c.put("evidence.json", json.dumps({"private_path": marker}))
            c.put("docs/decisions/planning-directory-not-published.md", "# Boundary\n")
            rows = scanner().scan_citations(c.root)
            data = json.dumps(scanner().inventory_document(rows))
            self.assertNotIn(marker, data)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].kind, "private_path")
            self.assertEqual(rows[0].disposition, "annotated_left_behind")
            self.assertEqual(rows[0].successor, "docs/decisions/planning-directory-not-published.md")

    def test_exact_inventory_rejects_missing_extra_duplicate_and_unknown(self):
        with Candidate() as c:
            c.put("README.md", "`absent.md` `absent.md`\n")
            s = scanner()
            rows = s.scan_citations(c.root)
            expected = s.inventory_document(rows)
            s.check_inventory(rows, expected)
            for change in (lambda d: d["entries"].pop(),
                           lambda d: d["entries"].append(d["entries"][0]),
                           lambda d: d["entries"][0].update(disposition="invented"),
                           lambda d: d["entries"][0].update(target="fabricated.md"),
                           lambda d: d.update(unknown=True)):
                altered = json.loads(json.dumps(expected))
                change(altered)
                with self.assertRaises(s.CitationError):
                    s.check_inventory(rows, altered)

    def test_empty_candidate_is_valid_but_missing_input_is_not(self):
        with Candidate() as c:
            s = scanner()
            self.assertEqual(s.inventory_document(s.scan_citations(c.root))["entries"], [])
            for root in (None, c.root / "absent"):
                with self.assertRaises(s.CitationError):
                    s.scan_citations(root)

    def test_unreadable_tracked_file_fails(self):
        with Candidate() as c:
            c.put("README.md", "# Retained\n")
            c.commit()
            (c.root / "README.md").unlink()
            with self.assertRaises(scanner().CitationError):
                scanner().scan_citations(c.root)

    def test_ignored_runtime_documents_do_not_enter_candidate(self):
        with Candidate() as c:
            c.put(".gitignore", "runtime/\n")
            c.put("runtime/private.md", "`missing.md`\n")
            self.assertEqual(scanner().scan_citations(c.root), [])

    def test_directory_resolution_and_confinement(self):
        with Candidate() as c:
            c.put("README.md", "`docs/` [escape](../../outside.md)\n")
            c.put("docs/page.md", "# Page\n")
            rows = scanner().scan_citations(c.root)
            self.assertEqual([o.resolution for o in rows], ["resolved", "outside_root"])
            self.assertEqual(rows[1].target, "[outside_repository]")

    def test_symlink_candidate_and_target_fail_closed(self):
        with Candidate() as c:
            c.put("README.md", "[link](linked.md)\n")
            c.put("real.md", "# Real\n")
            (c.root / "linked.md").symlink_to(c.root / "real.md")
            with self.assertRaises(scanner().CitationError):
                scanner().scan_citations(c.root)
            (c.root / "linked.md").unlink()
            c.put("README.md", "![link](linked.png)\n")
            (c.root / "linked.png").symlink_to(c.root / "real.md")
            with self.assertRaises(scanner().CitationError):
                scanner().scan_citations(c.root)

    def test_missing_fragment_is_unresolved(self):
        with Candidate() as c:
            c.put("README.md", "[link](target.md#missing)\n")
            c.put("target.md", "# Present\n")
            self.assertEqual(scanner().scan_citations(c.root)[0].resolution, "missing_fragment")

    def test_duplicate_json_keys_and_malformed_private_path_fail(self):
        with Candidate() as c:
            s = scanner()
            for text in ('{"private_path":"a.md","private_path":"b.md"}',
                         '{"private_path":null}', '{broken'):
                c.put("evidence.json", text)
                with self.assertRaises(s.CitationError):
                    s.scan_citations(c.root)

    def test_duplicate_inventory_json_keys_are_rejected(self):
        with Candidate() as c:
            c.put("inventory.json", '{"entries":[],"entries":[]}')
            with self.assertRaises(scanner().CitationError):
                scanner().load_document(c.root / "inventory.json")

    def test_historical_removed_and_repointed_anchors(self):
        with Candidate() as c:
            s = scanner()
            c.put("README.md", "[old](old.md)\n")
            old_rows = s.scan_citations(c.root)
            commit = c.commit()
            old_bytes = (c.root / "README.md").read_bytes()
            c.put("README.md", "[new](new.md)\n")
            c.put("new.md", "# New\n")
            document = s.transition_document(c.root, commit, old_rows, old_bytes)
            self.assertEqual(len(document["entries"]), 1)
            s.check_transitions(c.root, document)
            self.assertEqual(s.inventory_document(s.scan_citations(c.root))["entries"], [])
            for key, value in (("prior_sha256", "0" * 64), ("prior_blob", "0" * 40),
                               ("prior_commit", "0" * 40), ("disposition", "invented")):
                altered = json.loads(json.dumps(document))
                altered["entries"][0][key] = value
                with self.assertRaises(s.CitationError):
                    s.check_transitions(c.root, altered)
            c.put("README.md", "# Removed\n")
            removed = s.transition_document(c.root, commit, old_rows, old_bytes)
            self.assertEqual(removed["entries"][0]["disposition"], "removed")
            s.check_transitions(c.root, removed)

    def test_transition_cannot_claim_a_current_occurrence(self):
        with Candidate() as c:
            s = scanner()
            c.put("README.md", "[old](old.md)\n")
            rows = s.scan_citations(c.root)
            commit = c.commit()
            self.assertEqual(s.transition_document(c.root, commit, rows, (c.root / "README.md").read_bytes())["entries"], [])

    def test_atomic_output_survives_interruption(self):
        with Candidate() as c:
            s = scanner()
            c.put("inventory.json", "retained\n")
            with patch.object(s.os, "replace", side_effect=OSError("synthetic-secret")):
                with self.assertRaises(s.CitationError):
                    s.atomic_write(c.root / "inventory.json", b"replacement\n")
            self.assertEqual((c.root / "inventory.json").read_text(), "retained\n")
            self.assertEqual(list(c.root.glob("*.tmp")), [])

    def test_cli_errors_and_arguments_do_not_echo(self):
        with Candidate() as c:
            marker = "synthetic-citation-secret-zqxx"
            env = dict(os.environ, PYTHONPATH=str(PRODUCT))
            for args in (("--unknown-" + marker,), ("--check",)):
                completed = subprocess.run([sys.executable, "-m", "tools.citation_scan", *args],
                                           cwd=c.root, env=env, capture_output=True)
                self.assertNotEqual(completed.returncode, 0)
                self.assertNotIn(marker.encode(), completed.stdout + completed.stderr)

    def test_bound_parser_rejects_oversize_input(self):
        with Candidate() as c:
            c.put("README.md", "x" * (scanner().MAX_DOCUMENT_BYTES + 1))
            with self.assertRaises(scanner().CitationError):
                scanner().scan_citations(c.root)


if __name__ == "__main__":
    unittest.main()
