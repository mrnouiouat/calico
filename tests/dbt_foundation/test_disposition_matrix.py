"""Contract tests for the closed D1-D8 defect disposition matrix (D-12/D-13/D-14).

Covers: closed Draft 2020-12 shape (exact top-level keys, exactly eight
unique ordered IDs D1 through D8, closed per-entry/evidence/obligation key
sets), evidence-path existence and test-identifier discoverability, the
explicit absence of any IRS/private-foundation/revocation source, model, or
derived flag anywhere in the dbt project or `calico_dbt` runner code (D2/D5),
and the exact, immutable text/owner/status of the three still-pending
downstream obligations owned by later phases (D1 -> Phase 5, D3 -> Phase 4,
D4 -> Phase 8).

Never echoes a raw registry row or excluded value -- only membership,
equality, and existence assertions over the matrix's own safe content.

Run:
    py -V:3.13 -m unittest tests.dbt_foundation.test_disposition_matrix -v
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = REPO_ROOT / "docs" / "defects" / "d1-d8-dispositions-v1.json"
SCHEMA_PATH = REPO_ROOT / "contracts" / "d1-d8-dispositions-v1.schema.json"

_EXPECTED_IDS = ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8")

_TOP_LEVEL_KEYS = frozenset({"contract_version", "defects"})
_DEFECT_KEYS = frozenset(
    {
        "id",
        "name",
        "v1_disposition",
        "phase_3_evidence_status",
        "owner",
        "evidence",
        "note",
        "downstream_obligations",
    }
)
_OWNER_KEYS = frozenset({"phase", "tier"})
_EVIDENCE_KEYS = frozenset({"type", "path", "identifier"})
_OBLIGATION_KEYS = frozenset({"owning_phase", "requirement", "status"})

_EVIDENCE_TYPES = frozenset(
    {"python_test", "dbt_singular_test", "dbt_schema_test", "contract", "document"}
)

#: The three exact, immutable pending downstream obligations (D-13). Any
#: omission, reassignment to a different owning phase, silent completion, or
#: replacement with weaker Phase-3-only text must fail this test file.
_EXPECTED_PENDING_OBLIGATIONS = {
    "D1": {
        "owning_phase": "Phase 5",
        "requirement": (
            "Compute the three locked Last Renewal transition-diagnostic "
            "measures against observed exits, and prove that clearing this "
            "field is never treated as the event definition."
        ),
        "status": "pending",
    },
    "D3": {
        "owning_phase": "Phase 4",
        "requirement": (
            "Derive transition onset and exit only as release-interval "
            "bounds, and never substitute the source-reported current-status "
            "date as an exact transition onset."
        ),
        "status": "pending",
    },
    "D4": {
        "owning_phase": "Phase 8",
        "requirement": (
            "Require name narrowing followed by explicit selection of a "
            "displayed exact organization/full-registration-number "
            "candidate, including duplicate-name disambiguation, before any "
            "history is shown; never a fuzzy substring web join."
        ),
        "status": "pending",
    },
}

#: D6-D8 must each link the Phase 3 Parquet-only boundary test in addition
#: to their owned Phase 2 evidence (D-14).
_PARQUET_BOUNDARY_TEST_PATH = "dbt/tests/assert_parquet_only_boundary.sql"

#: Directories scanned for D2/D5 exclusion absence -- the dbt project and the
#: Python input-boundary/runner package are the only Phase 3 analytical
#: paths that could ever introduce an IRS/private-foundation model or field.
_ANALYTICAL_SCAN_ROOTS = ("dbt", "calico_dbt")
_ANALYTICAL_EXCLUDED_DIRS = frozenset({"target", "dbt_packages", "logs", "__pycache__"})
_ANALYTICAL_SCAN_SUFFIXES = (".sql", ".py", ".yml", ".yaml")

_FORBIDDEN_TERM_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\birs\b",
        r"private[\s_-]foundation",
        r"\brevocation\b",
        r"990[\s_-]?pf",
    )
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _analytical_files() -> list[Path]:
    files: list[Path] = []
    for root_name in _ANALYTICAL_SCAN_ROOTS:
        root = REPO_ROOT / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if set(path.parts) & _ANALYTICAL_EXCLUDED_DIRS:
                continue
            if path.suffix not in _ANALYTICAL_SCAN_SUFFIXES:
                continue
            files.append(path)
    return files


class ClosedShapeTests(unittest.TestCase):
    """D-12: closed Draft 2020-12 shape, exactly eight unique ordered IDs."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = _load_json(SCHEMA_PATH)
        cls.document = _load_json(MATRIX_PATH)

    def test_schema_file_declares_draft_2020_12(self) -> None:
        self.assertEqual(self.schema.get("$schema"), "https://json-schema.org/draft/2020-12/schema")

    def test_schema_is_closed_at_every_level(self) -> None:
        self.assertTrue(self.schema.get("additionalProperties") is False)
        defect_def = self.schema["$defs"]["defect"]
        self.assertTrue(defect_def.get("additionalProperties") is False)
        self.assertEqual(set(defect_def["required"]), _DEFECT_KEYS)
        self.assertTrue(self.schema["$defs"]["owner"].get("additionalProperties") is False)
        self.assertTrue(self.schema["$defs"]["evidence_entry"].get("additionalProperties") is False)
        self.assertTrue(self.schema["$defs"]["downstream_obligation"].get("additionalProperties") is False)

    def test_document_has_exactly_the_closed_top_level_keys(self) -> None:
        self.assertEqual(set(self.document.keys()), _TOP_LEVEL_KEYS)
        self.assertEqual(self.document["contract_version"], 1)

    def test_document_contains_exactly_eight_unique_ids_in_order(self) -> None:
        defects = self.document["defects"]
        self.assertEqual(len(defects), 8)
        ids = [defect["id"] for defect in defects]
        self.assertEqual(ids, list(_EXPECTED_IDS))
        self.assertEqual(len(set(ids)), 8, "duplicate defect ID present")

    def test_every_defect_has_exactly_the_closed_key_set(self) -> None:
        for defect in self.document["defects"]:
            with self.subTest(id=defect.get("id")):
                self.assertEqual(set(defect.keys()), _DEFECT_KEYS)
                self.assertEqual(set(defect["owner"].keys()), _OWNER_KEYS)
                for evidence in defect["evidence"]:
                    self.assertEqual(set(evidence.keys()), _EVIDENCE_KEYS)
                    self.assertIn(evidence["type"], _EVIDENCE_TYPES)
                for obligation in defect["downstream_obligations"]:
                    self.assertEqual(set(obligation.keys()), _OBLIGATION_KEYS)

    def test_no_defect_has_a_blank_disposition_or_note(self) -> None:
        for defect in self.document["defects"]:
            with self.subTest(id=defect["id"]):
                self.assertTrue(defect["v1_disposition"].strip())
                self.assertTrue(defect["note"].strip())


class EvidenceExistenceTests(unittest.TestCase):
    """Every owned evidence path resolves and every test identifier is
    discoverable (D-12 acceptance criterion, T-03-13)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.document = _load_json(MATRIX_PATH)

    def _iter_evidence(self):
        for defect in self.document["defects"]:
            for evidence in defect["evidence"]:
                yield defect["id"], evidence

    def test_every_evidence_path_exists(self) -> None:
        for defect_id, evidence in self._iter_evidence():
            with self.subTest(id=defect_id, path=evidence["path"]):
                self.assertTrue(
                    (REPO_ROOT / evidence["path"]).exists(),
                    f"{defect_id} evidence path does not exist: {evidence['path']}",
                )

    def test_every_python_test_identifier_is_discoverable(self) -> None:
        loader = unittest.defaultTestLoader
        for defect_id, evidence in self._iter_evidence():
            if evidence["type"] != "python_test":
                continue
            with self.subTest(id=defect_id, identifier=evidence["identifier"]):
                self.assertIsNotNone(evidence["identifier"])
                suite = loader.loadTestsFromName(evidence["identifier"])
                self.assertGreater(suite.countTestCases(), 0)

    def test_every_document_evidence_identifier_appears_in_its_file(self) -> None:
        for defect_id, evidence in self._iter_evidence():
            if evidence["type"] != "document" or evidence["identifier"] is None:
                continue
            with self.subTest(id=defect_id, identifier=evidence["identifier"]):
                content = (REPO_ROOT / evidence["path"]).read_text(encoding="utf-8")
                column_name = evidence["identifier"].rsplit(".", 1)[-1]
                self.assertIn(column_name, content)

    def test_d6_d7_d8_link_the_parquet_only_boundary_test(self) -> None:
        for defect in self.document["defects"]:
            if defect["id"] not in ("D6", "D7", "D8"):
                continue
            with self.subTest(id=defect["id"]):
                paths = {evidence["path"] for evidence in defect["evidence"]}
                self.assertIn(_PARQUET_BOUNDARY_TEST_PATH, paths)

    def test_d6_d7_d8_remain_owned_by_phase_2(self) -> None:
        for defect in self.document["defects"]:
            if defect["id"] not in ("D6", "D7", "D8"):
                continue
            with self.subTest(id=defect["id"]):
                self.assertEqual(defect["phase_3_evidence_status"], "linked_to_phase_2")
                self.assertEqual(defect["owner"]["phase"], "Phase 2")
                self.assertEqual(defect["downstream_obligations"], [])


class ExclusionAbsenceTests(unittest.TestCase):
    """D2/D5: no IRS/private-foundation/revocation source, model, or derived
    flag exists anywhere in a Phase 3 analytical path."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.document = _load_json(MATRIX_PATH)

    def test_d2_and_d5_are_explicit_exclusions_with_no_obligation(self) -> None:
        by_id = {defect["id"]: defect for defect in self.document["defects"]}
        for defect_id in ("D2", "D5"):
            with self.subTest(id=defect_id):
                self.assertEqual(by_id[defect_id]["phase_3_evidence_status"], "explicit_exclusion")
                self.assertEqual(by_id[defect_id]["downstream_obligations"], [])

    def test_no_irs_or_private_foundation_terms_in_analytical_paths(self) -> None:
        for path in _analytical_files():
            content = path.read_text(encoding="utf-8", errors="replace")
            for pattern in _FORBIDDEN_TERM_PATTERNS:
                self.assertIsNone(
                    pattern.search(content),
                    f"prohibited D2/D5 term {pattern.pattern!r} found in "
                    f"{path.relative_to(REPO_ROOT)}",
                )


class PendingObligationImmutabilityTests(unittest.TestCase):
    """D-13: D1/D3/D4 carry exactly the fixed pending Phase 5/4/8
    obligation; the schema/tests reject omission, reassignment to a
    different owning phase, silent completion, or replacement by weaker
    Phase-3-only evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.by_id = {defect["id"]: defect for defect in _load_json(MATRIX_PATH)["defects"]}

    def test_d1_d3_d4_each_carry_exactly_one_pending_obligation(self) -> None:
        for defect_id in ("D1", "D3", "D4"):
            with self.subTest(id=defect_id):
                obligations = self.by_id[defect_id]["downstream_obligations"]
                self.assertEqual(len(obligations), 1, f"{defect_id} must carry exactly one obligation")

    def test_pending_obligation_text_owner_and_status_are_exact(self) -> None:
        for defect_id, expected in _EXPECTED_PENDING_OBLIGATIONS.items():
            with self.subTest(id=defect_id):
                actual = self.by_id[defect_id]["downstream_obligations"][0]
                self.assertEqual(actual["owning_phase"], expected["owning_phase"])
                self.assertEqual(actual["requirement"], expected["requirement"])
                self.assertEqual(actual["status"], expected["status"])

    def test_d1_d3_d4_are_not_marked_fully_complete(self) -> None:
        # Phase 3's own staging/identity portion is implemented and tested,
        # but the overall evidence status must never claim the defect's
        # later-phase obligation is also satisfied.
        for defect_id in ("D1", "D3", "D4"):
            with self.subTest(id=defect_id):
                self.assertEqual(self.by_id[defect_id]["phase_3_evidence_status"], "implemented_and_tested")
                self.assertNotEqual(self.by_id[defect_id]["downstream_obligations"][0]["status"], "complete")


if __name__ == "__main__":
    unittest.main()
