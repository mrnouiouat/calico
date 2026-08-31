"""Integration tests for the closed, fixture-only `dbt docs generate` proof
(D-20, T-04-06B).

`RealProjectDocsProofTests` drives `calico_dbt.runner.docs()` against the
actual product `../calico/dbt` project (no `_dbt_project_dir_override`),
mirroring `tests.dbt_longitudinal.test_transitions`'s own precedent of
proving real-DAG behavior through the runner's safe, non-echo outcome --
this is the only place the docs proof is exercised over the complete, real
23-model Phase 4 DAG.

`SyntheticProjectDocsPlumbingTests` reuses the exact disposable ephemeral
dbt project `tests.dbt_foundation.test_runner` already builds for
`runner.build()`'s own subprocess tests, bound through the identical
`_dbt_project_dir_override` test-only seam `runner.docs()` also accepts --
this module never needs a second stub project definition, and this class is
reserved for Python-plumbing-only concerns (argument handling,
cleanup-on-failure) that do not require the real DAG.

Every assertion reads only the closed `SafeDocsProof` JSON or checks
filesystem absence; it never inspects raw dbt stdout or a generated
artifact's contents (D-15).

Run:
    py -V:3.13 -m unittest tests.dbt_longitudinal.test_docs -v
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from calico_dbt import runner
from tests.dbt_foundation.test_runner import _build_ephemeral_dbt_project

_SAFE_DOCS_PROOF_KEYS = frozenset(
    {
        "proof_schema_version",
        "command_schema_version",
        "mode",
        "status",
        "dbt_selected_node_count",
        "dbt_model_count",
        "dbt_test_count",
        "docs_node_count",
        "docs_artifact_count",
    }
)


def _spy_on_mkdtemp():
    """Return (context_manager_factory, captured_list) so a test can learn
    the exact temp root `docs()` created without adding a production seam.
    """

    real_mkdtemp = tempfile.mkdtemp
    captured: list[str] = []

    def spy(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        captured.append(path)
        return path

    return patch("calico_dbt.runner.tempfile.mkdtemp", side_effect=spy), captured


class RealProjectDocsProofTests(unittest.TestCase):
    """Drives the real product DAG. Exercised end-to-end again by this
    plan's own `<verify>` CLI invocation; these assertions add the
    machine-checked safe-proof-shape and cleanup guarantees."""

    def test_docs_succeeds_over_the_real_phase_4_dag(self) -> None:
        outcome = runner.docs()
        self.assertEqual(outcome.status, "success", outcome.category)
        proof = outcome.proof
        self.assertIsNotNone(proof)
        self.assertEqual(proof.mode, "fixture")
        self.assertEqual(proof.status, "success")
        # Exactly the complete closed Phase 3 + Phase 4 model set (D-01/D-22):
        # every dbt/models/**/*.sql file becomes exactly one "model." node.
        self.assertEqual(proof.dbt_model_count, 23)
        self.assertGreaterEqual(proof.dbt_test_count, 18)
        self.assertGreater(proof.dbt_selected_node_count, 0)
        self.assertGreater(proof.docs_node_count, 0)
        self.assertGreater(proof.docs_artifact_count, 0)

    def test_docs_proof_over_the_real_dag_is_closed_and_value_free(self) -> None:
        outcome = runner.docs()
        self.assertEqual(outcome.status, "success", outcome.category)
        document = json.loads(outcome.proof.to_json())
        self.assertEqual(set(document), _SAFE_DOCS_PROOF_KEYS)

    def test_docs_leaves_no_generated_artifact_on_disk_after_success(self) -> None:
        patcher, captured = _spy_on_mkdtemp()
        with patcher:
            outcome = runner.docs()

        self.assertEqual(outcome.status, "success", outcome.category)
        self.assertEqual(len(captured), 1)
        self.assertFalse(Path(captured[0]).exists())


class SyntheticProjectDocsPlumbingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="calico-docs-plumbing-test-")
        self.addCleanup(self._tmp.cleanup)
        self.project_dir = _build_ephemeral_dbt_project(Path(self._tmp.name))

    def _broken_project(self, name: str) -> Path:
        broken_project = Path(self._tmp.name) / name
        shutil.copytree(self.project_dir, broken_project)
        (broken_project / "models" / "int_promoted_releases.sql").write_text(
            "select * from {{ source('runtime_input', 'does_not_exist') }}", encoding="utf-8"
        )
        return broken_project

    def test_docs_succeeds_against_the_synthetic_project_too(self) -> None:
        outcome = runner.docs(_dbt_project_dir_override=self.project_dir)
        self.assertEqual(outcome.status, "success", outcome.category)

    def test_temp_root_is_removed_after_a_failed_build(self) -> None:
        broken_project = self._broken_project("broken-dbt-build")
        patcher, captured = _spy_on_mkdtemp()
        with patcher:
            outcome = runner.docs(_dbt_project_dir_override=broken_project)

        self.assertEqual(outcome.status, "failed")
        self.assertEqual(len(captured), 1)
        self.assertFalse(Path(captured[0]).exists())

    def test_docs_failure_category_never_carries_a_path_or_traceback(self) -> None:
        broken_project = self._broken_project("broken-dbt-nonecho")
        outcome = runner.docs(_dbt_project_dir_override=broken_project)
        self.assertEqual(outcome.status, "failed")
        for forbidden in (":\\", "/" + "Users" + "/", "Traceback", "duckdb.IOException"):
            self.assertNotIn(forbidden, outcome.category or "")


class CliDocsContractTests(unittest.TestCase):
    """The CLI's `docs` subcommand is closed fixture-only: `--mode` accepts
    exactly `fixture`, never `real` (T-04-06F)."""

    @staticmethod
    def _parser():
        from calico_dbt.__main__ import _build_parser

        return _build_parser()

    def test_cli_docs_rejects_real_mode_at_parse_time(self) -> None:
        with self.assertRaises(SystemExit):
            self._parser().parse_args(["docs", "--mode", "real"])

    def test_cli_docs_accepts_fixture_mode(self) -> None:
        args = self._parser().parse_args(["docs", "--mode", "fixture"])
        self.assertEqual(args.command, "docs")
        self.assertEqual(args.mode, "fixture")

    def test_cli_docs_defaults_to_fixture_mode(self) -> None:
        args = self._parser().parse_args(["docs"])
        self.assertEqual(args.command, "docs")
        self.assertEqual(args.mode, "fixture")


if __name__ == "__main__":
    unittest.main()
