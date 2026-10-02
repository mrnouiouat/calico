"""Closed-mode Gate A reconciliation contract and same-DAG fixture proof."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from calico_dbt import runner

REPO_ROOT = Path(__file__).resolve().parents[2]
SQL_PATH = REPO_ROOT / "dbt" / "tests" / "assert_gate_a_reconciliation.sql"
ORACLE_PATH = REPO_ROOT.parent / "calico-build" / "GATE-A-EVIDENCE.md"
ORACLE_SHA256 = "82543a3b3b6bc62e42e066d8997e968e2aca440d0c05d6f589905f4d54827133"

#: The Gate A oracle lives in the private `calico-build` workspace beside this
#: repository, so it is absent from any public checkout -- CI included. Tests
#: bound to it skip there rather than erroring, which keeps a genuine failure
#: visible instead of buried in noise. They still run wherever the workspace
#: is present, which is where the binding actually has to hold.
_ORACLE_PRESENT = ORACLE_PATH.is_file()
_ORACLE_REASON = "Gate A oracle is private to the calico-build workspace and absent here"


ORACLE_IDENTITY_PATH = REPO_ROOT / "docs/evidence/gate-b/oracle-byte-identity.json"
CANONICAL_ORACLE_SHA256 = "3c7943ad82184cd3e54ab0fd844c2b3ec2732fc63eb05395bd53d9662890cf62"
CANONICAL_ORACLE_BLOB = "6c2035576d5f4c88615954d609bb6675e3c41a45"


def _unique_oracle_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("oracle.invalid_identity")
        result[key] = value
    return result


def _load_oracle_identity(path=ORACLE_IDENTITY_PATH):
    try:
        document = json.loads(path.read_bytes(), object_pairs_hook=_unique_oracle_keys)
        if (not isinstance(document, dict) or set(document) != {"schema_version", "canonical", "supersedes"}
                or type(document["schema_version"]) is not int or document["schema_version"] != 1):
            raise ValueError("oracle.invalid_identity")
        canonical, predecessor = document["canonical"], document["supersedes"]
        if (not isinstance(canonical, dict) or set(canonical) != {"byte_format", "sha256", "git_blob"}
                or not isinstance(predecessor, dict) or set(predecessor) != {"byte_format", "sha256"}):
            raise ValueError("oracle.invalid_identity")
        for record in (canonical, predecessor):
            if not isinstance(record["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
                raise ValueError("oracle.invalid_identity")
        if (canonical["byte_format"] != "LF" or predecessor["byte_format"] != "CRLF"
                or canonical["sha256"] != CANONICAL_ORACLE_SHA256
                or predecessor["sha256"] != ORACLE_SHA256
                or canonical["sha256"] == predecessor["sha256"]
                or canonical["git_blob"] != CANONICAL_ORACLE_BLOB):
            raise ValueError("oracle.invalid_identity")
        return document
    except (OSError, ValueError, TypeError, KeyError):
        raise ValueError("oracle.invalid_identity") from None


def _verify_oracle_identity(path=ORACLE_PATH):
    document = _load_oracle_identity()
    if hashlib.sha256(path.read_bytes()).hexdigest() != document["canonical"]["sha256"]:
        raise ValueError("oracle.byte_identity_mismatch")
    try:
        blob = subprocess.check_output(
            ["git", "rev-parse", "HEAD:GATE-A-EVIDENCE.md"], cwd=path.parent,
            text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        raise ValueError("oracle.git_identity_unavailable") from None
    if blob != document["canonical"]["git_blob"]:
        raise ValueError("oracle.git_identity_mismatch")


class FixtureReconciliationTests(unittest.TestCase):
    def test_fixture_runs_the_complete_dag(self) -> None:
        outcome = runner.build(mode="fixture")
        self.assertEqual(outcome.status, "success", outcome.category)
        self.assertIsNotNone(outcome.proof)

    @unittest.skipUnless(_ORACLE_PRESENT, _ORACLE_REASON)
    def test_oracle_is_byte_identical(self) -> None:
        _verify_oracle_identity()


class ReconciliationContractTests(unittest.TestCase):
    def test_oracle_successor_preserves_both_exact_byte_identities(self):
        document = _load_oracle_identity()
        self.assertEqual(document["canonical"]["sha256"], CANONICAL_ORACLE_SHA256)
        self.assertEqual(document["supersedes"]["sha256"], ORACLE_SHA256)
        self.assertEqual(document["canonical"]["git_blob"], CANONICAL_ORACLE_BLOB)

    def test_oracle_successor_rejects_unknown_keys_types_hashes_and_newline_labels(self):
        original = _load_oracle_identity()
        mutations = (
            ((), "extra", True), ((), "schema_version", True), ((), "schema_version", 2),
            (("canonical",), "extra", True), (("supersedes",), "extra", True),
            (("canonical",), "sha256", "z" * 64), (("supersedes",), "sha256", "a" * 64),
            (("canonical",), "sha256", ORACLE_SHA256),
            (("supersedes",), "sha256", CANONICAL_ORACLE_SHA256),
            (("canonical",), "byte_format", "CRLF"), (("supersedes",), "byte_format", "LF"),
            (("canonical",), "git_blob", "a" * 40),
        )
        with tempfile.TemporaryDirectory(prefix="calico-oracle-schema-") as temp:
            path = Path(temp) / "identity.json"
            for traversal, key, value in mutations:
                document = copy.deepcopy(original)
                target = document
                for component in traversal:
                    target = target[component]
                target[key] = value
                path.write_text(json.dumps(document), encoding="utf-8")
                with self.subTest(field=key), self.assertRaisesRegex(ValueError, "^oracle.invalid_identity$"):
                    _load_oracle_identity(path)
            path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "^oracle.invalid_identity$"):
                _load_oracle_identity(path)

    def test_changed_or_converted_oracle_bytes_fail_without_normalization(self):
        with tempfile.TemporaryDirectory(prefix="calico-oracle-bytes-") as temp:
            path = Path(temp) / "oracle.md"
            for raw in (b"changed\n", b"changed\r\n"):
                path.write_bytes(raw)
                with self.assertRaisesRegex(ValueError, "^oracle.byte_identity_mismatch$"):
                    _verify_oracle_identity(path)

    @unittest.skipUnless(_ORACLE_PRESENT, _ORACLE_REASON)
    def test_changed_tracked_oracle_blob_fails_independently_of_file_bytes(self):
        with patch(__name__ + ".subprocess.check_output", return_value="a" * 40):
            with self.assertRaisesRegex(ValueError, "^oracle.git_identity_mismatch$"):
                _verify_oracle_identity()

    def test_sql_is_real_gated_and_contains_locked_counts(self) -> None:
        content = SQL_PATH.read_text(encoding="utf-8")
        self.assertIn("var('calico_verified_mode')", content)
        for value in ("557067", "557291", "557211", "247441", "248077", "248215", "5476", "13169", "13071", "7737", "5411", "7750", "13065"):
            self.assertIn(value, content)

    def test_runner_owns_mode_variable(self) -> None:
        content = (REPO_ROOT / "calico_dbt" / "runner.py").read_text(encoding="utf-8")
        self.assertIn('"calico_verified_mode"', content)
        self.assertNotIn("calico_verified_mode=", content)


@unittest.skipUnless(_ORACLE_PRESENT, _ORACLE_REASON)
class RealProofProvenanceTests(unittest.TestCase):
    """Task 2: the v3 Gate B exit proof writer and its closed verifier
    (D-11..D-15, D-22, T-05-05A/B/C).

    Exercises `runner._write_proof_output_v3` and `runner.verify_proof`
    directly against a synthetic `SafeBuildProof` and a synthetic
    `run_results.json` -- neither needs a real store or a real dbt build to
    prove this module's own writer/verifier logic correct, so this class
    passes identically before and after the owner-supplied real build Task
    2's own `<verify>` block runs it around.

    Every test saves and restores whatever `real-build-proof-v3.json`
    state already exists before it runs (mirroring
    `tests.dbt_foundation.test_runner`'s v2 precedent) so a real proof
    already on disk is left byte-identical once the whole class finishes.
    """

    V3_PATH = REPO_ROOT / "docs" / "evidence" / "gate-b" / "real-build-proof-v3.json"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="calico-v3-proof-test-")
        self.addCleanup(self._tmp.cleanup)
        self.target_path = Path(self._tmp.name) / "target"
        self.target_path.mkdir(parents=True)
        self._write_run_results(self._passing_results())

        v3_existed_before = self.V3_PATH.exists()
        v3_bytes_before = self.V3_PATH.read_bytes() if v3_existed_before else None
        self.addCleanup(self._restore_v3, v3_existed_before, v3_bytes_before)

    def _restore_v3(self, existed_before: bool, bytes_before: bytes | None) -> None:
        if existed_before:
            self.V3_PATH.write_bytes(bytes_before)
        else:
            self.V3_PATH.unlink(missing_ok=True)

    @staticmethod
    def _passing_results() -> dict:
        return {
            "results": [
                {
                    "unique_id": "test.calico_registry.assert_gate_a_reconciliation.ab12cd34",
                    "status": "pass",
                    "failures": 0,
                },
                {
                    "unique_id": "model.calico_registry.mart_last_renewal_diagnostic",
                    "status": "success",
                },
                {
                    "unique_id": "test.calico_registry.assert_last_renewal_diagnostic.ef567890",
                    "status": "pass",
                    "failures": 0,
                },
                {
                    "unique_id": "model.calico_registry.mart_claim_support",
                    "status": "success",
                },
                {
                    "unique_id": "test.calico_registry.assert_claim_support.12ab34cd",
                    "status": "pass",
                    "failures": 0,
                },
            ]
        }

    def _write_run_results(self, document: dict) -> None:
        (self.target_path / "run_results.json").write_text(json.dumps(document), encoding="utf-8")

    @staticmethod
    def _sample_proof() -> "runner.SafeBuildProof":
        return runner.SafeBuildProof(
            proof_schema_version=runner.PROOF_SCHEMA_VERSION,
            command_schema_version=runner.COMMAND_SCHEMA_VERSION,
            mode="real",
            status="success",
            verified_release_count=3,
            verified_object_count=12,
            dbt_selected_node_count=171,
            dbt_model_count=23,
            dbt_test_count=140,
        )

    def _write_sample_v3(self, store_fingerprint: str = "f" * 64) -> dict:
        runner._write_proof_output_v3(  # noqa: SLF001 -- exercising the exact seam build() calls
            self._sample_proof(), target_path=self.target_path, store_fingerprint=store_fingerprint
        )
        return json.loads(self.V3_PATH.read_text(encoding="utf-8"))

    # -- writer -------------------------------------------------------------

    def test_writer_produces_a_closed_v3_document_superseding_v2(self) -> None:
        document = self._write_sample_v3(store_fingerprint="a" * 64)
        self.assertEqual(document["proof_schema_version"], 3)
        self.assertEqual(document["mode"], "real")
        self.assertEqual(document["status"], "success")
        self.assertIs(document["verified_input_binding"], True)
        self.assertEqual(document["store_fingerprint_sha256"], "a" * 64)
        self.assertEqual(len(document["run_id"]), 32)
        self.assertEqual(document["reconciliation"]["status"], "reconciled")
        self.assertEqual(document["reconciliation"]["mismatch_row_count"], 0)
        self.assertEqual(
            sorted(document["diagnostics"]["measures"]),
            sorted(
                ["conditional_precision", "eligible_exit_sensitivity", "all_exit_sensitivity"]
            ),
        )
        self.assertEqual(document["claim_support"]["relation"], "mart_claim_support")
        self.assertEqual(
            document["claim_support"]["relation_version"], "claim_support_status_movement_v1"
        )
        self.assertEqual(
            document["supersedes"]["path"], "docs/evidence/gate-b/real-build-proof-v2.json"
        )
        self.assertEqual(set(document["hashes"]), {
            "oracle_sha256",
            "predecessor_v2_sha256",
            "metric_denominators_contract_sha256",
            "last_renewal_diagnostic_contract_sha256",
            "claim_support_contract_sha256",
            "reconciliation_sql_sha256",
            "generated_proof_payload_sha256",
        })

    def test_writer_never_touches_v1_or_v2(self) -> None:
        v1_path = REPO_ROOT / "docs" / "evidence" / "gate-b" / "real-build-proof-v1.json"
        v2_path = REPO_ROOT / "docs" / "evidence" / "gate-b" / "real-build-proof-v2.json"
        v1_before = v1_path.read_bytes()
        v2_before = v2_path.read_bytes()
        self._write_sample_v3(store_fingerprint="b" * 64)
        self.assertEqual(v1_path.read_bytes(), v1_before)
        self.assertEqual(v2_path.read_bytes(), v2_before)

    def test_writer_fails_closed_when_reconciliation_did_not_pass(self) -> None:
        self._write_run_results(
            {
                "results": [
                    {
                        "unique_id": "test.calico_registry.assert_gate_a_reconciliation.ab12cd34",
                        "status": "fail",
                        "failures": 2,
                    },
                ]
            }
        )
        with self.assertRaises(runner.RunnerError) as ctx:
            runner._write_proof_output_v3(  # noqa: SLF001
                self._sample_proof(), target_path=self.target_path, store_fingerprint="c" * 64
            )
        self.assertEqual(ctx.exception.category, "runner.reconciliation_status_unavailable")

    def test_writer_fails_closed_when_diagnostic_test_did_not_pass(self) -> None:
        results = self._passing_results()
        results["results"][2]["status"] = "fail"
        self._write_run_results(results)
        with self.assertRaises(runner.RunnerError) as ctx:
            runner._write_proof_output_v3(  # noqa: SLF001
                self._sample_proof(), target_path=self.target_path, store_fingerprint="d" * 64
            )
        self.assertEqual(ctx.exception.category, "runner.diagnostic_status_unavailable")

    def test_writer_fails_closed_when_claim_support_model_did_not_build(self) -> None:
        results = self._passing_results()
        results["results"][3]["status"] = "error"
        self._write_run_results(results)
        with self.assertRaises(runner.RunnerError) as ctx:
            runner._write_proof_output_v3(  # noqa: SLF001
                self._sample_proof(), target_path=self.target_path, store_fingerprint="e" * 64
            )
        self.assertEqual(ctx.exception.category, "runner.claim_support_status_unavailable")

    # -- verifier -------------------------------------------------------------

    def test_verify_proof_accepts_a_freshly_written_document(self) -> None:
        self._write_sample_v3(store_fingerprint="0" * 64)
        outcome = runner.verify_proof(
            proof_path=self.V3_PATH,
            require_mode="real",
            require_current_run=True,
            require_verified_binding=True,
            require_exact_reconciliation=True,
            require_diagnostics=True,
            require_claim_support=True,
            verify_hashes=True,
        )
        self.assertEqual(outcome.status, "verified", outcome.category)

    def test_verify_proof_rejects_absent_file(self) -> None:
        outcome = runner.verify_proof(proof_path=Path(self._tmp.name) / "does-not-exist.json")
        self.assertEqual(outcome.category, "verify_proof.file_not_found")

    def test_verify_proof_rejects_stale_run(self) -> None:
        self._write_sample_v3(store_fingerprint="1" * 64)
        outcome = runner.verify_proof(
            proof_path=self.V3_PATH,
            require_current_run=True,
            now=datetime.now(timezone.utc) + timedelta(days=2),
        )
        self.assertEqual(outcome.category, "verify_proof.stale_run")

    def test_verify_proof_rejects_hash_tampering(self) -> None:
        self._write_sample_v3(store_fingerprint="2" * 64)
        document = json.loads(self.V3_PATH.read_text(encoding="utf-8"))
        document["verified_release_count"] = 999
        self.V3_PATH.write_text(json.dumps(document), encoding="utf-8")
        outcome = runner.verify_proof(proof_path=self.V3_PATH, verify_hashes=True)
        self.assertEqual(outcome.category, "verify_proof.hash_mismatch")

    def test_verify_proof_rejects_fixture_mode_when_real_required(self) -> None:
        self._write_sample_v3(store_fingerprint="3" * 64)
        document = json.loads(self.V3_PATH.read_text(encoding="utf-8"))
        document["mode"] = "fixture"
        self.V3_PATH.write_text(json.dumps(document), encoding="utf-8")
        outcome = runner.verify_proof(proof_path=self.V3_PATH, require_mode="real")
        self.assertEqual(outcome.category, "verify_proof.mode_mismatch")

    def test_verify_proof_rejects_a_path_bearing_document(self) -> None:
        self._write_sample_v3(store_fingerprint="4" * 64)
        document = json.loads(self.V3_PATH.read_text(encoding="utf-8"))
        # Split so the committed source text itself never contains the
        # contiguous absolute-path shape the repository's own privacy
        # scanner flags everywhere (matches this repository's established
        # test-fixture convention).
        document["debug_note"] = "C" + ":" + "\\private\\store\\path"
        self.V3_PATH.write_text(json.dumps(document), encoding="utf-8")
        outcome = runner.verify_proof(proof_path=self.V3_PATH)
        self.assertEqual(outcome.category, "verify_proof.path_like_value_detected")

    def test_verify_proof_rejects_incomplete_diagnostics(self) -> None:
        self._write_sample_v3(store_fingerprint="5" * 64)
        document = json.loads(self.V3_PATH.read_text(encoding="utf-8"))
        document["diagnostics"]["measures"] = ["conditional_precision"]
        self.V3_PATH.write_text(json.dumps(document), encoding="utf-8")
        outcome = runner.verify_proof(proof_path=self.V3_PATH, require_diagnostics=True)
        self.assertEqual(outcome.category, "verify_proof.diagnostics_incomplete")

    def test_verify_proof_rejects_unsupported_claim(self) -> None:
        self._write_sample_v3(store_fingerprint="6" * 64)
        document = json.loads(self.V3_PATH.read_text(encoding="utf-8"))
        document["claim_support"]["status"] = "unsupported"
        self.V3_PATH.write_text(json.dumps(document), encoding="utf-8")
        outcome = runner.verify_proof(proof_path=self.V3_PATH, require_claim_support=True)
        self.assertEqual(outcome.category, "verify_proof.claim_not_supported")

    def test_verify_proof_rejects_non_exact_reconciliation(self) -> None:
        self._write_sample_v3(store_fingerprint="7" * 64)
        document = json.loads(self.V3_PATH.read_text(encoding="utf-8"))
        document["reconciliation"]["mismatch_row_count"] = 1
        self.V3_PATH.write_text(json.dumps(document), encoding="utf-8")
        outcome = runner.verify_proof(proof_path=self.V3_PATH, require_exact_reconciliation=True)
        self.assertEqual(outcome.category, "verify_proof.reconciliation_not_exact")

    def test_verify_proof_rejects_unverified_binding(self) -> None:
        self._write_sample_v3(store_fingerprint="8" * 64)
        document = json.loads(self.V3_PATH.read_text(encoding="utf-8"))
        document["verified_input_binding"] = False
        self.V3_PATH.write_text(json.dumps(document), encoding="utf-8")
        outcome = runner.verify_proof(proof_path=self.V3_PATH, require_verified_binding=True)
        self.assertEqual(outcome.category, "verify_proof.binding_not_verified")


if __name__ == "__main__":
    unittest.main()
