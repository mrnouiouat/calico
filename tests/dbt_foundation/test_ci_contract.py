"""Contract tests for the Gate B fixture CI workflow, build-mode docs, the
manifest-anchor-only real catalog/evidence documents, and the committed
real-mode proof (D-02/D-04/D-15, T-03-14/T-03-15).

Never echoes a private path, row, or excluded value -- only membership,
equality, and closed-key-set assertions over safe committed text.

Run:
    py -V:3.13 -m unittest tests.dbt_foundation.test_ci_contract -v
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from calico_dbt.runner import COMMAND_SCHEMA_VERSION, PROOF_SCHEMA_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]

WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "dbt-fixture.yml"
BUILD_MODES_DOC_PATH = REPO_ROOT / "docs" / "build-modes.md"
REAL_CATALOG_PATH = REPO_ROOT / "contracts" / "dbt-input-catalog-v1.json"
EVIDENCE_CATALOG_PATH = REPO_ROOT / "docs" / "evidence" / "gate-b" / "real-input-catalog-v1.json"
REAL_PROOF_PATH = REPO_ROOT / "docs" / "evidence" / "gate-b" / "real-build-proof-v1.json"

CHECKOUT_PIN = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON_PIN = "5fda3b95a4ea91299a34e894583c3862153e4b97"

_CATALOG_TOP_LEVEL_KEYS = frozenset({"contract_version", "releases"})
_CATALOG_RELEASE_KEYS = frozenset(
    {"as_of_date", "release_revision", "revision_fingerprint", "revision_manifest_sha256"}
)
_FORBIDDEN_CATALOG_KEYS = frozenset(
    {
        "canonical_object_sha256",
        "parquet_sha256",
        "object_hash",
        "raw_sha256",
        "byte_count",
        "raw_byte_count",
        "schema",
        "row_count",
        "parquet_row_count",
        "path",
    }
)

_SAFE_PROOF_KEYS = frozenset(
    {
        "proof_schema_version",
        "command_schema_version",
        "mode",
        "status",
        "verified_release_count",
        "verified_object_count",
        "dbt_selected_node_count",
        "dbt_model_count",
        "dbt_test_count",
    }
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_json(path: Path) -> dict:
    return json.loads(_read(path))


class WorkflowContractTests(unittest.TestCase):
    """T-03-14/T-03-15: fixture-only, read-only, SHA-pinned CI."""

    def _workflow(self) -> str:
        return _read(WORKFLOW_PATH)

    def test_workflow_exists(self) -> None:
        self.assertTrue(WORKFLOW_PATH.is_file())

    def test_workflow_permission_is_exactly_contents_read(self) -> None:
        content = self._workflow()
        lines = content.splitlines()
        start = next((i for i, line in enumerate(lines) if line.strip() == "permissions:"), None)
        self.assertIsNotNone(start, "no top-level `permissions:` block found")
        block_lines: list[str] = []
        for line in lines[start + 1 :]:
            if line.strip() == "" or line.startswith((" ", "\t")):
                if line.strip():
                    block_lines.append(line.strip())
                continue
            break
        self.assertEqual(block_lines, ["contents: read"])

    def test_workflow_never_grants_write_permission(self) -> None:
        content = self._workflow()
        self.assertNotIn("write", content.lower())

    def test_workflow_checkout_pinned_to_full_sha_with_version_comment(self) -> None:
        content = self._workflow()
        self.assertIn(f"actions/checkout@{CHECKOUT_PIN}", content)
        self.assertIn("v7.0.1", content)
        self.assertNotRegex(content, r"actions/checkout@v\d")

    def test_workflow_setup_python_pinned_to_full_sha_with_version_comment(self) -> None:
        content = self._workflow()
        self.assertIn(f"actions/setup-python@{SETUP_PYTHON_PIN}", content)
        self.assertIn("v7.0.0", content)
        self.assertNotRegex(content, r"actions/setup-python@v\d")

    def test_workflow_pins_python_3_13_15(self) -> None:
        content = self._workflow()
        self.assertIn("3.13.15", content)

    def test_workflow_installs_only_the_approved_pin_file(self) -> None:
        content = self._workflow()
        self.assertIn("requirements-dbt.txt", content)
        # No unpinned/ad hoc package install beyond the approved pin file.
        for line in content.splitlines():
            if "pip install" in line:
                self.assertIn("requirements-dbt.txt", line)

    def test_workflow_runs_fixture_mode_only_and_never_real_mode(self) -> None:
        content = self._workflow()
        self.assertIn("--mode fixture", content)
        self.assertNotIn("--mode real", content)
        self.assertNotIn("--store", content)

    def test_workflow_never_persists_credentials_or_secrets(self) -> None:
        content = self._workflow()
        self.assertIn("persist-credentials: false", content)
        for token in ("secrets.", "password", "api_key", "token:"):
            self.assertNotIn(token, content.lower())

    def test_workflow_never_ignores_step_failures(self) -> None:
        content = self._workflow()
        self.assertNotIn("continue-on-error", content)

    def test_workflow_never_uploads_an_artifact(self) -> None:
        content = self._workflow()
        self.assertNotIn("upload-artifact", content)

    def test_workflow_never_fetches_an_external_source(self) -> None:
        content = self._workflow()
        for token in ("curl ", "wget ", "requests.get", "urllib"):
            self.assertNotIn(token, content)

    def test_workflow_runs_repository_matrix_and_ci_contract_tests(self) -> None:
        content = self._workflow()
        self.assertIn("tests.test_repository_contract", content)
        self.assertIn("tests.dbt_foundation.test_disposition_matrix", content)
        self.assertIn("tests.dbt_foundation.test_ci_contract", content)

    def test_workflow_runs_the_publication_gate_entry_point(self) -> None:
        content = self._workflow()
        self.assertIn(
            "Prove the publication gate rejects committed violation fixtures",
            content,
        )
        self.assertIn(
            "python -m calico_publish verify --mode fixture "
            "--staging tests/fixtures/publish/valid",
            content,
        )
        self.assertNotIn("from calico_publish.gate import", content)

    def test_workflow_runs_the_committed_gate_violation_tests(self) -> None:
        self.assertIn(
            "python -m unittest tests.publish.test_gate -v",
            self._workflow(),
        )

    def test_publication_gate_step_precedes_the_privacy_gate(self) -> None:
        content = self._workflow()
        self.assertLess(
            content.index("python -m calico_publish verify"),
            content.index("python -m tools.privacy_scan"),
        )

    def test_workflow_runs_the_privacy_scan(self) -> None:
        content = self._workflow()
        self.assertIn("python -m tools.privacy_scan --tree HEAD --history-all", content)


class BuildModesDocumentationTests(unittest.TestCase):
    """D-04: one stable fixture command, one explicit real command, honest
    reproducibility boundary."""

    def _doc(self) -> str:
        return _read(BUILD_MODES_DOC_PATH)

    def test_doc_exists(self) -> None:
        self.assertTrue(BUILD_MODES_DOC_PATH.is_file())

    def test_doc_contains_exactly_one_fixture_command(self) -> None:
        content = self._doc()
        self.assertEqual(content.count("python -m calico_dbt build --mode fixture"), 1)

    def test_doc_contains_exactly_one_real_command(self) -> None:
        content = self._doc()
        self.assertEqual(content.count("python -m calico_dbt build --mode real"), 1)
        self.assertIn("--proof-output", content)

    def test_doc_never_prints_a_concrete_owner_path(self) -> None:
        content = self._doc()
        self.assertNotIn("C:" + "\\", content)
        self.assertNotIn("_data_cache", content)

    def test_doc_states_the_honest_reproducibility_boundary(self) -> None:
        content = self._doc().lower()
        self.assertIn("private", content)
        self.assertIn("cannot rerun real mode", content)


class RealCatalogContractTests(unittest.TestCase):
    """D-02/D-16: manifest-anchor-only trust catalog, never a canonical
    object hash, size, schema, or row count."""

    def test_committed_runner_catalog_exists_and_is_closed(self) -> None:
        document = _load_json(REAL_CATALOG_PATH)
        self.assertEqual(set(document.keys()), _CATALOG_TOP_LEVEL_KEYS)
        self.assertEqual(document["contract_version"], 1)
        self.assertEqual(len(document["releases"]), 3)
        for release in document["releases"]:
            self.assertEqual(set(release.keys()), _CATALOG_RELEASE_KEYS)
            self.assertFalse(set(release.keys()) & _FORBIDDEN_CATALOG_KEYS)

    def test_evidence_catalog_exists_and_is_closed(self) -> None:
        document = _load_json(EVIDENCE_CATALOG_PATH)
        self.assertEqual(set(document.keys()), _CATALOG_TOP_LEVEL_KEYS)
        self.assertEqual(document["contract_version"], 1)
        for release in document["releases"]:
            self.assertEqual(set(release.keys()), _CATALOG_RELEASE_KEYS)
            self.assertFalse(set(release.keys()) & _FORBIDDEN_CATALOG_KEYS)

    def test_evidence_catalog_matches_the_committed_runner_catalog(self) -> None:
        # The evidence document is the Gate B successor/reference to the
        # immutable Gate A correction index (D-022): it names the same
        # approved anchors the runner actually trusts, never a different or
        # weaker set.
        runner_catalog = _load_json(REAL_CATALOG_PATH)
        evidence_catalog = _load_json(EVIDENCE_CATALOG_PATH)
        self.assertEqual(runner_catalog, evidence_catalog)

    def test_gate_a_correction_index_is_untouched(self) -> None:
        gate_a_path = REPO_ROOT / "docs" / "evidence" / "gate-a" / "correction-index-v1.json"
        self.assertTrue(gate_a_path.is_file())
        # This plan never rewrites Gate A evidence (D-022); it only adds a
        # new, additive Gate B document referencing the same three anchors.
        content = _read(gate_a_path)
        self.assertIn("e7d025f771be28d1508cb68ee796c301ffd326037840c95a2c13156ca5fb4096", content)


class RealBuildProofContractTests(unittest.TestCase):
    """D-15: the committed real-mode proof matches Plan 02's exact
    `SafeBuildProof` schema and carries only safe manifest/category/count
    metadata -- never a path, row, excluded value, or raw dbt output."""

    def test_proof_file_exists(self) -> None:
        self.assertTrue(
            REAL_PROOF_PATH.is_file(),
            "real-build-proof-v1.json must be generated by `calico_dbt build "
            "--mode real --store <path> --proof-output`, never hand-authored",
        )

    def test_proof_has_exactly_the_safe_build_proof_keys(self) -> None:
        document = _load_json(REAL_PROOF_PATH)
        self.assertEqual(set(document.keys()), _SAFE_PROOF_KEYS)

    def test_proof_schema_and_command_versions_match_the_runner(self) -> None:
        document = _load_json(REAL_PROOF_PATH)
        self.assertEqual(document["proof_schema_version"], PROOF_SCHEMA_VERSION)
        self.assertEqual(document["command_schema_version"], COMMAND_SCHEMA_VERSION)

    def test_proof_mode_is_real_and_status_is_success(self) -> None:
        document = _load_json(REAL_PROOF_PATH)
        self.assertEqual(document["mode"], "real")
        self.assertEqual(document["status"], "success")

    def test_proof_counts_are_non_negative_integers(self) -> None:
        document = _load_json(REAL_PROOF_PATH)
        for key in (
            "verified_release_count",
            "verified_object_count",
            "dbt_selected_node_count",
            "dbt_model_count",
            "dbt_test_count",
        ):
            value = document[key]
            self.assertIsInstance(value, int)
            self.assertGreaterEqual(value, 0)

    def test_proof_never_contains_a_path_row_or_excluded_value(self) -> None:
        raw = _read(REAL_PROOF_PATH)
        self.assertNotIn("C:" + "\\", raw)
        # Built via concatenation (never written contiguously, including in
        # this comment) so the committed source text itself never contains
        # the absolute-path shape the repository's own privacy scanner
        # flags everywhere (mirrors the fix documented for Plan 02).
        self.assertNotIn("/" + "Users" + "/", raw)
        self.assertNotIn("_data_cache", raw)
        self.assertNotIn("FEIN", raw)
        # No key beyond the closed SafeBuildProof vocabulary ever appears.
        document = json.loads(raw)
        self.assertEqual(set(document.keys()), _SAFE_PROOF_KEYS)


if __name__ == "__main__":
    unittest.main()
