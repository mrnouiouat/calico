"""Behavior contracts for isolated actual-SQL replay evidence."""

import importlib.util
import unittest


class HostedReplayAcceptedTracerTests(unittest.TestCase):
    def test_staged_replay_authority_exists(self):
        self.assertIsNotNone(importlib.util.find_spec("tools.hosted_replay"),
                             "staged capture and publication authority is required")


class HostedReplayEvidenceContractTests(unittest.TestCase):
    def test_driver_contract_exists(self):
        from pathlib import Path
        self.assertTrue((Path(__file__).resolve().parents[1] / "contracts" /
                         "hosted-replay-driver-v1.schema.json").is_file(),
                        "closed driver contract is required before downstream use")
