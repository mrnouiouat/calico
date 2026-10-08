"""Behavior contracts for isolated actual-SQL replay evidence."""

import importlib.util
import copy
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest


class HostedReplayAcceptedTracerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools import hosted_replay as replay
        cls.replay = replay
        cls.run_tuple = replay.ReplayRunTuple("fixture/replay", 1, 1, "a" * 40)
        cls.temp = Path(tempfile.gettempdir()).resolve()
        cls.prepared = replay.prepare_hosted_checkpoint(runner_temp=cls.temp, run_tuple=cls.run_tuple, scenario="accepted")
        cls.published = replay.run_publication_worker(runner_temp=cls.temp, run_tuple=cls.run_tuple, scenario="accepted",
            route_authorized=True, expected_input_digest=cls.prepared["input_digest"],
            expected_provenance_digest=cls.prepared["provenance_digest"])

    def test_staged_replay_authority_exists(self):
        self.assertIsNotNone(importlib.util.find_spec("tools.hosted_replay"),
                             "staged capture and publication authority is required")

    def test_actual_capture_builds_named_sql_outputs_without_candidate_publication(self):
        doc = self.prepared.to_dict()
        self.assertEqual(doc["input_profile"], "fixture")
        self.assertEqual(doc["outcomes"][0]["outcome"], "accepted")
        self.assertEqual(doc["outcomes"][0]["build_calls"], 1)
        self.assertEqual(doc["outcomes"][0]["publication_calls"], 0)
        self.assertEqual(doc["local_parent"], doc["local_commit"])
        self.assertEqual(len(doc["source_catalog"]), 4)
        self.assertEqual((doc["dbt_model_count"], doc["dbt_test_count"]), (32, 228))
        self.assertTrue(all(row["row_count"] > 0 for row in doc["exports"]
            if row["export_name"] in {"dim_public_organizations", "fct_public_status_observations"}))

    def test_authorized_fresh_worker_matches_exact_source_provenance_and_exports(self):
        before, after = self.prepared.to_dict(), self.published.to_dict()
        for key in ("input_digest", "provenance_digest", "source_catalog", "exports", "analytical_sha256"):
            self.assertEqual(before[key], after[key])
        self.assertTrue(after["reconstructed"])
        self.assertEqual(after["evidence_class"], "fresh_job_reconstruction")
        self.assertEqual(after["outcomes"][0]["publication_calls"], 1)
        self.assertNotEqual(after["local_commit"], after["local_parent"])

    def test_authorization_and_input_mismatch_fail_before_workspace_creation(self):
        for kwargs in ({"route_authorized": False}, {"route_authorized": 1}, {"scenario": "repeat"},
                       {"expected_input_digest": "0" * 64}, {"expected_provenance_digest": None}):
            arguments = dict(runner_temp=self.temp, run_tuple=self.run_tuple, scenario="accepted", route_authorized=True,
                expected_input_digest=self.prepared["input_digest"], expected_provenance_digest=self.prepared["provenance_digest"])
            arguments.update(kwargs)
            with self.subTest(case=list(kwargs)), patch.object(self.replay, "replay_workspace") as workspace:
                with self.assertRaises(self.replay.ReplayError):
                    self.replay.run_publication_worker(**arguments)
                workspace.assert_not_called()

    def test_provenance_mismatch_cannot_publish_candidate(self):
        original = self.replay._publish
        publications = []
        def observe(workspace, staging, **kwargs):
            publications.append(staging.name)
            return original(workspace, staging, **kwargs)
        with patch.object(self.replay, "_publish", side_effect=observe):
            with self.assertRaises(self.replay.ReplayError):
                self.replay.run_publication_worker(runner_temp=self.temp, run_tuple=self.run_tuple, scenario="accepted",
                    route_authorized=True, expected_input_digest=self.prepared["input_digest"], expected_provenance_digest="0" * 64)
        self.assertEqual(publications, ["baseline-publication"])


class HostedReplayEvidenceContractTests(unittest.TestCase):
    def test_driver_contract_exists(self):
        from pathlib import Path
        self.assertTrue((Path(__file__).resolve().parents[1] / "contracts" /
                         "hosted-replay-driver-v1.schema.json").is_file(),
                        "closed driver contract is required before downstream use")

    def test_closed_decoder_rejects_unsafe_shapes_order_and_empty_named_outputs(self):
        from tools import hosted_replay as replay
        doc = HostedReplayAcceptedTracerTests.prepared.to_dict() if hasattr(HostedReplayAcceptedTracerTests, "prepared") else None
        if doc is None:
            doc = replay.prepare_hosted_checkpoint(runner_temp=Path(tempfile.gettempdir()).resolve(),
                run_tuple=replay.ReplayRunTuple("fixture/replay", 1, 1, "a" * 40), scenario="accepted").to_dict()
        cases = []
        for key in doc:
            missing, null = copy.deepcopy(doc), copy.deepcopy(doc)
            missing.pop(key)
            null[key] = None
            cases.extend([missing, null])
        extra = copy.deepcopy(doc)
        extra["extra"] = "unsafe"
        cases.append(extra)
        for key in ("exports", "source_catalog", "byte_audits"):
            reordered = copy.deepcopy(doc)
            reordered[key].reverse()
            cases.append(reordered)
        empty = copy.deepcopy(doc)
        empty["exports"][0]["row_count"] = 0
        cases.append(empty)
        for candidate in cases:
            with self.assertRaises(replay.ReplayError):
                replay.validate_driver(candidate)
        with self.assertRaises(replay.ReplayError):
            replay.validate_driver('{"schema_version":1,"schema_version":2}')

    def test_neutral_fixture_has_complete_policy_and_empty_committed_excluded_fields(self):
        from tests.fixtures.hosted_replay import hosted_replay_fixture
        fixture = hosted_replay_fixture()
        policy = json.loads(fixture.policy)
        self.assertEqual({row["classification"] for row in policy["classifications"]},
                         {"eligible", "unclassified", "ambiguous_natural_person"})
        for index in range(len(fixture.releases)):
            for raw in fixture.source_payloads(index).values():
                for line in raw.decode("cp1252").splitlines()[1:]:
                    self.assertEqual(line.split(",")[2:4], ["", ""])

    def test_no_default_fixture_or_fabricated_build_provenance_is_used(self):
        from tools import hosted_replay as replay
        source = Path(replay.__file__).read_text()
        self.assertFalse("BuildOutcome(" in source)
        self.assertFalse("eval(" in source)
        self.assertTrue('fixture_store_factory=captured_store' in source)
