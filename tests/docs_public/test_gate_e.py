"""Gate E observable evidence contracts and non-echo failure directions."""
from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def module(case):
    case.assertIsNotNone(importlib.util.find_spec("tools.docs_public.gate_e"),
                         "Gate E must validate and render openable evidence")
    return importlib.import_module("tools.docs_public.gate_e")


def authority(m):
    evidence = {"type": "file_sha256", "locator": "README.md",
                "claim": "Published explanation", "sha256": hashlib.sha256((ROOT / "README.md").read_bytes()).hexdigest()}
    return {"schema_version": "portfolio-ready-v1", "recorded_at": "2026-10-10",
            "conditions": [{"condition": n, "text": text, "status": "pass",
                            "evidence": [copy.deepcopy(evidence)], "amendment": None}
                           for n, text in enumerate(m.CONDITION_TEXTS, 1)]}


class GateEEvidenceContractTests(unittest.TestCase):
    def test_ten_conditions_validate_and_render_deterministically(self):
        m = module(self)
        d = authority(m)
        self.assertEqual(m.decode_gate_e_document(json.dumps(d), root=ROOT), d)
        rendered = m.render_gate_e_markdown(d, root=ROOT)
        self.assertEqual(rendered, m.render_gate_e_markdown(copy.deepcopy(d), root=ROOT))
        self.assertIn("scheduled and manually dispatched", rendered)

    def test_independent_closed_schema(self):
        m = module(self)
        schema = json.loads((ROOT / m.SCHEMA_PATH).read_text())
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["conditions"]["minItems"], 10)

    def test_resolvable_commit_test_manifest_and_dated_observations(self):
        m = module(self)
        d = authority(m)
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        rows = [
            {"type": "ci_run", "locator": "https://github.com/mrnouiouat/calico/actions/runs/37985365302/attempts/1", "claim": "Hosted fixture replay", "recorded_at": "2026-10-10", "conclusion": "success", "head_sha": head, "evidence_class": "fixture_hosted_replay"},
            {"type": "commit", "locator": head, "claim": "Committed product"},
            {"type": "test_id", "locator": "tests.docs_public.test_gate_e.GateEEvidenceContractTests.test_ten_conditions_validate_and_render_deterministically", "claim": "Strict evidence contract"},
            {"type": "manifest", "locator": "docs/evidence/gate-e/hosted-replay-v1.json", "claim": "Closed measured projection", "sha256": hashlib.sha256((ROOT / "docs/evidence/gate-e/hosted-replay-v1.json").read_bytes()).hexdigest()},
            {"type": "service_observation", "locator": "https://app.powerbi.com/view?r=observed", "claim": "Recorded Service observation", "recorded_at": "2026-10-08", "conclusion": "observed"},
            {"type": "owner_attestation", "locator": "README.md#limitations", "claim": "Recorded signed-out observation", "recorded_at": "2026-10-08", "conclusion": "observed"},
        ]
        d["conditions"][3]["evidence"] = sorted(rows, key=lambda r: (r["type"], r["locator"]))
        m.validate_gate_e_document(d, root=ROOT)


class GateEEvidenceFailingDirectionTests(unittest.TestCase):
    def test_condition_enumeration_is_exact_and_not_coerced(self):
        m = module(self)
        for indices in ([], list(range(1, 10)), [1, 2, 3, 4, 4, 6, 7, 8, 9, 10], list(range(10, 0, -1)), ["1", *range(2, 11)], [True, *range(2, 11)], [0, *range(2, 11)]):
            d = authority(m)
            d["conditions"] = [dict(d["conditions"][min(int(n), 10)-1], condition=n) for n in indices]
            with self.assertRaises(m.GateEEvidenceError):
                m.validate_gate_e_document(d, root=ROOT)

    def test_all_required_fields_and_evidence_fail_closed(self):
        m = module(self)
        original = authority(m)
        for key in original:
            for value in (None, "", []):
                d = copy.deepcopy(original); d[key] = value
                with self.assertRaises(m.GateEEvidenceError): m.validate_gate_e_document(d, root=ROOT)
            d = copy.deepcopy(original); del d[key]
            with self.assertRaises(m.GateEEvidenceError): m.validate_gate_e_document(d, root=ROOT)
        for key in original["conditions"][3]:
            d = copy.deepcopy(original); del d["conditions"][3][key]
            with self.assertRaises(m.GateEEvidenceError): m.validate_gate_e_document(d, root=ROOT)
        for value in (None, [], [{}]):
            d = authority(m); d["conditions"][3]["evidence"] = value
            with self.assertRaises(m.GateEEvidenceError): m.validate_gate_e_document(d, root=ROOT)

    def test_unresolved_private_hash_date_text_and_unknown_fields_fail(self):
        m = module(self)
        for patch in ({"locator": "../private-approval.json"}, {"locator": ".planning/approval.json"}, {"locator": "README.md#missing-anchor"}, {"sha256": "0" * 64}, {"locator": "missing.md"}, {"type": "unknown"}, {"claim": "private\ncontent"}, {"extra": "excluded"}):
            d = authority(m); d["conditions"][3]["evidence"][0].update(patch)
            with self.assertRaises(m.GateEEvidenceError): m.validate_gate_e_document(d, root=ROOT)
        for patch in ({"recorded_at": "2026-02-30"}, {"recorded_at": "2099-01-01"}, {"extra": True}):
            d = authority(m); d.update(patch)
            with self.assertRaises(m.GateEEvidenceError): m.validate_gate_e_document(d, root=ROOT)
        d = authority(m); d["conditions"][3]["text"] = "Different condition"
        with self.assertRaises(m.GateEEvidenceError): m.validate_gate_e_document(d, root=ROOT)
        d = authority(m); d["conditions"][3]["evidence"] = [{"type": "test_id", "locator": "tests.docs_public.test_gate_e.Missing.test_missing", "claim": "Missing test"}]
        with self.assertRaises(m.GateEEvidenceError): m.validate_gate_e_document(d, root=ROOT)

    def test_duplicate_json_keys_nonfinite_and_duplicate_evidence_fail(self):
        m = module(self)
        for raw in ('{"schema_version":1,"schema_version":2}', '{"conditions":NaN}'):
            with self.assertRaises(m.GateEEvidenceError): m.decode_gate_e_document(raw, root=ROOT)
        d = authority(m); d["conditions"][3]["evidence"] *= 2
        with self.assertRaises(m.GateEEvidenceError): m.validate_gate_e_document(d, root=ROOT)

    def test_pass_deviation_requires_public_dated_hashed_amendment(self):
        m = module(self)
        d = authority(m)
        amendment = {"locator": "README.md#limitations", "sha256": hashlib.sha256((ROOT / "README.md").read_bytes()).hexdigest(), "recorded_at": "2026-10-10"}
        d["conditions"][3]["amendment"] = amendment
        with self.assertRaises(m.GateEEvidenceError): m.validate_gate_e_document(d, root=ROOT)
        d["conditions"][3]["status"] = "pass_with_disclosed_deviation"
        m.validate_gate_e_document(d, root=ROOT)
        for value in (None, {}, dict(amendment, locator="../approval.json"), dict(amendment, recorded_at=None)):
            d["conditions"][3]["amendment"] = value
            with self.assertRaises(m.GateEEvidenceError): m.validate_gate_e_document(d, root=ROOT)

    def test_cli_errors_do_not_echo_input_and_check_is_read_only(self):
        m = module(self)
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            path = Path(temporary) / "authority.json"; path.write_text(json.dumps(authority(m)))
            output = Path(temporary) / "evidence.md"
            result = subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "tools.docs_public", "gate-e-generate", "--authority", str(path), "--output", str(output)], cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            before = output.read_bytes()
            result = subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "tools.docs_public", "gate-e-check", "--authority", str(path), "--markdown", str(output)], cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode()); self.assertEqual(output.read_bytes(), before)
            path.write_text('{"unapproved":"PRIVATE_SENTINEL"}')
            result = subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "tools.docs_public", "gate-e-check", "--authority", str(path), "--markdown", str(output)], cwd=ROOT, capture_output=True)
            self.assertNotEqual(result.returncode, 0); self.assertNotIn(b"PRIVATE_SENTINEL", result.stderr)


class ConditionFourEvidenceClassTests(unittest.TestCase):
    def test_closed_evidence_classes_and_actual_event_are_distinct(self):
        m = module(self)
        self.assertEqual(m.EVIDENCE_CLASSES, ("fixture_hosted_replay", "live_source_observation", "actual_schedule_observation", "real_restore_republish_observation"))
        d = authority(m)
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        item = {"type": "ci_run", "locator": "https://github.com/mrnouiouat/calico/actions/runs/37985365302/attempts/1", "claim": "Recorded schedule", "recorded_at": "2026-10-10", "conclusion": "success", "head_sha": head, "evidence_class": "actual_schedule_observation", "event": "workflow_dispatch"}
        d["conditions"][3]["evidence"] = [item]
        with self.assertRaises(m.GateEEvidenceError): m.validate_gate_e_document(d, root=ROOT)
