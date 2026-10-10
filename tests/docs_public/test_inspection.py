"""Immutable inspection and precisely bounded publication changes."""
from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def api(test):
    test.assertIsNotNone(importlib.util.find_spec("tools.docs_public.inspection"),
                         "Immutable public inspection must exist before freeze")
    from tools.docs_public import inspection
    return inspection


def git(root, *args):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True)
    if result.returncode:
        raise AssertionError("Disposable Git operation failed")
    return result.stdout.decode().strip()


@contextmanager
def fixture():
    with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
        root = Path(directory) / "product"
        git(ROOT, "clone", "--quiet", "--shared", "--", str(ROOT), str(root))
        for index, oid in enumerate(git(ROOT, "for-each-ref", "--format=%(objectname)").splitlines()):
            git(root, "update-ref", "refs/synthetic-source/" + str(index), oid)
        for name in git(ROOT, "ls-files", "--cached", "--others", "--exclude-standard").splitlines():
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        git(root, "config", "user.name", "Synthetic Inspector")
        git(root, "config", "user.email", "synthetic" + "@example.invalid")
        git(root, "add", "--", *git(root, "ls-files", "--cached", "--others", "--exclude-standard").splitlines())
        git(root, "commit", "--quiet", "--allow-empty", "-m", "Synthetic prior inspection snapshot")
        yield root, git(root, "rev-parse", "HEAD")


def document(m, root, head):
    return {"schema_version": "final-inspection-checklist-v1", "inspected_snapshot": head,
        "recorded_at": "2026-10-10", "substantive_inputs": m.build_substantive_input_manifest(head, root=root),
        "semantic_inventory_sha256": hashlib.sha256((root / "powerbi/semantic-model-inventory-v1.json").read_bytes()).hexdigest(),
        "published_data_commit": m.gate_e.HISTORICAL_REAL_OBSERVATION["published_data_commit"],
        "published_manifest_sha256": m.gate_e.HISTORICAL_REAL_OBSERVATION["published_manifest_sha256"],
        "checks": {key: "pass" for key in m.CHECKS}, "result": "pass"}


def publish(m, root):
    from tests.docs_public.test_gate_e import FinalUrlRenderingTests
    ledger = json.loads((root / m.gate_e.AUTHORITY_PATH).read_bytes())
    ledger["report"] = FinalUrlRenderingTests().report()
    (root / m.gate_e.AUTHORITY_PATH).write_bytes(m.gate_e._encode(ledger))
    from tools.docs_public.readme import generate_readme
    generate_readme(root, write=True)
    m.gate_e.generate_gate_e_pair(ledger, root / m.gate_e.AUTHORITY_PATH, root / m.gate_e.MARKDOWN_PATH, root=root)


class PublicInspectionContractTests(unittest.TestCase):
    def test_immutable_inspection_contract_exists_before_freeze(self):
        m = api(self)
        for name in ("build_substantive_input_manifest", "validate_public_inspection",
                     "render_public_inspection", "check_final_url_diff"):
            self.assertTrue(callable(getattr(m, name, None)))

    def test_synthetic_prior_snapshot_runs_all_existing_gates_end_to_end(self):
        m = api(self)
        with fixture() as (root, head):
            generated = subprocess.run([sys.executable, "-m", "tools.docs_public", "inspection-generate", "--snapshot", head],
                                       cwd=root, capture_output=True)
            self.assertEqual(generated.returncode, 0, "Synthetic inspection CLI must run the real existing gates")
            result = json.loads((root / m.JSON_PATH).read_bytes())
            self.assertEqual(result["checks"], {key: "pass" for key in m.CHECKS})
            manifest = {row["path"] for row in result["substantive_inputs"]}
            for path in ("contracts/hosted-replay-driver-v1.schema.json", "contracts/hosted-replay-envelope-v1.schema.json",
                    "contracts/hosted-replay-public-v1.schema.json", "tools/hosted_replay.py", "tools/docs_public/hosted_replay.py",
                    ".github/workflows/publication-route.yml", ".github/workflows/hosted-replay.yml",
                    "calico_capture/calendar.py", "docs/decisions/condition-4-hosted-outcomes.md",
                    "tests/docs_public/test_gate_e.py", "powerbi/semantic-model-inventory-v1.json"):
                self.assertIn(path, manifest)
            self.assertFalse(manifest & m.FREEZE_OUTPUTS)
            self.assertEqual(m.validate_public_inspection(result, root=root), result)
            self.assertIn(head, m.render_public_inspection(result, root=root))
            self.assertEqual((root / m.MARKDOWN_PATH).read_text(), m.render_public_inspection(result, root=root))
            from tools.citation_scan.scanner import write_repository
            write_repository(root)
            git(root, "add", "--", *sorted(m.FREEZE_OUTPUTS))
            git(root, "commit", "--quiet", "-m", "Synthetic inspection freeze")
            checked = subprocess.run([sys.executable, "-m", "tools.docs_public", "inspection-check"], cwd=root, capture_output=True)
            self.assertEqual(checked.returncode, 0, "Committed synthetic checklist must validate through its real CLI")
            (root / "tools/docs_public/inspection.py").write_bytes((root / "tools/docs_public/inspection.py").read_bytes() + b"\n")
            with self.assertRaisesRegex(m.InspectionContractError, "inspection.snapshot_tree_mismatch"):
                m.check_public_inspection(root=root)

    def test_closed_schema_rejects_self_future_private_unknown_duplicate_and_extra_evidence(self):
        m = api(self)
        with fixture() as (root, head):
            original = document(m, root, head)
            mutations = [dict(original, ci_runs=[]), dict(original, own_commit=head), dict(original, own_hash="0" * 64),
                dict(original, inspected_snapshot="HEAD"), dict(original, result="fail"),
                dict(original, checks={**original["checks"], "unknown": "pass"}),
                dict(original, substantive_inputs=[*original["substantive_inputs"], original["substantive_inputs"][0]])]
            bad = copy.deepcopy(original); bad["substantive_inputs"][0]["path"] = ".planning/private.json"; mutations.append(bad)
            bad = copy.deepcopy(original); bad["substantive_inputs"].append({"path": m.JSON_PATH, "sha256": "0" * 64}); mutations.append(bad)
            for bad in mutations:
                with self.assertRaises(m.InspectionContractError):
                    m.validate_public_inspection(bad, root=root)
            raw = m.gate_e._encode(original).decode().replace('"result": "pass"', '"result": "pass", "result": "pass"')
            with self.assertRaises(m.InspectionContractError):
                m.validate_public_inspection(raw, root=root)

    def test_concurrent_ref_movement_and_interrupted_output_do_not_leave_partial_checklists(self):
        m = api(self)
        with fixture() as (root, head):
            original = document(m, root, head)
            with patch.object(m, "_refs", side_effect=[b"before", b"after"]), patch.object(m, "_run_checks", return_value=original["checks"]):
                with self.assertRaisesRegex(m.InspectionContractError, "inspection.concurrent_change"):
                    m.build_public_inspection(head, root=root)
            with patch.object(m, "build_public_inspection", return_value=original), patch.object(m.gate_e, "_exchange_directories", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    m.generate_public_inspection(head, root=root)
            self.assertFalse((root / m.JSON_PATH).exists())
            self.assertFalse((root / m.MARKDOWN_PATH).exists())

    def test_checklist_cannot_claim_the_commit_containing_it(self):
        m = api(self)
        with fixture() as (root, head):
            original = document(m, root, head)
            (root / m.JSON_PATH).write_bytes(m.gate_e._encode(original))
            git(root, "add", "--", m.JSON_PATH)
            git(root, "commit", "--quiet", "-m", "Synthetic checklist successor")
            original["inspected_snapshot"] = git(root, "rev-parse", "HEAD")
            with self.assertRaisesRegex(m.InspectionContractError, "inspection.self_snapshot"):
                m.validate_public_inspection(original, root=root)


class MigrationRedactionGateTests(unittest.TestCase):
    def test_exact_named_successor_is_present_and_private_original_is_absent(self):
        m = api(self)
        with fixture() as (root, head):
            m.check_migration_redaction(head, root=root)

    def test_reachable_changed_original_path_cannot_be_hidden_by_later_repair(self):
        m = api(self)
        with fixture() as (root, head):
            path = root / m.MIGRATION_PATH
            approved = path.read_bytes()
            path.write_bytes(approved + b"\nUnapproved original successor\n")
            git(root, "add", "--", m.MIGRATION_PATH); git(root, "commit", "--quiet", "-m", "Synthetic bad historical migration")
            path.write_bytes(approved)
            git(root, "add", "--", m.MIGRATION_PATH); git(root, "commit", "--quiet", "-m", "Synthetic restored migration")
            with self.assertRaisesRegex(m.InspectionContractError, "inspection.migration_redaction_failed"):
                m.check_migration_redaction(git(root, "rev-parse", "HEAD"), root=root)

    def test_renamed_original_digest_is_rejected_from_any_reachable_path(self):
        m = api(self)
        with fixture() as (root, head):
            raw = b"Synthetic excluded original identity\n"
            (root / "docs/renamed-investigation.md").write_bytes(raw)
            git(root, "add", "--", "docs/renamed-investigation.md"); git(root, "commit", "--quiet", "-m", "Synthetic renamed original")
            with patch.object(m, "MIGRATION_ORIGINAL_SHA256", hashlib.sha256(raw).hexdigest()):
                with self.assertRaisesRegex(m.InspectionContractError, "inspection.migration_redaction_failed"):
                    m.check_migration_redaction(git(root, "rev-parse", "HEAD"), root=root)


class ExactFinalDiffTests(unittest.TestCase):
    def test_verified_transition_is_exact_in_worktree_and_immutable_successor(self):
        m = api(self)
        with fixture() as (root, head):
            self.assertEqual(m.check_final_url_diff(head, head, root=root), {"result": "no_change"})
            publish(m, root)
            self.assertEqual(m.check_final_url_diff(head, root=root), {"result": "pass"})
            git(root, "add", "--", *sorted(m.REPORT_PATHS)); git(root, "commit", "--quiet", "-m", "Synthetic verified report transition")
            final = git(root, "rev-parse", "HEAD")
            self.assertEqual(m.check_final_url_diff(head, final, root=root), {"result": "pass"})
            checked = subprocess.run([sys.executable, "-m", "tools.docs_public", "final-url-diff", "--base", head, "--final", final],
                                     cwd=root, capture_output=True)
            self.assertEqual(checked.returncode, 0, "Exact immutable publication diff must validate through the real CLI")

    def test_every_other_path_and_normalizable_or_unrelated_report_change_fails(self):
        m = api(self)
        with fixture() as (root, head):
            publish(m, root)
            baseline = {name: (root / name).read_bytes() for name in m.REPORT_PATHS}
            paths = ["tools/docs_public/inspection.py", m.SCHEMA_PATH, "contracts/publication-exports-v3.json",
                "docs/provenance/citation-inventory-v1.json", "contracts/dbt-input-catalog-v1.json",
                ".github/workflows/publication-route.yml"]
            for name in paths:
                path = root / name; previous = path.read_bytes(); path.write_bytes(previous + b"\n")
                with self.assertRaisesRegex(m.InspectionContractError, "inspection.unexpected_final_paths"):
                    m.check_final_url_diff(head, root=root)
                path.write_bytes(previous)
            for name in m.REPORT_PATHS:
                path = root / name; path.write_bytes(baseline[name] + b"\n")
                with self.assertRaises(m.InspectionContractError): m.check_final_url_diff(head, root=root)
                path.write_bytes(baseline[name])
            (root / "README.md").write_bytes(baseline["README.md"].replace(b"\n", b"\r\n"))
            with self.assertRaisesRegex(m.InspectionContractError, "inspection.non_report_readme_change"):
                m.check_final_url_diff(head, root=root)
            (root / "README.md").write_bytes(baseline["README.md"])
            ledger = json.loads(baseline[m.gate_e.AUTHORITY_PATH])
            ledger = dict(reversed(list(ledger.items())))
            (root / m.gate_e.AUTHORITY_PATH).write_bytes(m.gate_e._encode(ledger))
            with self.assertRaisesRegex(m.InspectionContractError, "inspection.non_report_ledger_change"):
                m.check_final_url_diff(head, root=root)
            ledger = json.loads(baseline[m.gate_e.AUTHORITY_PATH]); ledger["recorded_at"] = "2026-10-09"
            (root / m.gate_e.AUTHORITY_PATH).write_bytes(m.gate_e._encode(ledger))
            with self.assertRaisesRegex(m.InspectionContractError, "inspection.non_report_ledger_change"):
                m.check_final_url_diff(head, root=root)

    def test_invalid_refs_incomplete_fields_mode_drift_and_concurrency_fail_closed(self):
        m = api(self)
        with fixture() as (root, head):
            for value in ("", None, "HEAD", "0" * 40):
                with self.assertRaises(m.InspectionContractError): m.check_final_url_diff(value, root=root)
            publish(m, root)
            ledger = json.loads((root / m.gate_e.AUTHORITY_PATH).read_bytes())
            ledger["report"].pop("observed_at")
            (root / m.gate_e.AUTHORITY_PATH).write_bytes(m.gate_e._encode(ledger))
            with self.assertRaises(m.InspectionContractError): m.check_final_url_diff(head, root=root)
            publish(m, root)
            (root / "README.md").chmod(0o755)
            with self.assertRaisesRegex(m.InspectionContractError, "inspection.unexpected_final_mode"):
                m.check_final_url_diff(head, root=root)
            (root / "README.md").chmod(0o644)
            with patch.object(m, "_refs", side_effect=[b"before", b"after"]):
                with self.assertRaisesRegex(m.InspectionContractError, "inspection.concurrent_change"):
                    m.check_final_url_diff(head, root=root)


class StableReportCitationTests(unittest.TestCase):
    def test_both_states_have_the_same_schema_resolved_locator(self):
        m = api(self)
        from tools.citation_scan import scanner
        with fixture() as (root, head):
            before = [row for row in scanner.scan_citations(root) if row.locator == scanner.REPORT_LOCATOR]
            self.assertEqual(len(before), 2)
            self.assertIsNone(scanner.resolve_report_citation(root))
            publish(m, root)
            after = [row for row in scanner.scan_citations(root) if row.locator == scanner.REPORT_LOCATOR]
            self.assertEqual(before, after)
            self.assertEqual(scanner.resolve_report_citation(root), "https://app.powerbi.com/view?r=synthetic")
            (root / "README.md").write_text((root / "README.md").read_text().replace("r=synthetic", "r=changed"))
            with self.assertRaises(scanner.CitationError): scanner.scan_citations(root)

    def test_other_external_urls_keep_their_exact_destinations(self):
        from tools.citation_scan import scanner
        original = "https://example.invalid/one"
        changed = "https://example.invalid/two"
        one = scanner._raw_occurrences("docs/example.md", "[External](" + original + ")")
        two = scanner._raw_occurrences("docs/example.md", "[External](" + changed + ")")
        self.assertEqual(one[0][2], original); self.assertEqual(two[0][2], changed)
