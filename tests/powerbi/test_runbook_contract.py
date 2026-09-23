import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs/powerbi-refresh-runbook.md"


class PowerBiRefreshRunbookContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = RUNBOOK.read_text(encoding="utf-8")
        cls.normalized = re.sub(r"\s+", " ", cls.text)

    def assertContainsAll(self, *phrases):
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.normalized)

    def test_records_the_proven_manual_branch_without_inventing_schedule_success(self):
        self.assertContainsAll(
            "Report updates use a documented manual Power BI Refresh now step. The report is not fully automatic.",
            "08-TRACER-EVIDENCE.md",
            "08-04-SUMMARY.md",
            "Refresh now",
            "15:30 America/Los_Angeles",
            "must be recorded from the native Service refresh history",
        )

    def test_keeps_deployment_inside_the_inspected_boundary(self):
        self.assertContainsAll(
            "committed PBIP",
            "generate-inventory",
            "check-inventory",
            "Anonymous",
            "Public",
            "Do not edit the semantic model in the Service",
            "Do not create a Publish to web embed",
            "mixed branch reads are not an atomic Service snapshot",
        )

    def test_lists_every_adversarial_exact_key_case(self):
        self.assertContainsAll(
            "Zero exact keys",
            "Unique name only",
            "Duplicate names",
            "One directly selected exact key",
            "Multiple exact keys",
            "Candidate-row context",
            "Propagated relationship context",
            "Changed name search",
            "Initial saved state",
            "leading zero",
            "suffix",
            "long organization name",
            "missing from the latest release",
        )

    def test_states_exposure_history_and_correction_boundaries(self):
        self.assertContainsAll(
            "complete approved named export remains in the inspected semantic model",
            "not access control",
            "historical observations indefinitely",
            "approximately 15 days",
            "current official record",
            "correction channel",
            "derivation rule is not recorded",
            "Phase 10",
            "founder approval",
        )

    def test_forbids_sensitive_evidence_and_unproven_results(self):
        self.assertContainsAll(
            "synthetic identities",
            "account or tenant identifiers",
            "credentials",
            "private store paths",
            "Do not record a pass until it is observed",
            "approximately 509 MiB",
        )


if __name__ == "__main__":
    unittest.main()
