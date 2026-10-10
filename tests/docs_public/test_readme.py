"""Public README facts remain bound to immutable evidence, never live refs."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
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
        self.assertEqual(headings, [("# " if i == 0 else "## ") + api.TOPICS[i] for i in api.README_TOPIC_ORDER])
        self.assertEqual(len(headings), 18)
        self.assertNotIn("willingness to pay", text)
        self.assertNotIn("California Charity Registry Monitor", text)
        self.assertLess(text.index("## Report"), text.index("## Build, measure, retire"))
        self.assertLess(text.index("<!-- calico:walkthrough:start -->"), text.index("## Architecture"))
        self.assertEqual(text.count("<details>"), 6)
        self.assertEqual(text.count("</details>"), 6)
        self.assertIn("public access is pending final publication approval", text)
        self.assertIn("**One observed finding:**", text)
        for required in ("**cali**fornia **c**harity **o**bservatory", "retrocat",
                "California Charity Observatory", "2026-09-02",
                "final three-release panel", "pending verification", api.MANUAL_DISCLOSURE,
                "**Current scope:**", "Adopting the successor publication is future work",
                "retirement described here concerns the commercial proposition",
                "2026-08-19", "2026-10-01T03:56:55.950Z", "source_contract_mismatch",
                "disappearance is not cure", "[Phase 10 report URL slot]", "CP1252", "QUOTE_NONE",
                "Fixture acceptance is not live-source acceptance", "dispatched calendar cases are not actual scheduled observations",
                "exactly six residuals", "66, 22 and 66", "not evidence of excluded-content leakage or a privacy waiver",
                "founder action #5 remain beyond v1", "No trust, risk, fraud, quality or robustness score"):
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
        result = subprocess.run([sys.executable, "-m", "tools.docs_public", "inputs",
                                 "--published-ref", marker], cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(marker not in result.stdout + result.stderr, "CLI must not reflect invalid input")
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_cli_non_echo_in_checkout_without_local_environment(self):
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory).resolve()
            for package in ("tools", "calico_capture", "calico_dbt", "calico_publish", "calico_landing"):
                shutil.copytree(ROOT / package, checkout / package,
                                ignore=shutil.ignore_patterns("__pycache__"))
            self.assertFalse((checkout / ".venv").exists())
            # Run the real test against a real CLI, with no mocked subprocess.
            with patch(__name__ + ".ROOT", checkout):
                self.test_cli_is_non_echo_and_check_is_offline()

    def test_no_metric_math_in_documentation_formatter(self):
        import ast
        api = self.api()
        tree = ast.parse((ROOT / "tools/docs_public/readme.py").read_text())
        arithmetic = [node for node in ast.walk(tree) if isinstance(node, ast.BinOp)
                      and isinstance(node.op, (ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow))]
        self.assertEqual(arithmetic, [])


class WrittenWalkthroughLinkContracts(unittest.TestCase):
    def test_generated_readme_links_the_repository_document(self):
        from tools.docs_public import readme as api
        content = api.generate_readme(ROOT)
        self.assertIn(api.WALKTHROUGH_LINK, content)
        self.assertNotIn("owner recording pending", content)
        self.assertTrue((ROOT / "docs/walkthrough.md").is_file())


    def test_video_or_other_readme_edits_fail_strict_check(self):
        from tools.docs_public import readme as api
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve()
            for name in api.LOCAL_AUTHORITIES + (api.INPUTS, api.EXCERPTS):
                destination = target / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, destination)
            shutil.copytree(ROOT / "dbt/models", target / "dbt/models", dirs_exist_ok=True)
            subprocess.run(["git", "init", "-q", str(target)], check=True)
            (target / ".git/objects/info/alternates").write_text(str(ROOT / ".git/objects") + "\n")
            api.generate_readme(target, write=True)
            source = (target / "README.md").read_text()
            api.check_readme(target)
            for damaged in (source.replace(api.WALKTHROUGH_LINK, "Walkthrough: https://youtu.be/AbCdEfGhI_j"),
                            source + "\nArbitrary prose\n", source.replace("CP1252", "UTF-8"),
                            source.replace("[Phase 10 report URL slot]", "https://example.invalid/report"),
                            source.replace("<!-- calico:walkthrough:end -->", "")):
                (target / "README.md").write_text(damaged)
                with self.assertRaises(api.ReadmeInputError):
                    api.check_readme(target)


class FinalUrlDiffContractTests(unittest.TestCase):
    def test_only_report_block_changes_between_report_states(self):
        from tools.docs_public import readme as api
        from tests.docs_public.test_gate_e import FinalUrlRenderingTests
        from tools.docs_public.gate_e import AUTHORITY_PATH
        unpublished = api.generate_readme(ROOT)
        self.assertIn("<!-- calico:report:start -->", unpublished)
        original = api._read
        ledger = json.loads((ROOT / AUTHORITY_PATH).read_bytes())
        ledger["report"] = FinalUrlRenderingTests().report()
        def read(root, name):
            return json.dumps(ledger).encode() if name == AUTHORITY_PATH else original(root, name)
        with patch.object(api, "_read", side_effect=read):
            published = api.generate_readme(ROOT)
        def outside(text):
            left, tail = text.split("<!-- calico:report:start -->")
            _, right = tail.split("<!-- calico:report:end -->")
            return left + right
        self.assertEqual(outside(unpublished), outside(published))
        self.assertIn(ledger["report"]["url"], published)
        self.assertNotIn("[Phase 10 report URL slot]", published)


class HostedRepublishRunbookContracts(unittest.TestCase):
    RUNBOOKS = ("docs/capture-runbook.md", "docs/build-modes.md", "docs/powerbi-refresh-runbook.md")
    CORRECTION = "## Correction — 2026-10-01: private policy enables hosted republish"

    def assert_current_operations(self, text):
        self.assertIn(self.CORRECTION, text)
        current = text.split(self.CORRECTION, 1)[1].split("\n## ", 1)[0]
        for required in ("**Supersedes:**", "2026-09-16", "2026-09-02", "seed-policy",
                "readback", "B2-only", "archive/v1/", "`listFiles`, `readFiles`, `writeFiles`",
                "publication key retains exactly `listFiles`, `readFiles`",
                "No new key or wider prefix", "workflow artifact", "category-only",
                "classification_version", "preflight.public_eligibility_missing",
                "every immutable private version", "private manifest versions", "Refresh now",
                "not an accepted live capture"):
            self.assertIn(required, current, "current operations contract is incomplete")
        for contradiction in ("scheduled Power BI refresh is proven", "republish is an accepted live capture",
                "skipped schedules are rejected", "only the latest private version needs deletion"):
            self.assertNotIn(contradiction, current, "current operations must preserve the bounded claim")

    def test_all_three_runbooks_share_the_corrected_mechanism_and_retention_boundary(self):
        for path in self.RUNBOOKS:
            with self.subTest(path=path):
                self.assert_current_operations((ROOT / path).read_text())

    def test_contradictory_refresh_retirement_or_retention_claims_are_rejected(self):
        for path in self.RUNBOOKS:
            text = (ROOT / path).read_text()
            for contradiction in ("scheduled Power BI refresh is proven", "republish is an accepted live capture",
                    "skipped schedules are rejected", "only the latest private version needs deletion"):
                with self.subTest(path=path, claim=contradiction), self.assertRaises(AssertionError):
                    self.assert_current_operations(text.replace(self.CORRECTION,
                        self.CORRECTION + "\n" + contradiction))

    def test_historical_refusal_and_manual_service_fallback_remain_visible(self):
        capture = (ROOT / self.RUNBOOKS[0]).read_text()
        self.assertIn("**The hosted republish path is refused, by design.**", capture)
        self.assertIn("Historical procedure, superseded on 2026-10-01", capture)
        from tools.docs_public import readme as api
        self.assertIn(api.MANUAL_DISCLOSURE, api.generate_readme(ROOT))


if __name__ == "__main__":
    unittest.main()
