"""Observable contracts for the smaller, generated hosted replay evidence."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from functools import cache
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
_ENVIRONMENT = None


def setUpModule():
    global _ENVIRONMENT
    _ENVIRONMENT = patch.dict(os.environ)
    _ENVIRONMENT.start()
    for name in tuple(os.environ):
        if name.startswith(("CALICO_B2_", "B2_", "AWS_", "AZURE_", "GOOGLE_APPLICATION_")) or name in {
            "GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "GIT_SSH", "GIT_SSH_COMMAND", "GIT_ASKPASS",
            "SSH_ASKPASS", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_DIR", "GIT_WORK_TREE",
            "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_INDEX_FILE",
            "GIT_TEMPLATE_DIR", "GIT_EXEC_PATH"}:
            os.environ.pop(name, None)
    os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_NOSYSTEM="1")


def tearDownModule():
    _ENVIRONMENT.stop()


@cache
def _envelope_bytes():
    from tools import hosted_replay as driver
    from tests import test_hosted_replay as fixtures
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    identity = driver.ReplayRunTuple("mrnouiouat/calico", 1, 1, head)
    with patch.object(fixtures, "_run_tuple", return_value=identity), patch.object(fixtures, "_CHECKPOINTS", {}):
        return driver.collect_hosted_envelope(**fixtures._api_fixture()).to_json()


def envelope():
    from tools import hosted_replay as driver
    return driver.HostedEnvelope(driver.validate_envelope(_envelope_bytes()))


def public_module():
    from tools.docs_public import hosted_replay
    return hosted_replay


class HostedReplayProjectionContractTests(unittest.TestCase):
    def test_public_boundary_authority_exists(self):
        self.assertIsNotNone(importlib.util.find_spec("tools.docs_public.hosted_replay"),
                             "a separate public projection authority is required")

    def test_independent_closed_public_schema_exists(self):
        path = ROOT / "contracts/hosted-replay-public-v1.schema.json"
        self.assertTrue(path.is_file(), "the independent public evidence schema is required")
        schema = json.loads(path.read_text())
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["schema_version"]["const"], "hosted-replay-public-v1")

    def test_projection_preserves_exact_jobs_digests_audits_and_live_equality(self):
        public = public_module().project_hosted_replay_public(envelope()).to_dict()
        private = envelope().to_dict()
        self.assertEqual(public["run"]["head_sha"], private["run_tuple"]["head_sha"])
        self.assertEqual(public["jobs"], private["jobs"])
        self.assertEqual(public["worker_matrix"], private["worker_matrix"])
        self.assertEqual(public["live_boundary"], private["live_before"])
        self.assertTrue(public["live_boundary_unchanged"])
        for actual, source in zip(public["checkpoints"], private["checkpoints"]):
            for key in ("input_digest", "provenance_digest", "exports", "analytical_sha256", "outcomes"):
                self.assertEqual(actual[key], source["driver"][key])
            self.assertNotIn("driver", actual)
            self.assertTrue(all(row["byte_length"] > 0 for row in actual["byte_audits"]
                                if row["surface"] in {"status", "summary", "publication"}))
        self.assertLess(len(json.dumps(public)), len(json.dumps(private)))

    def test_all_public_objects_are_closed_required_nonnull_and_ordered(self):
        module = public_module()
        original = module.project_hosted_replay_public(envelope()).to_dict()
        schema = json.loads((ROOT / "contracts/hosted-replay-public-v1.schema.json").read_text())
        def check(value, contract, path=()):
            if isinstance(value, dict):
                for key in value:
                    for mutation in ("missing", "null"):
                        changed = copy.deepcopy(original)
                        target = changed
                        for part in path:
                            target = target[part]
                        if mutation == "missing":
                            target.pop(key)
                        else:
                            target[key] = None
                        with self.assertRaises(module.HostedReplayPublicError):
                            module.decode_hosted_replay_public(changed)
                changed = copy.deepcopy(original)
                target = changed
                for part in path:
                    target = target[part]
                target["unapproved_field"] = "excluded"
                with self.assertRaises(module.HostedReplayPublicError):
                    module.decode_hosted_replay_public(changed)
                if len(value) > 1:
                    changed = copy.deepcopy(original)
                    target = changed
                    for part in path[:-1]:
                        target = target[part]
                    reversed_object = dict(reversed(list(value.items())))
                    if path:
                        target[path[-1]] = reversed_object
                    else:
                        changed = reversed_object
                    with self.assertRaises(module.HostedReplayPublicError):
                        module.decode_hosted_replay_public(changed)
                for key, item in value.items():
                    check(item, contract["properties"][key], (*path, key))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    check(item, contract["items"], (*path, index))
        check(original, schema)

    def test_wrong_variants_and_tampered_private_envelopes_are_rejected(self):
        module = public_module()
        for candidate in (envelope().to_dict(), envelope()["checkpoints"][0]["driver"], None):
            with self.assertRaises(module.HostedReplayPublicError):
                module.decode_hosted_replay_public(candidate)
        changed = envelope().to_dict()
        changed["jobs"][0]["conclusion"] = "failure"
        with self.assertRaises(module.HostedReplayPublicError):
            module.project_hosted_replay_public(changed)

    def test_value_object_is_immutable_and_does_not_alias_envelope(self):
        module = public_module()
        source = envelope().to_dict()
        public = module.project_hosted_replay_public(source)
        saved = public.to_json()
        source["jobs"][0]["conclusion"] = "failure"
        public.to_dict()["jobs"][0]["conclusion"] = "failure"
        self.assertEqual(public.to_json(), saved)
        with self.assertRaises(AttributeError):
            public.encoded = b"changed"

    def test_duplicate_nonfinite_and_raw_private_fields_fail_without_echo(self):
        module = public_module()
        for raw in ('{"schema_version":1,"schema_version":2}', '{"unapproved":NaN}'):
            with self.assertRaises(module.HostedReplayPublicError) as caught:
                module.decode_hosted_replay_public(raw)
            self.assertEqual(str(caught.exception), caught.exception.category)
        original = module.project_hosted_replay_public(envelope()).to_dict()
        for field in ("raw_logs", "job_outputs", "local_path", "canary", "approval_locator", "owner"):
            changed = copy.deepcopy(original)
            changed[field] = "unapproved"
            with self.assertRaises(module.HostedReplayPublicError):
                module.decode_hosted_replay_public(changed)

    def test_reordered_lists_and_empty_audits_fail_closed(self):
        module = public_module()
        original = module.project_hosted_replay_public(envelope()).to_dict()
        for key in ("jobs", "worker_matrix", "checkpoints", "log_audit_commitments"):
            changed = copy.deepcopy(original)
            changed[key].reverse()
            with self.assertRaises(module.HostedReplayPublicError):
                module.decode_hosted_replay_public(changed)
        for key in ("exports", "byte_audits"):
            changed = copy.deepcopy(original)
            changed["checkpoints"][0][key].reverse()
            with self.assertRaises(module.HostedReplayPublicError):
                module.decode_hosted_replay_public(changed)
        changed = copy.deepcopy(original)
        changed["log_audit_commitments"][0]["audit"]["byte_length"] = 0
        with self.assertRaises(module.HostedReplayPublicError):
            module.decode_hosted_replay_public(changed)


class HostedReplayEvidenceClassTests(unittest.TestCase):
    def test_rendering_is_deterministic_and_separates_four_evidence_classes(self):
        module = public_module()
        public = module.project_hosted_replay_public(envelope())
        rendered = module.render_hosted_replay_markdown(public)
        self.assertEqual(rendered, module.render_hosted_replay_markdown(public.to_dict()))
        for phrase in ("workflow_dispatch", "fixture_hosted_replay", "live_source_observation",
                       "actual_schedule_observation", "real_restore_republish_observation",
                       "reconstructed", "status timestamps", "historical"):
            self.assertIn(phrase, rendered)

    def test_fixture_capture_and_dispatched_calendar_cannot_be_relabelled(self):
        module = public_module()
        original = module.project_hosted_replay_public(envelope()).to_dict()
        for key in ("live_source_observation", "actual_schedule_observation"):
            changed = copy.deepcopy(original)
            changed["evidence_classes"][key] = "measured"
            with self.assertRaises(module.HostedReplayPublicError):
                module.decode_hosted_replay_public(changed)
        changed = copy.deepcopy(original)
        changed["run"]["event"] = "schedule"
        with self.assertRaises(module.HostedReplayPublicError):
            module.decode_hosted_replay_public(changed)

    def test_immutable_historical_tuple_is_preserved_and_cannot_be_changed(self):
        module = public_module()
        original = module.project_hosted_replay_public(envelope()).to_dict()
        private = envelope()["historical_real_republish"]
        for key in ("run_id", "head_sha", "published_data_commit", "published_manifest_sha256"):
            self.assertEqual(original["historical_real_republish"][key], private[key])
            changed = copy.deepcopy(original)
            changed["historical_real_republish"][key] = 2 if key == "run_id" else "0" * len(private[key])
            with self.assertRaises(module.HostedReplayPublicError):
                module.decode_hosted_replay_public(changed)


if __name__ == "__main__":
    unittest.main()
