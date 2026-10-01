"""Public README facts remain bound to immutable evidence, never live refs."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
PIN = "c35e88618fc78f6f8ddcb1f6e3f8dbd68b42b3ba"


class InputContracts(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("tools.docs_public.readme"),
                             "Pinned README evidence capture must exist")
        from tools.docs_public import readme
        return readme

    def test_rejected_attempt_preserves_manifest_accepted_panel(self):
        api = self.api()
        document = api.capture_inputs(ROOT, PIN)
        self.assertEqual([r["as_of_date"] for r in document["accepted_releases"]],
                         ["2026-07-15", "2026-08-05", "2026-08-19"])
        self.assertEqual(document["latest_attempt"]["outcome"], "rejected")
        self.assertEqual(document["latest_attempt"]["ended_at_utc"], "2026-10-01T03:56:55.950Z")
        self.assertEqual(document["accepted_releases"][-1]["release_revision"], 1)
        self.assertEqual(document["published_data_commit"], PIN)

    def test_moving_refs_and_missing_objects_fail_distinctly(self):
        api = self.api()
        for ref in ("published-data", "origin/published-data", "HEAD", "-" + "a" * 40):
            with self.assertRaises(api.ReadmeInputError) as caught:
                api.capture_inputs(ROOT, ref)
            self.assertEqual(caught.exception.category, "readme.explicit_commit_required")
        with self.assertRaises(api.ReadmeInputError) as caught:
            api.capture_inputs(ROOT, "0" * 40)
        self.assertEqual(caught.exception.category, "readme.unfetched_commit")

    def test_duplicate_keys_unknown_fields_and_empty_manifest_fail(self):
        api = self.api()
        with self.assertRaises(api.ReadmeInputError):
            api.decode_document(b'{"schema_version":1,"schema_version":1}')
        manifest = api.decode_document(subprocess.check_output(
            ["git", "show", PIN + ":manifest/published-manifest-v1.json"], cwd=ROOT))
        status = api.decode_document(subprocess.check_output(["git", "show", PIN + ":capture-status.json"], cwd=ROOT))
        catalog = api.decode_document((ROOT / "contracts/dbt-input-catalog-v1.json").read_bytes())
        for bad in (None, {}, dict(manifest, accepted_releases=[])):
            with self.assertRaises(api.ReadmeInputError):
                api.project_identity(bad, status, catalog)
        with self.assertRaises(api.ReadmeInputError) as caught:
            api.project_identity(manifest, None, catalog)
        self.assertEqual(caught.exception.category, "readme.missing_status")
        with self.assertRaises(api.ReadmeInputError):
            api.project_identity(dict(manifest, unapproved="x"), status, catalog)
        bad = copy.deepcopy(catalog)
        bad["releases"][0]["revision_fingerprint"] = "0" * 64
        with self.assertRaises(api.ReadmeInputError):
            api.project_identity(manifest, status, bad)

    def test_excerpt_twenty_five_passes_twenty_six_fails(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "dbt/models/intermediate/example.sql"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"select 1;\r\n" * 26)
            excerpt = api.extract_sql_excerpt(root, "dbt/models/intermediate/example.sql", [[1, 25]], "Purpose.")
            self.assertEqual(excerpt["line_count"], 25)
            self.assertEqual(excerpt["selected_text"], "select 1;\r\n" * 25)
            with self.assertRaises(api.ReadmeInputError):
                api.extract_sql_excerpt(root, "dbt/models/intermediate/example.sql", [[1, 26]], "Purpose.")
            with self.assertRaises(api.ReadmeInputError):
                api.extract_sql_excerpt(root, "../escape.sql", [[1, 2]], "Purpose.")

    def test_evidence_regenerates_without_network(self):
        api = self.api()
        one, two = api.capture_inputs(ROOT, PIN), api.capture_inputs(ROOT, PIN)
        self.assertEqual(api.encode_document(one), api.encode_document(two))
        with patch("socket.create_connection", side_effect=AssertionError("Network forbidden")):
            api.validate_inputs(ROOT, one)
        bad = copy.deepcopy(one)
        bad["accepted_releases"][0]["revision_fingerprint"] = "0" * 64
        with self.assertRaises(api.ReadmeInputError):
            api.validate_inputs(ROOT, bad)
        bad = dict(one, raw_manifest={})
        with self.assertRaises(api.ReadmeInputError):
            api.validate_inputs(ROOT, bad)


class ReadmeContracts(unittest.TestCase):
    def api(self):
        from tools.docs_public import readme
        return readme

    def test_complete_readme_has_all_decided_topics_and_separate_refresh(self):
        api = self.api()
        self.assertTrue(callable(getattr(api, "generate_readme", None)),
                        "All decided README topics must be generated")
        text = api.generate_readme(ROOT)
        headings = [line for line in text.splitlines() if line.startswith("# ") or line.startswith("## ")]
        self.assertEqual(headings, ["# California Charity Registry Monitor", *["## " + h for h in api.TOPICS[1:]]])
        self.assertEqual(len(headings), 18)
        for required in ("**cali**fornia **c**harity **o**bservatory", "retrocat",
                "no organization was interviewed about willingness to pay", "2026-09-02",
                "final three-release panel", "pending verification", api.MANUAL_DISCLOSURE,
                "2026-08-19", "2026-10-01T03:56:55.950Z", "source_contract_mismatch",
                "disappearance is not cure", "[Phase 10 report URL slot]", "CP1252", "QUOTE_NONE"):
            self.assertIn(required, text)
        for name in api.BLOCK_NAMES:
            self.assertEqual(text.count("<!-- calico:" + name + ":start -->"), 1)
            self.assertEqual(text.count("<!-- calico:" + name + ":end -->"), 1)

    def test_readme_excerpts_and_lineage_equal_pinned_sources(self):
        api = self.api()
        text = api.generate_readme(ROOT)
        evidence = api.decode_document((ROOT / api.EXCERPTS).read_bytes())
        for excerpt in evidence["excerpts"]:
            self.assertIn(excerpt["selected_text"], text)
            self.assertIn(excerpt["source_sha256"], text)
        from tools.docs_public.lineage import render_mermaid
        graph = api.decode_document((ROOT / api.LINEAGE).read_bytes())
        self.assertIn(render_mermaid(graph), text)

    def test_atomic_generation_and_offline_check_do_not_rewrite(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve()
            # A disposable copy uses the same offline Git object database.
            import shutil
            for name in api.LOCAL_AUTHORITIES + (api.INPUTS, api.EXCERPTS):
                destination = target / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, destination)
            shutil.copytree(ROOT / "dbt/models", target / "dbt/models", dirs_exist_ok=True)
            subprocess.run(["git", "init", "-q", str(target)], check=True)
            (target / ".git/objects/info/alternates").write_text(str(ROOT / ".git/objects") + "\n")
            api.generate_readme(target, write=True)
            before = (target / "README.md").stat().st_mtime_ns
            with patch("socket.create_connection", side_effect=AssertionError("Network forbidden")):
                api.check_readme(target)
            self.assertEqual(before, (target / "README.md").stat().st_mtime_ns)
            for mutation in ("<!-- calico:identity:start -->", "A trust " + "score."):
                (target / "README.md").write_text(api.generate_readme(target) + "\n" + mutation)
                with self.assertRaises(api.ReadmeInputError):
                    api.check_readme(target)
            api.generate_readme(target, write=True)
            (target / "LICENSE").write_text("Not the confirmed grant")
            with self.assertRaises(api.ReadmeInputError):
                api.check_readme(target)

    def test_unknown_inputs_and_excerpt_source_drift_fail(self):
        api = self.api()
        document = api.decode_document((ROOT / api.EXCERPTS).read_bytes())
        bad = copy.deepcopy(document)
        bad["excerpts"][0]["source_sha256"] = "0" * 64
        with self.assertRaises(api.ReadmeInputError):
            api.validate_excerpts(ROOT, bad)
        bad = dict(document, unknown=True)
        with self.assertRaises(api.ReadmeInputError):
            api.validate_excerpts(ROOT, bad)

    def test_cli_is_non_echo_and_check_is_offline(self):
        marker = "invalid" + "-private" + "-reference"
        result = subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "tools.docs_public", "inputs",
                                 "--published-ref", marker], cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(marker not in result.stdout + result.stderr, "CLI must not reflect invalid input")
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_no_metric_math_in_documentation_formatter(self):
        import ast
        api = self.api()
        tree = ast.parse((ROOT / "tools/docs_public/readme.py").read_text())
        arithmetic = [node for node in ast.walk(tree) if isinstance(node, ast.BinOp)
                      and isinstance(node.op, (ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow))]
        self.assertEqual(arithmetic, [])


if __name__ == "__main__":
    unittest.main()
