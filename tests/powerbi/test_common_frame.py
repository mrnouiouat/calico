"""Contracts for the four-page shared native PBIR frame and CI gate."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "powerbi/Calico.Report/definition"
PAGES = [
    ("published_registry_change", "Published registry change"),
    ("cohort_persistence", "Cohort persistence"),
    ("release_quality", "Release quality"),
    ("organization_lookup", "Organization lookup"),
]
CHILDREN = {
    "release_identity", "capture_outcome", "attempt_time", "newer_attempt_warning",
    "source_retirement", "data_as_of_caveat",
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class CommonFrameTests(unittest.TestCase):
    def test_exact_four_visible_pages_in_required_order(self):
        metadata = load(REPORT / "pages/pages.json")
        self.assertEqual(metadata["pageOrder"], [name for name, _ in PAGES])
        page_dirs = {path.parent.name for path in (REPORT / "pages").glob("*/page.json")}
        self.assertEqual(page_dirs, set(metadata["pageOrder"]))
        for name, display in PAGES:
            page = load(REPORT / "pages" / name / "page.json")
            self.assertEqual((page["displayName"], page["width"], page["height"]), (display, 1600, 1440))
            self.assertEqual(page.get("visibility"), "AlwaysVisible")
            self.assertNotIn("pageBinding", page)
            self.assertNotIn("type", page)

    def test_identical_banner_group_and_required_children_on_every_page(self):
        signatures = []
        for name, _ in PAGES:
            visuals = REPORT / "pages" / name / "visuals"
            group = load(visuals / "release_status_banner/visual.json")
            self.assertEqual(group["name"], "release_status_banner")
            self.assertEqual(group["position"], {"x": 24, "y": 80, "z": 100, "height": 208, "width": 1552, "tabOrder": 100})
            self.assertEqual(group["visualGroup"]["groupMode"], "ScaleMode")
            child_payloads = {child: load(visuals / child / "visual.json") for child in CHILDREN}
            self.assertTrue(all(payload["parentGroupName"] == "release_status_banner" for payload in child_payloads.values()))
            signatures.append({key: value for key, value in child_payloads.items()})
        self.assertTrue(all(signature == signatures[0] for signature in signatures[1:]))

    def test_layout_frame_and_keyboard_order_are_explicit(self):
        for name, _ in PAGES:
            visuals = REPORT / "pages" / name / "visuals"
            title = load(visuals / "page_title/visual.json")["position"]
            content = load(visuals / "content_frame/visual.json")["position"]
            footer = load(visuals / "footer_notes/visual.json")["position"]
            self.assertEqual((title["x"], title["y"], title["height"]), (24, 24, 40))
            self.assertEqual(content["y"], 312)
            self.assertEqual((footer["y"], footer["height"]), (1336, 80))
            orders = [load(path)["position"]["tabOrder"] for path in visuals.glob("*/visual.json")]
            self.assertEqual(len(orders), len(set(orders)))

    def test_manual_refresh_and_temporal_caveats_are_always_visible(self):
        required = ["accepted published releases", "not a real-time record", "manual Power BI Refresh now", "not fully automatic"]
        for name, _ in PAGES:
            payload = load(REPORT / "pages" / name / "visuals/data_as_of_caveat/visual.json")
            text = json.dumps(payload)
            for phrase in required:
                self.assertIn(phrase, text)

    def test_theme_uses_approved_tokens(self):
        theme = load(ROOT / "powerbi/report-theme.json")
        self.assertEqual(theme["background"], "#FFFFFF")
        self.assertEqual(theme["foreground"], "#172B4D")
        self.assertEqual(theme["tableAccent"], "#2457A7")
        self.assertEqual(theme["dataColors"], ["#2457A7", "#6B4C9A", "#2F6977", "#5D6675"])
        self.assertTrue(all(item["fontFace"] == "Segoe UI" for item in theme["textClasses"].values()))
        self.assertEqual({item["fontSize"] for item in theme["textClasses"].values()}, {11, 12, 18, 24})

    def test_fixture_ci_generates_checks_and_diffs_real_inventory(self):
        workflow = (ROOT / ".github/workflows/dbt-fixture.yml").read_text(encoding="utf-8")
        expected = [
            "python -m calico_publish generate-inventory --model powerbi/Calico.SemanticModel --output powerbi/semantic-model-inventory-v1.json",
            "python -m calico_publish check-inventory --inventory powerbi/semantic-model-inventory-v1.json",
            "git diff --exit-code -- powerbi/semantic-model-inventory-v1.json",
            "python -m unittest discover -s tests -t . -v",
            "python -m tools.privacy_scan --tree HEAD --history-all",
        ]
        for command in expected:
            self.assertIn(command, workflow)
        self.assertIn("permissions:\n  contents: read", workflow)


if __name__ == "__main__":
    unittest.main()
