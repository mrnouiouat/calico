"""Gate E observable evidence contracts and non-echo failure directions."""
from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CommittedPortfolioAuthorityTests(unittest.TestCase):
    def test_ten_real_conditions_have_their_required_evidence(self):
        m = module(self)
        self.assertTrue(callable(getattr(m, "build_portfolio_document", None)),
                        "The authority must bind each actual condition to its required evidence")
        d = m.build_portfolio_document(root=ROOT)
        m.validate_portfolio_authority(d, root=ROOT)
        self.assertEqual([r["condition"] for r in d["conditions"]], list(range(1, 11)))
        self.assertEqual(d["conditions"][3]["status"], "pass_with_disclosed_deviation")
        self.assertEqual(m.check_gate_e(ROOT / m.AUTHORITY_PATH, ROOT / m.MARKDOWN_PATH), d)
        self.assertNotIn("app.powerbi.com/view", json.dumps(d))


class PortfolioEvidenceResolutionTests(unittest.TestCase):
    def test_missing_real_evidence_cannot_be_replaced_by_readme_or_tests(self):
        m = module(self)
        self.assertTrue(callable(getattr(m, "build_portfolio_document", None)))
        d = m.build_portfolio_document(root=ROOT)
        for n in range(10):
            bad = copy.deepcopy(d)
            bad["conditions"][n]["evidence"] = authority(m)["conditions"][n]["evidence"]
            with self.assertRaises(m.GateEEvidenceError): m.validate_portfolio_authority(bad, root=ROOT)

    def test_unknown_null_stale_future_and_self_evidence_fail(self):
        m = module(self)
        self.assertTrue(callable(getattr(m, "build_portfolio_document", None)))
        d = m.build_portfolio_document(root=ROOT)
        for field, value in (("evidence", None), ("evidence", []), ("condition", 1.0), ("status", "unknown")):
            bad = copy.deepcopy(d); bad["conditions"][0][field] = value
            with self.assertRaises(m.GateEEvidenceError): m.validate_portfolio_authority(bad, root=ROOT)
        for locator in (m.AUTHORITY_PATH, m.MARKDOWN_PATH, "docs/evidence/gate-e/spike-era-requirements-v1.json"):
            bad = copy.deepcopy(d)
            bad["conditions"][0]["evidence"][0] = {"type": "file_sha256", "locator": locator, "sha256": "0" * 64, "claim": "Invalid reference"}
            with self.assertRaises(m.GateEEvidenceError): m.validate_portfolio_authority(bad, root=ROOT)
        bad = copy.deepcopy(d); bad["conditions"][0]["evidence"][0]["head_sha"] = "0" * 40
        with self.assertRaises(m.GateEEvidenceError): m.validate_portfolio_authority(bad, root=ROOT)

    def test_equality_publication_proof_is_valid_and_precision_is_preserved(self):
        m = module(self)
        self.assertTrue(callable(getattr(m, "build_portfolio_document", None)))
        d = m.build_portfolio_document(root=ROOT)
        m.validate_portfolio_authority(d, root=ROOT)
        real = next(e for e in d["conditions"][4]["evidence"] if e.get("evidence_class") == "real_restore_republish_observation")
        self.assertIn("no_change", real["claim"])
        self.assertIn(m.HISTORICAL_REAL_OBSERVATION["published_manifest_sha256"], real["claim"])


class SpikeEraAuditContractTests(unittest.TestCase):
    def test_historical_identity_and_exclusion_evidence_contains_the_claimed_fields(self):
        m = module(self)
        d = m.build_spike_audit_document(root=ROOT)
        locators = {item["locator"] for item in d["rows"][5]["evidence"]}
        source = "docs/provenance/spikes/001-archive-sample-validation/archive-sample-manifest.json.md"
        self.assertIn(source, locators)
        text = (ROOT / source).read_text()
        for field in ("original_url", "wayback_timestamp", "received_bytes", "sha256", "parsed_rows", "logical_release", "list"):
            self.assertIn('"' + field + '"', text)
        self.assertIn("docs/evidence/gate-a/spike-001-successor-v1.json", locators)
        exclusion = "docs/provenance/spikes/005-project-recommendation/README.md#usefulness-boundary"
        self.assertIn(exclusion, {item["locator"] for item in d["rows"][10]["evidence"]})
        text = (ROOT / exclusion.partition("#")[0]).read_text().split("## Usefulness boundary", 1)[1].split("##", 1)[0]
        for phrase in ("stakeholder interviews", "investigation queues", "email notifications", "predictions", "causal explanations"):
            self.assertIn(phrase, text)

    def test_exact_requirement_clauses_and_superseding_decisions(self):
        m = module(self)
        self.assertTrue(callable(getattr(m, "build_spike_audit_document", None)),
                        "The exact predecessor requirement enumeration must be implemented")
        d = m.build_spike_audit_document(root=ROOT)
        m.validate_spike_audit_document(d, root=ROOT)
        self.assertEqual([r["text"] for r in d["rows"]], list(m.SPIKE_REQUIREMENT_TEXTS))
        self.assertEqual([r["decision"] for r in d["rows"] if r["disposition"] == "superseded"], ["D-007", "D-008", "D-012"])
        self.assertEqual(sum(r["disposition"] == "satisfied" for r in d["rows"]), 11)
        self.assertEqual(sum(r["disposition"] == "deferred_not_v1" for r in d["rows"]), 1)

    def test_missing_extra_duplicate_reordered_null_and_unknown_rows_fail(self):
        m = module(self)
        self.assertTrue(callable(getattr(m, "build_spike_audit_document", None)))
        d = m.build_spike_audit_document(root=ROOT)
        for rows in (None, [], d["rows"][:1], d["rows"][:-1], d["rows"] * 2, list(reversed(d["rows"])), [None] * 15):
            bad = copy.deepcopy(d); bad["rows"] = rows
            with self.assertRaises(m.GateEEvidenceError): m.validate_spike_audit_document(bad, root=ROOT)
        for field, value in (("decision", "D-999"), ("disposition", "pass"), ("text", "invented"), ("id", 1.0), ("evidence", [])):
            bad = copy.deepcopy(d); bad["rows"][0][field] = value
            with self.assertRaises(m.GateEEvidenceError): m.validate_spike_audit_document(bad, root=ROOT)


class GateEAtomicGenerationTests(unittest.TestCase):
    def test_process_interruption_after_exchange_leaves_the_entire_new_pair(self):
        m = module(self)
        self.assertTrue(callable(getattr(m, "generate_gate_e_pair", None)))
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            root = Path(directory)
            paths = (root / "docs/evidence/a.json", root / "docs/provenance/a.md")
            m._generate_common_pair({"generation": 1}, *paths, "generation one\n")
            script = """import os, sys
from pathlib import Path
from tools.docs_public import gate_e as m
root = Path(sys.argv[1])
exchange = m._exchange_directories
def interrupt(a, b):
    exchange(a, b)
    os._exit(91)
m._exchange_directories = interrupt
m._generate_common_pair({'generation': 2}, root/'docs/evidence/a.json', root/'docs/provenance/a.md', 'generation two\\n')
"""
            result = subprocess.run([sys.executable, "-c", script, str(root)], cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 91)
            self.assertEqual(json.loads(paths[0].read_bytes()), {"generation": 2})
            self.assertEqual(paths[1].read_text(), "generation two\n")

    def test_generators_produce_byte_identical_complete_pairs(self):
        m = module(self)
        self.assertTrue(callable(getattr(m, "generate_gate_e_pair", None)),
                        "Generation must publish the authority and rendering in one directory transaction")
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            root = Path(directory)
            for builder, generator, paths in ((m.build_portfolio_document, m.generate_gate_e_pair, (m.AUTHORITY_PATH, m.MARKDOWN_PATH)),
                                              (m.build_spike_audit_document, m.generate_spike_audit_pair, (m.SPIKE_AUTHORITY_PATH, m.SPIKE_MARKDOWN_PATH))):
                d = builder(root=ROOT)
                targets = tuple(root / p for p in paths)
                generator(d, *targets, root=ROOT)
                before = [p.read_bytes() for p in targets]
                generator(d, *targets, root=ROOT)
                self.assertEqual(before, [p.read_bytes() for p in targets])

    def test_interruption_and_concurrent_generation_preserve_the_complete_pair(self):
        from unittest.mock import patch
        import concurrent.futures
        m = module(self)
        self.assertTrue(callable(getattr(m, "generate_gate_e_pair", None)))
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            d = m.build_portfolio_document(root=ROOT)
            paths = (Path(directory) / m.AUTHORITY_PATH, Path(directory) / m.MARKDOWN_PATH)
            m.generate_gate_e_pair(d, *paths, root=ROOT)
            before = [p.read_bytes() for p in paths]
            with patch.object(m, "_exchange_directories", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt): m.generate_gate_e_pair(d, *paths, root=ROOT)
            self.assertEqual(before, [p.read_bytes() for p in paths])
            def generate():
                try: m.generate_gate_e_pair(d, *paths, root=ROOT)
                except m.GateEEvidenceError as error: self.assertEqual(error.category, "gate_e.lock_busy")
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _: generate(), range(8)))
            self.assertEqual(before, [p.read_bytes() for p in paths])

ROOT = Path(__file__).resolve().parents[2]


class FinalUrlRenderingTests(unittest.TestCase):
    def test_closed_report_states_are_available_before_freeze(self):
        from tools.docs_public import gate_e as api
        self.assertTrue(callable(getattr(api, "validate_report_state", None)),
                        "The ledger must validate both bounded report states before freeze")
        self.assertEqual(api.validate_report_state({}), None)

    def report(self):
        return {"url": "https://app.powerbi.com/view?r=synthetic",
                "observed_at": "2026-10-01",
                "anonymous_observation": {"access_mode": "signed_out", "pages": {
                    "published_registry_change": "pass", "release_quality": "pass",
                    "cohort_persistence": "pass", "organization_lookup": "pass"},
                    "release_banner_source_retired": "pass", "lookup_selection_guard": "pass"}}

    def test_valid_report_and_schema_agree(self):
        from tools.docs_public import gate_e as api
        report = self.report()
        self.assertEqual(api.validate_report_state({"report": report}), report)
        document = api.build_portfolio_document()
        document["report"] = report
        self.assertEqual(api.validate_portfolio_authority(document), document)
        self.assertIn(report["url"], api.render_gate_e_markdown(document))

    def test_invalid_report_combinations_fail_without_echo(self):
        from tools.docs_public import gate_e as api
        values = [None, {}, dict(self.report(), extra=True)]
        values += [dict(self.report(), url=value) for value in (
            None, "", " ", "http://example.invalid", "https://", "https://user:secret" + "@example.invalid",
            "https://example.invalid/a)injection", "https://example.invalid/%0a",
            "https://app.powerbi.com/view?r=%ZZ", "https://app.powerbi.com/view?r=%0a")]
        values += [dict(self.report(), observed_at=value) for value in (None, "", "2099-01-01", "2026-02-30")]
        values += [{key: value for key, value in self.report().items() if key != missing}
                   for missing in self.report()]
        wrong = self.report()
        wrong["anonymous_observation"]["pages"]["organization_lookup"] = "fail"
        values.append(wrong)
        for value in values:
            with self.assertRaises(api.GateEEvidenceError) as caught:
                api.validate_report_state({"report": value})
            self.assertEqual(caught.exception.category, "gate_e.invalid_report_state")


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
        self.assertIn("[README.md](../../README.md)", rendered)

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
            {"type": "ci_run", "locator": "https://github.com/mrnouiouat/calico/actions/runs/37985365302/attempts/1", "claim": "Hosted fixture replay", "recorded_at": "2026-10-10", "conclusion": "success", "head_sha": head, "evidence_class": "fixture_hosted_replay", "event": "workflow_dispatch"},
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
            result = subprocess.run([sys.executable, "-m", "tools.docs_public", "gate-e-generate", "--authority", str(path), "--output", str(output)], cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            before = output.read_bytes()
            result = subprocess.run([sys.executable, "-m", "tools.docs_public", "gate-e-check", "--authority", str(path), "--markdown", str(output)], cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode()); self.assertEqual(output.read_bytes(), before)
            path.write_text('{"unapproved":"PRIVATE_SENTINEL"}')
            result = subprocess.run([sys.executable, "-m", "tools.docs_public", "gate-e-check", "--authority", str(path), "--markdown", str(output)], cwd=ROOT, capture_output=True)
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


def measured(m):
    hosted = json.loads((ROOT / "docs/evidence/gate-e/hosted-replay-v1.json").read_text())
    return hosted, copy.deepcopy(m.HISTORICAL_REAL_OBSERVATION)


def observations(*, scheduled=False, complete=False):
    outcomes = ("accepted", "no_new_release", "rejected") if complete else ("rejected",)
    return [{"evidence_class": "actual_schedule_observation" if scheduled else "live_source_observation",
             "run_id": 37693376162 + n, "run_attempt": 1,
             "event": "schedule" if scheduled else "workflow_dispatch",
             "head_sha": "ae8a612b0e0be8b47b1a15d366b1a41eb3433677",
             "created_at": "2026-10-07T22:01:00Z", "completed_at": "2026-10-07T22:10:09Z",
             "recorded_at": "2026-10-10", "conclusion": "success", "outcome": outcome,
             "outcome_basis": "recorded_capture_status", "log_privacy": "no_findings",
             "log_finding_count": 0} for n, outcome in enumerate(outcomes)]


def draft(m, *, complete=False):
    hosted, real = measured(m)
    return m.derive_condition_four_disposition(hosted_replay=hosted, real_republish=real,
        live_observations=observations(complete=complete), scheduled_observations=observations(scheduled=True, complete=complete))


class ConditionFourDispositionEqualityTests(unittest.TestCase):
    def test_derivation_follows_measured_outcomes_and_class_boundaries(self):
        m = module(self)
        self.assertTrue(callable(getattr(m, "derive_condition_four_disposition", None)),
                        "Residual gaps must be derived from measured evidence")
        d = draft(m)
        self.assertEqual(d["proved_outcomes"], ["accepted", "no_new_release", "rejected"])
        self.assertEqual([r["outcome"] for r in d["residual_gaps"]], ["accepted", "no_new_release", "accepted", "no_new_release"])
        self.assertEqual(d["recommended_status"], "pass_with_disclosed_deviation")
        self.assertTrue(d["amendment_required"])
        self.assertEqual(tuple(d["evidence_classes"]), m.EVIDENCE_CLASSES)
        complete = draft(m, complete=True)
        self.assertEqual(complete["residual_gaps"], [])
        self.assertEqual(complete["recommended_status"], "pass")
        self.assertFalse(complete["amendment_required"])

    def test_missing_unknown_mislabeled_and_ambiguous_observations_fail(self):
        m = module(self); hosted, real = measured(m)
        for value in (None, [], [{}], [dict(observations(scheduled=True)[0], event="workflow_dispatch")],
                      [dict(observations(scheduled=True)[0], evidence_class="fixture_hosted_replay")],
                      [dict(observations(scheduled=True)[0], outcome=None)],
                      [dict(observations(scheduled=True)[0], completed_at="2026-02-30T00:00:00Z")]):
            with self.assertRaises(m.GateEEvidenceError):
                m.derive_condition_four_disposition(hosted_replay=hosted, real_republish=real,
                    live_observations=observations(), scheduled_observations=value)
        duplicate = observations(scheduled=True) * 2
        with self.assertRaises(m.GateEEvidenceError):
            m.derive_condition_four_disposition(hosted_replay=hosted, real_republish=real,
                live_observations=observations(), scheduled_observations=duplicate)

    def test_wrong_measured_head_run_hash_or_historical_tuple_fails(self):
        m = module(self); hosted, real = measured(m)
        for key, value in (("head_sha", "0" * 40), ("run_id", 1), ("event", "schedule"), ("conclusion", "failure")):
            changed = copy.deepcopy(hosted); changed["run"][key] = value
            with self.assertRaises(m.GateEEvidenceError):
                m.derive_condition_four_disposition(hosted_replay=changed, real_republish=real,
                    live_observations=observations(), scheduled_observations=observations(scheduled=True))
        for key in ("head_sha", "published_data_commit", "published_manifest_sha256", "policy_sha256", "run_id"):
            changed = copy.deepcopy(real); changed[key] = 1 if key == "run_id" else "0" * len(changed[key])
            with self.assertRaises(m.GateEEvidenceError):
                m.derive_condition_four_disposition(hosted_replay=hosted, real_republish=changed,
                    live_observations=observations(), scheduled_observations=observations(scheduled=True))

    def test_only_three_approval_additions_are_allowed_and_all_draft_values_equal(self):
        m = module(self); hosted, real = measured(m); d = draft(m)
        final = m.finalize_condition_four_disposition(draft=d, approved_at="2026-10-10", approved_by_role="repository owner")
        self.assertEqual(set(final) - set(d), {"approval_required", "approved_at", "approved_by_role"})
        self.assertEqual({key: final[key] for key in d}, d)
        m.validate_final_condition_four_disposition(draft=d, final=final, hosted_replay=hosted, real_republish=real)
        for key in d:
            changed = copy.deepcopy(final)
            value = changed[key]
            changed[key] = list(reversed(value)) if isinstance(value, list) else "changed"
            if changed[key] == value: changed[key] = None
            with self.assertRaises(m.GateEEvidenceError):
                m.validate_final_condition_four_disposition(draft=d, final=changed, hosted_replay=hosted, real_republish=real)
        changed = copy.deepcopy(d); changed["residual_gaps"] = []
        with self.assertRaises(m.GateEEvidenceError):
            m.finalize_condition_four_disposition(draft=changed, approved_at=None, approved_by_role=None)
        changed = copy.deepcopy(d); changed["evidence_classes"]["fixture_hosted_replay"]["run"]["run_id"] = 1
        with self.assertRaises(m.GateEEvidenceError):
            m.validate_final_condition_four_disposition(draft=changed, final=final, hosted_replay=hosted, real_republish=real)
        changed = copy.deepcopy(final); changed["evidence_classes"] = dict(reversed(list(changed["evidence_classes"].items())))
        with self.assertRaises(m.GateEEvidenceError):
            m.validate_final_condition_four_disposition(draft=d, final=changed, hosted_replay=hosted, real_republish=real)

    def test_approval_dates_roles_and_proof_complete_null_approval(self):
        m = module(self)
        for when, role in ((None, None), ("2026-02-30", "repository owner"), ("2026-10-10", "someone"), ("2026-10-10", "repository-owner"), ("2026-10-08", "repository owner"), ("2099-01-01", "repository owner")):
            with self.assertRaises(m.GateEEvidenceError):
                m.finalize_condition_four_disposition(draft=draft(m), approved_at=when, approved_by_role=role)
        complete = draft(m, complete=True)
        final = m.finalize_condition_four_disposition(draft=complete, approved_at=None, approved_by_role=None)
        self.assertFalse(final["approval_required"]); self.assertIsNone(final["approved_at"]); self.assertIsNone(final["approved_by_role"])
        with self.assertRaises(m.GateEEvidenceError):
            m.finalize_condition_four_disposition(draft=complete, approved_at="2026-10-10", approved_by_role="repository owner")


class ConditionFourDisclosureTests(unittest.TestCase):
    def test_public_projection_has_exact_residual_meaning_and_no_private_fields(self):
        m = module(self); hosted, real = measured(m); d = draft(m)
        final = m.finalize_condition_four_disposition(draft=d, approved_at="2026-10-10", approved_by_role="repository owner")
        public = m.render_condition_four_public_decision(draft=d, final=final, hosted_replay=hosted, real_republish=real)
        self.assertEqual(public, m.render_condition_four_public_decision(draft=json.dumps(d), final=json.dumps(final), hosted_replay=hosted, real_republish=real))
        self.assertIn("Fixture acceptance is not live-source acceptance", public)
        self.assertIn("Dispatched calendar cases are not actual scheduled observations", public)
        self.assertIn("2026-10-10", public)
        for gap in d["residual_gaps"]:
            self.assertIn(gap["evidence_class"] + " | " + gap["outcome"], public)
        for excluded in ("repository owner", "approved_by_role", "approval_required", ".planning", "calico-build"):
            self.assertNotIn(excluded, public)
        complete = draft(m, complete=True)
        complete_final = m.finalize_condition_four_disposition(draft=complete, approved_at=None, approved_by_role=None)
        self.assertIn("No residual gaps", m.render_condition_four_public_decision(draft=complete, final=complete_final, hosted_replay=hosted, real_republish=real))

    def test_private_unknown_duplicate_and_changed_draft_or_final_fail(self):
        m = module(self); hosted, real = measured(m); d = draft(m)
        for key in d:
            changed = copy.deepcopy(d); changed[key] = None
            with self.assertRaises(m.GateEEvidenceError): m.decode_condition_four_draft(changed)
        for changed in (dict(d, private_path="../private"), dict(d, derived_at="2026-02-30")):
            with self.assertRaises(m.GateEEvidenceError): m.decode_condition_four_draft(changed)
        raw = json.dumps(d).replace('"schema_version":', '"schema_version":"duplicate","schema_version":', 1)
        with self.assertRaises(m.GateEEvidenceError): m.decode_condition_four_draft(raw)
        final = m.finalize_condition_four_disposition(draft=d, approved_at="2026-10-10", approved_by_role="repository owner")
        final["residual_gaps"].reverse()
        with self.assertRaises(m.GateEEvidenceError):
            m.render_condition_four_public_decision(draft=d, final=final, hosted_replay=hosted, real_republish=real)

    def test_historical_privacy_findings_are_disclosed_and_not_waived(self):
        m = module(self); hosted, real = measured(m)
        live = observations(complete=True); live[0].update(log_privacy="absolute_local_path_findings", log_finding_count=66)
        d = m.derive_condition_four_disposition(hosted_replay=hosted, real_republish=real,
            live_observations=live, scheduled_observations=observations(scheduled=True, complete=True))
        self.assertEqual(d["residual_gaps"], [{"evidence_class": "live_source_observation", "outcome": "privacy_boundary", "reason": "historical_log_privacy_not_clean"}])

    def test_finalize_check_render_cli_round_trip_and_repository_owner_role_mapping(self):
        m = module(self); hosted, real = measured(m)
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            paths = {key: Path(temporary) / (key + ".json") for key in ("draft", "final", "hosted", "real", "public")}
            for key, value in (("draft", draft(m)), ("hosted", hosted), ("real", real)):
                paths[key].write_text(json.dumps(value))
            base = [sys.executable, "-m", "tools.docs_public"]
            result = subprocess.run(base + ["condition-four-finalize", "--draft", str(paths["draft"]), "--final", str(paths["final"]), "--approved-at", "2026-10-10", "--approved-by-role", "repository-owner"], cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(json.loads(paths["final"].read_text())["approved_by_role"], "repository owner")
            flags = [item for key in ("draft", "final", "hosted", "real") for item in ("--" + key, str(paths[key]))]
            result = subprocess.run(base + ["condition-four-render", *flags, "--output", str(paths["public"])], cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            result = subprocess.run(base + ["condition-four-check", *flags, "--public", str(paths["public"])], cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            paths["public"].write_text("changed")
            result = subprocess.run(base + ["condition-four-check", *flags, "--public", str(paths["public"])], cwd=ROOT, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
