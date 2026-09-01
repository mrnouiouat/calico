"""Closed-mode Gate A reconciliation contract and same-DAG fixture proof."""

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from calico_dbt import runner

REPO_ROOT = Path(__file__).resolve().parents[2]
SQL_PATH = REPO_ROOT / "dbt" / "tests" / "assert_gate_a_reconciliation.sql"
ORACLE_PATH = REPO_ROOT.parent / "calico-build" / "GATE-A-EVIDENCE.md"
ORACLE_SHA256 = "82543a3b3b6bc62e42e066d8997e968e2aca440d0c05d6f589905f4d54827133"


class FixtureReconciliationTests(unittest.TestCase):
    def test_fixture_runs_the_complete_dag(self) -> None:
        outcome = runner.build(mode="fixture")
        self.assertEqual(outcome.status, "success", outcome.category)
        self.assertIsNotNone(outcome.proof)

    def test_oracle_is_byte_identical(self) -> None:
        self.assertEqual(hashlib.sha256(ORACLE_PATH.read_bytes()).hexdigest(), ORACLE_SHA256)


class ReconciliationContractTests(unittest.TestCase):
    def test_sql_is_real_gated_and_contains_locked_counts(self) -> None:
        content = SQL_PATH.read_text(encoding="utf-8")
        self.assertIn("var('calico_verified_mode')", content)
        for value in ("557067", "557291", "557211", "247441", "248077", "248215", "5476", "13169", "13071", "7737", "5411", "7750", "13065"):
            self.assertIn(value, content)

    def test_runner_owns_mode_variable(self) -> None:
        content = (REPO_ROOT / "calico_dbt" / "runner.py").read_text(encoding="utf-8")
        self.assertIn('"calico_verified_mode"', content)
        self.assertNotIn("calico_verified_mode=", content)


if __name__ == "__main__":
    unittest.main()
