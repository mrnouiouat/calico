from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tests.powerbi.report_contract import ReportContractError, validate_report

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "powerbi/Calico.Report/definition"


class ReportBoundaryTests(unittest.TestCase):
    def validate(self, report=REPORT):
        validate_report(report, ROOT / "contracts/publication-exports-v3.json", ROOT / "contracts/claim-support-v1.json")

    def test_actual_report_uses_only_active_authority(self):
        self.validate()

    def test_unapproved_field_fails_value_free(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "definition"
            import shutil
            shutil.copytree(REPORT, target)
            path = target / "pages/release_quality/visuals/release_health/visual.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            projection = payload["visual"]["query"]["queryState"]["Values"]["projections"][0]
            projection["field"]["Column"]["Property"] = "not_approved"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ReportContractError, "unapproved_field"):
                self.validate(target)

    def test_calculation_and_navigation_objects_fail_closed(self):
        payload = {"Calculation": {"expression": "opaque"}}
        from tests.powerbi.report_contract import _walk
        with self.assertRaisesRegex(ReportContractError, "prohibited_calculation"):
            list(_walk(payload))
        with self.assertRaisesRegex(ReportContractError, "prohibited_navigation"):
            list(_walk({"bookmark": {"state": "opaque"}}))

    def test_approved_limitation_copy_is_not_rejected(self):
        self.assertIn("does not establish", (REPORT / "pages/cohort_persistence/visuals/status_age/visual.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
