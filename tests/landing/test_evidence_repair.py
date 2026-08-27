"""Synthetic end-to-end contract suite for `tools.evidence_repair` (D-11-D-13).

Admits three synthetic releases (identical content, three distinct
`As-of Date` values, so every recomputed structural claim is known in
advance) into a temporary external store using the real
`calico_landing.admission.admit()` service, writes three safe synthetic
predecessor documents outside the product tree, and then runs the real
`python -m tools.evidence_repair derive`/`verify` subprocess commands
against them. Proves the exact four-artifact closed schema (with special
attention to this plan's own locked `correction-index-v1.json` shape and
its two bounded `resource_measurements` integers), deterministic key
order/serialization, hash/lineage correctness, SQL-owned recomputation,
fail-closed rejection with no partial output, and non-echo behavior.

No real organization identity or excluded value is used -- only reserved
synthetic sentinels split via runtime concatenation (mirrors
`tests.fixtures.landing.fixture_builder`'s D-10 precedent).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from calico_landing.admission import admit
from calico_landing.contracts import LOGICAL_LIST_ORDER
from calico_landing.store import read_promoted_releases
from tests.fixtures.landing import fixture_builder as fb

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_MODULE_PATH = REPO_ROOT / "tools" / "evidence_repair" / "__main__.py"
SQL_SCRIPT_PATH = REPO_ROOT / "tools" / "evidence_repair" / "spike_002_confirmation.sql"
CALICO_LANDING_DIR = REPO_ROOT / "calico_landing"

_RELEASE_DATES = ("2030-01-01", "2030-02-01", "2030-03-01")

#: Every synthetic sentinel this suite embeds in a malformed argument, a
#: nonexistent path, or predecessor file content -- none may ever appear
#: in stdout, stderr, or a written artifact (D-05/D-10).
SENTINEL_ARG_MARKER = "synthetic-evidence-arg-" + "zzqx-7734"
SENTINEL_PATH_MARKER = "synthetic-evidence-path-" + "zzqx-8845"
SENTINEL_PREDECESSOR_MARKER = "synthetic-evidence-predecessor-" + "zzqx-9956"

_CORRECTION_INDEX_KEYS = ["schema_version", "derivation", "corrections", "gate_a_evidence"]
_DERIVATION_KEYS = [
    "command_id",
    "generated_at_utc",
    "parser_contract_version",
    "parquet_writer_version",
    "source_release_fingerprints",
    "resource_measurements",
]
_SOURCE_FINGERPRINT_KEYS = [
    "as_of_date",
    "release_revision",
    "revision_fingerprint",
    "manifest_sha256",
]
_RESOURCE_MEASUREMENT_KEYS = [
    "first_admission_elapsed_ms",
    "first_admission_peak_temporary_disk_bytes",
]
_CORRECTION_ENTRY_KEYS = ["successor_file", "supersedes", "successor_sha256"]
_SUPERSEDES_KEYS = ["private_path", "predecessor_sha256"]
_GATE_A_EVIDENCE_KEYS = ["private_path", "sha256", "status"]

_AUGUST_SUCCESSOR = "august-manifest-successor-v1.json"
_SPIKE_001_SUCCESSOR = "spike-001-successor-v1.json"
_SPIKE_002_SUCCESSOR = "spike-002-successor-v1.json"
_CORRECTION_INDEX = "correction-index-v1.json"
_ALL_OUTPUT_FILENAMES = (_AUGUST_SUCCESSOR, _SPIKE_001_SUCCESSOR, _SPIKE_002_SUCCESSOR, _CORRECTION_INDEX)

_EXPECTED_PRIVATE_PATHS = {
    _AUGUST_SUCCESSOR: "data/registry-archive/manifest.json",
    _SPIKE_001_SUCCESSOR: ".planning/spikes/001-archive-sample-validation/archive-sample-manifest.json",
    _SPIKE_002_SUCCESSOR: ".planning/spikes/002-entity-change-validation/entity-changes.json",
}

_HEX64 = "^[0-9a-f]{64}$"


def _cli_env() -> dict[str, str]:
    env = {"PYTHONPATH": str(REPO_ROOT)}
    for key in ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP"):
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    return env


def _run_tool(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "tools.evidence_repair", *args],
        cwd=REPO_ROOT,
        env=_cli_env(),
        capture_output=True,
        check=False,
    )


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _set_all_as_of_dates(candidate: fb.MutatedCandidate, new_date: str) -> None:
    for logical_list in fb.LOGICAL_LIST_FILES:
        text = candidate.read_bytes(logical_list).decode("cp1252")
        lines = text.split("\r\n")
        data_line_count = sum(1 for line in lines[1:] if line)
        for row_index in range(data_line_count):
            candidate.replace_field(logical_list, row_index, fb.AS_OF_DATE_COLUMN, new_date)


def _admit_release(store_root: Path, as_of_date: str) -> None:
    with fb.mutated_candidate() as candidate:
        _set_all_as_of_dates(candidate, as_of_date)
        result = admit(candidate.root, store_root)
    if result.status != "accepted":
        raise AssertionError(f"synthetic setup admission did not accept: {result.status}")


@contextmanager
def _admitted_store() -> Iterator[tuple[Path, list[str]]]:
    with tempfile.TemporaryDirectory() as store_dir:
        store_root = Path(store_dir)
        for as_of_date in _RELEASE_DATES:
            _admit_release(store_root, as_of_date)
        releases = [f"{as_of_date}:1" for as_of_date in _RELEASE_DATES]
        yield store_root, releases


@contextmanager
def _synthetic_predecessors() -> Iterator[tuple[Path, Path, Path]]:
    with tempfile.TemporaryDirectory() as predecessor_dir:
        root = Path(predecessor_dir)
        paths = []
        for name in ("august-predecessor.json", "spike-001-predecessor.json", "spike-002-predecessor.json"):
            path = root / name
            path.write_text(
                json.dumps({"note": "synthetic predecessor", "marker": SENTINEL_PREDECESSOR_MARKER}),
                encoding="utf-8",
            )
            paths.append(path)
        yield tuple(paths)  # type: ignore[return-value]


def _derive_args(
    store_root: Path,
    releases: list[str],
    predecessors: tuple[Path, Path, Path],
    output_dir: Path,
    *,
    elapsed_ms: str = "1000",
    peak_bytes: str = "2000",
    extra_elapsed_ms: list[str] | None = None,
    extra_peak_bytes: list[str] | None = None,
) -> list[str]:
    august, spike001, spike002 = predecessors
    args = ["derive", "--store", str(store_root)]
    for release in releases:
        args += ["--release", release]
    args += [
        "--august-predecessor",
        str(august),
        "--spike-001-predecessor",
        str(spike001),
        "--spike-002-predecessor",
        str(spike002),
        "--output-dir",
        str(output_dir),
    ]
    elapsed_values = [elapsed_ms] if extra_elapsed_ms is None else extra_elapsed_ms
    peak_values = [peak_bytes] if extra_peak_bytes is None else extra_peak_bytes
    for value in elapsed_values:
        args += ["--first-admission-elapsed-ms", value]
    for value in peak_values:
        args += ["--first-admission-peak-temporary-disk-bytes", value]
    return args


class DeriveSuccessTests(unittest.TestCase):
    """Exact filenames, closed key order, hashes, and recomputed content."""

    def test_derive_creates_exactly_four_artifacts_with_locked_schema(self) -> None:
        with _admitted_store() as (store_root, releases), _synthetic_predecessors() as predecessors:
            with tempfile.TemporaryDirectory() as output_dir:
                output_path = Path(output_dir) / "artifacts"
                completed = _run_tool(
                    _derive_args(
                        store_root,
                        releases,
                        predecessors,
                        output_path,
                        elapsed_ms="12345",
                        peak_bytes="67890",
                    )
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

                actual_files = sorted(p.name for p in output_path.iterdir())
                self.assertEqual(actual_files, sorted(_ALL_OUTPUT_FILENAMES))
                self.assertFalse((output_path / ".evidence-repair.lock").exists())

                index_text = (output_path / _CORRECTION_INDEX).read_text(encoding="utf-8")
                self.assertTrue(index_text.endswith("\n"))
                self.assertFalse(index_text.endswith("\n\n"))
                index = json.loads(index_text)

                self.assertEqual(list(index.keys()), _CORRECTION_INDEX_KEYS)
                self.assertEqual(index["schema_version"], "correction-index-v1")

                derivation = index["derivation"]
                self.assertEqual(list(derivation.keys()), _DERIVATION_KEYS)
                self.assertRegex(derivation["generated_at_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
                self.assertIsInstance(derivation["parser_contract_version"], int)
                self.assertIsInstance(derivation["parquet_writer_version"], str)

                fingerprints = derivation["source_release_fingerprints"]
                self.assertEqual(len(fingerprints), 3)
                for expected_date, entry in zip(_RELEASE_DATES, fingerprints):
                    self.assertEqual(list(entry.keys()), _SOURCE_FINGERPRINT_KEYS)
                    self.assertEqual(entry["as_of_date"], expected_date)
                    self.assertEqual(entry["release_revision"], 1)
                    self.assertRegex(entry["revision_fingerprint"], _HEX64)
                    self.assertRegex(entry["manifest_sha256"], _HEX64)

                resources = derivation["resource_measurements"]
                self.assertEqual(list(resources.keys()), _RESOURCE_MEASUREMENT_KEYS)
                self.assertEqual(resources["first_admission_elapsed_ms"], 12345)
                self.assertNotIsInstance(resources["first_admission_elapsed_ms"], bool)
                self.assertEqual(resources["first_admission_peak_temporary_disk_bytes"], 67890)
                self.assertNotIsInstance(resources["first_admission_peak_temporary_disk_bytes"], bool)

                corrections = index["corrections"]
                self.assertEqual(len(corrections), 3)
                for expected_filename, entry in zip(_ALL_OUTPUT_FILENAMES[:3], corrections):
                    self.assertEqual(list(entry.keys()), _CORRECTION_ENTRY_KEYS)
                    self.assertEqual(entry["successor_file"], expected_filename)
                    self.assertEqual(list(entry["supersedes"].keys()), _SUPERSEDES_KEYS)
                    self.assertEqual(
                        entry["supersedes"]["private_path"], _EXPECTED_PRIVATE_PATHS[expected_filename]
                    )
                    self.assertRegex(entry["supersedes"]["predecessor_sha256"], _HEX64)
                    self.assertRegex(entry["successor_sha256"], _HEX64)
                    successor_path = output_path / expected_filename
                    self.assertEqual(entry["successor_sha256"], _hash_file(successor_path))

                gate_a_evidence = index["gate_a_evidence"]
                self.assertEqual(list(gate_a_evidence.keys()), _GATE_A_EVIDENCE_KEYS)
                self.assertEqual(gate_a_evidence["private_path"], "GATE-A-EVIDENCE.md")
                self.assertEqual(gate_a_evidence["status"], "unchanged")
                self.assertRegex(gate_a_evidence["sha256"], _HEX64)

    def test_predecessor_sha256_matches_actual_predecessor_bytes(self) -> None:
        with _admitted_store() as (store_root, releases), _synthetic_predecessors() as predecessors:
            with tempfile.TemporaryDirectory() as output_dir:
                output_path = Path(output_dir) / "artifacts"
                completed = _run_tool(_derive_args(store_root, releases, predecessors, output_path))
                self.assertEqual(completed.returncode, 0)

                index = json.loads((output_path / _CORRECTION_INDEX).read_text(encoding="utf-8"))
                for predecessor_path, entry in zip(predecessors, index["corrections"]):
                    self.assertEqual(
                        entry["supersedes"]["predecessor_sha256"], _hash_file(predecessor_path)
                    )
                    # Never the caller's actual local path.
                    self.assertNotEqual(entry["supersedes"]["private_path"], str(predecessor_path))

    def test_recomputed_coverage_and_delinquency_match_known_fixture_content(self) -> None:
        # The committed baseline candidate carries nine total rows (one
        # blank-key row) and exactly two delinquent-family rows, unchanged
        # across all three synthetic releases here (only `As-of Date`
        # differs) -- a fully known-in-advance recomputation target.
        with _admitted_store() as (store_root, releases), _synthetic_predecessors() as predecessors:
            with tempfile.TemporaryDirectory() as output_dir:
                output_path = Path(output_dir) / "artifacts"
                completed = _run_tool(_derive_args(store_root, releases, predecessors, output_path))
                self.assertEqual(completed.returncode, 0)

                spike_002 = json.loads(
                    (output_path / _SPIKE_002_SUCCESSOR).read_text(encoding="utf-8")
                )
                self.assertEqual(len(spike_002["coverage"]), 3)
                for coverage_entry in spike_002["coverage"]:
                    self.assertEqual(coverage_entry["total_row_count"], 9)
                    self.assertEqual(coverage_entry["keyed_row_count"], 8)
                    self.assertEqual(coverage_entry["keyless_row_count"], 1)
                    self.assertEqual(coverage_entry["delinquent_row_count"], 2)
                    self.assertEqual(coverage_entry["coverage_status"], "corrected")

                # Identical membership across all three synthetic releases
                # (only the As-of Date column differs between them).
                membership = spike_002["keyed_membership"]
                self.assertEqual(len(membership), 3)
                self.assertEqual(membership[0]["sha256"], membership[1]["sha256"])
                self.assertEqual(membership[1]["sha256"], membership[2]["sha256"])
                for entry in membership:
                    self.assertEqual(entry["membership_status"], "confirmed_by_recomputation")

                transitions = spike_002["transition_confirmations"]
                self.assertEqual(len(transitions), 2)
                for transition in transitions:
                    self.assertEqual(transition["exit_count"], 0)
                    self.assertEqual(transition["status"], "confirmed_by_recomputation")

                august = json.loads((output_path / _AUGUST_SUCCESSOR).read_text(encoding="utf-8"))
                self.assertEqual(august["release_total"], 9)
                self.assertEqual(sum(august["logical_list_totals"].values()), 9)
                self.assertEqual(set(august["logical_list_totals"]), set(LOGICAL_LIST_ORDER))
                self.assertEqual(august["status"], "corrected")

                spike_001 = json.loads((output_path / _SPIKE_001_SUCCESSOR).read_text(encoding="utf-8"))
                self.assertEqual(spike_001["release_total"], 9)
                self.assertTrue(spike_001["embedded_newline_explanation_retracted"])
                self.assertEqual(spike_001["status"], "corrected")

    def test_repeated_derive_is_reproducible_apart_from_generation_timestamp(self) -> None:
        with _admitted_store() as (store_root, releases), _synthetic_predecessors() as predecessors:
            with tempfile.TemporaryDirectory() as output_dir:
                first_dir = Path(output_dir) / "first"
                second_dir = Path(output_dir) / "second"
                first = _run_tool(_derive_args(store_root, releases, predecessors, first_dir))
                second = _run_tool(_derive_args(store_root, releases, predecessors, second_dir))
                self.assertEqual(first.returncode, 0)
                self.assertEqual(second.returncode, 0)

                for filename in (_AUGUST_SUCCESSOR, _SPIKE_001_SUCCESSOR, _SPIKE_002_SUCCESSOR):
                    self.assertEqual(
                        (first_dir / filename).read_bytes(), (second_dir / filename).read_bytes()
                    )

                first_index = json.loads((first_dir / _CORRECTION_INDEX).read_text(encoding="utf-8"))
                second_index = json.loads((second_dir / _CORRECTION_INDEX).read_text(encoding="utf-8"))
                first_index["derivation"]["generated_at_utc"] = "FIXED"
                second_index["derivation"]["generated_at_utc"] = "FIXED"
                self.assertEqual(first_index, second_index)

    def test_predecessor_files_remain_byte_identical_after_derive(self) -> None:
        with _admitted_store() as (store_root, releases), _synthetic_predecessors() as predecessors:
            before = {path: path.read_bytes() for path in predecessors}
            with tempfile.TemporaryDirectory() as output_dir:
                completed = _run_tool(
                    _derive_args(store_root, releases, predecessors, Path(output_dir) / "artifacts")
                )
                self.assertEqual(completed.returncode, 0)
            for path, original_bytes in before.items():
                self.assertEqual(path.read_bytes(), original_bytes)

    def test_verify_accepts_freshly_derived_artifacts_including_store_drift_check(self) -> None:
        with _admitted_store() as (store_root, releases), _synthetic_predecessors() as predecessors:
            with tempfile.TemporaryDirectory() as output_dir:
                output_path = Path(output_dir) / "artifacts"
                derive_completed = _run_tool(
                    _derive_args(store_root, releases, predecessors, output_path)
                )
                self.assertEqual(derive_completed.returncode, 0)

                august, spike001, spike002 = predecessors
                verify_completed = _run_tool(
                    [
                        "verify",
                        "--artifacts",
                        str(output_path),
                        "--store",
                        str(store_root),
                        "--august-predecessor",
                        str(august),
                        "--spike-001-predecessor",
                        str(spike001),
                        "--spike-002-predecessor",
                        str(spike002),
                    ]
                )
                self.assertEqual(verify_completed.returncode, 0, verify_completed.stdout + verify_completed.stderr)


class SafeOutputContentTests(unittest.TestCase):
    """No row, membership, raw-column, or absolute-path leakage (D-11)."""

    def test_outputs_contain_no_raw_registration_keys_paths_or_row_material(self) -> None:
        with _admitted_store() as (store_root, releases), _synthetic_predecessors() as predecessors:
            with tempfile.TemporaryDirectory() as output_dir:
                output_path = Path(output_dir) / "artifacts"
                completed = _run_tool(_derive_args(store_root, releases, predecessors, output_path))
                self.assertEqual(completed.returncode, 0)

                forbidden_substrings = (
                    "1023456",
                    "CT100045",
                    "EX200078",
                    "2045678",
                    "3078901",
                    "CT300099",
                    "4098765",
                    "EX400012",
                    ".csv",
                    ".parquet",
                    ".pdf",
                    str(store_root),
                    SENTINEL_PREDECESSOR_MARKER,
                )
                for filename in _ALL_OUTPUT_FILENAMES:
                    text = (output_path / filename).read_text(encoding="utf-8")
                    for forbidden in forbidden_substrings:
                        self.assertNotIn(forbidden, text, f"{forbidden!r} leaked into {filename}")

    def test_gate_a_evidence_and_predecessors_never_read_for_content(self) -> None:
        # The correction index's `gate_a_evidence.private_path` is a fixed
        # label, never the caller's `--*-predecessor` argument text.
        with _admitted_store() as (store_root, releases), _synthetic_predecessors() as predecessors:
            with tempfile.TemporaryDirectory() as output_dir:
                output_path = Path(output_dir) / "artifacts"
                completed = _run_tool(_derive_args(store_root, releases, predecessors, output_path))
                self.assertEqual(completed.returncode, 0)
                index = json.loads((output_path / _CORRECTION_INDEX).read_text(encoding="utf-8"))
                self.assertEqual(index["gate_a_evidence"]["private_path"], "GATE-A-EVIDENCE.md")


class ResourceMeasurementValidationTests(unittest.TestCase):
    """The one Plan 06 correction-index contract: exactly-once, bounded,
    canonical-digit resource measurements (D-05 non-echo on rejection).
    """

    def _assert_accepts(self, elapsed_ms: str, peak_bytes: str, *, expected_elapsed: int, expected_peak: int) -> None:
        with _admitted_store() as (store_root, releases), _synthetic_predecessors() as predecessors:
            with tempfile.TemporaryDirectory() as output_dir:
                output_path = Path(output_dir) / "artifacts"
                completed = _run_tool(
                    _derive_args(
                        store_root, releases, predecessors, output_path, elapsed_ms=elapsed_ms, peak_bytes=peak_bytes
                    )
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                index = json.loads((output_path / _CORRECTION_INDEX).read_text(encoding="utf-8"))
                resources = index["derivation"]["resource_measurements"]
                self.assertEqual(resources["first_admission_elapsed_ms"], expected_elapsed)
                self.assertEqual(resources["first_admission_peak_temporary_disk_bytes"], expected_peak)

    def test_ordinary_values_accepted(self) -> None:
        self._assert_accepts("500", "1024", expected_elapsed=500, expected_peak=1024)

    def test_zero_bound_accepted(self) -> None:
        self._assert_accepts("0", "0", expected_elapsed=0, expected_peak=0)

    def test_inclusive_maximum_accepted(self) -> None:
        self._assert_accepts(
            "86400000", "1099511627776", expected_elapsed=86400000, expected_peak=1099511627776
        )

    def _assert_rejects(self, *, extra_elapsed_ms: list[str] | None = None, extra_peak_bytes: list[str] | None = None) -> None:
        with _admitted_store() as (store_root, releases), _synthetic_predecessors() as predecessors:
            with tempfile.TemporaryDirectory() as output_dir:
                output_path = Path(output_dir) / "artifacts"
                completed = _run_tool(
                    _derive_args(
                        store_root,
                        releases,
                        predecessors,
                        output_path,
                        extra_elapsed_ms=extra_elapsed_ms,
                        extra_peak_bytes=extra_peak_bytes,
                    )
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertFalse(output_path.exists())
                combined = completed.stdout + completed.stderr
                self.assertNotIn(SENTINEL_ARG_MARKER.encode("utf-8"), combined)

    def test_omitted_elapsed_ms_rejected(self) -> None:
        self._assert_rejects(extra_elapsed_ms=[])

    def test_duplicated_elapsed_ms_rejected(self) -> None:
        self._assert_rejects(extra_elapsed_ms=["100", "200"])

    def test_negative_elapsed_ms_rejected(self) -> None:
        self._assert_rejects(extra_elapsed_ms=["-1"])

    def test_fractional_elapsed_ms_rejected(self) -> None:
        self._assert_rejects(extra_elapsed_ms=["1.5"])

    def test_exponent_elapsed_ms_rejected(self) -> None:
        self._assert_rejects(extra_elapsed_ms=["1e3"])

    def test_non_finite_elapsed_ms_rejected(self) -> None:
        self._assert_rejects(extra_elapsed_ms=["inf"])
        self._assert_rejects(extra_elapsed_ms=["nan"])

    def test_boolean_shaped_elapsed_ms_rejected(self) -> None:
        self._assert_rejects(extra_elapsed_ms=["true"])
        self._assert_rejects(extra_elapsed_ms=["False"])

    def test_non_digit_elapsed_ms_rejected(self) -> None:
        self._assert_rejects(extra_elapsed_ms=[SENTINEL_ARG_MARKER])

    def test_leading_zero_elapsed_ms_rejected(self) -> None:
        self._assert_rejects(extra_elapsed_ms=["007"])

    def test_above_maximum_elapsed_ms_rejected(self) -> None:
        self._assert_rejects(extra_elapsed_ms=["86400001"])

    def test_above_maximum_peak_bytes_rejected(self) -> None:
        self._assert_rejects(extra_peak_bytes=["1099511627777"])

    def test_omitted_peak_bytes_rejected(self) -> None:
        self._assert_rejects(extra_peak_bytes=[])

    def test_duplicated_peak_bytes_rejected(self) -> None:
        self._assert_rejects(extra_peak_bytes=["1", "2"])


class VerifyMutationRejectionTests(unittest.TestCase):
    """Mutate a verified artifact set at every defined nesting level;
    `verify` must reject every mutation (D-05/D-08/T-02-08).
    """

    def setUp(self) -> None:
        self._exit_stack_dirs: list[tempfile.TemporaryDirectory] = []

    def tearDown(self) -> None:
        for handle in self._exit_stack_dirs:
            handle.cleanup()

    def _baseline_artifacts(self) -> tuple[Path, tuple[Path, Path, Path]]:
        store_dir = tempfile.TemporaryDirectory()
        predecessor_dir = tempfile.TemporaryDirectory()
        output_dir = tempfile.TemporaryDirectory()
        self._exit_stack_dirs.extend([store_dir, predecessor_dir, output_dir])

        store_root = Path(store_dir.name)
        for as_of_date in _RELEASE_DATES:
            _admit_release(store_root, as_of_date)
        releases = [f"{as_of_date}:1" for as_of_date in _RELEASE_DATES]

        predecessor_root = Path(predecessor_dir.name)
        predecessors = []
        for name in ("august-predecessor.json", "spike-001-predecessor.json", "spike-002-predecessor.json"):
            path = predecessor_root / name
            path.write_text(json.dumps({"note": "synthetic"}), encoding="utf-8")
            predecessors.append(path)
        predecessors = tuple(predecessors)  # type: ignore[assignment]

        output_path = Path(output_dir.name) / "artifacts"
        completed = _run_tool(_derive_args(store_root, releases, predecessors, output_path))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return output_path, predecessors

    def _verify(self, artifacts_dir: Path, predecessors: tuple[Path, Path, Path]) -> subprocess.CompletedProcess[bytes]:
        august, spike001, spike002 = predecessors
        return _run_tool(
            [
                "verify",
                "--artifacts",
                str(artifacts_dir),
                "--august-predecessor",
                str(august),
                "--spike-001-predecessor",
                str(spike001),
                "--spike-002-predecessor",
                str(spike002),
            ]
        )

    def _mutated_copy(self, source_dir: Path, mutate) -> Path:
        target_dir = Path(tempfile.mkdtemp(prefix="calico-evidence-verify-"))
        for name in _ALL_OUTPUT_FILENAMES:
            shutil.copyfile(source_dir / name, target_dir / name)
        index_path = target_dir / _CORRECTION_INDEX
        document = json.loads(index_path.read_text(encoding="utf-8"))
        mutate(document)
        index_path.write_text(json.dumps(document), encoding="utf-8")
        return target_dir

    def test_baseline_verifies_clean(self) -> None:
        output_path, predecessors = self._baseline_artifacts()
        completed = self._verify(output_path, predecessors)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def _assert_mutation_rejected(self, mutate) -> None:
        output_path, predecessors = self._baseline_artifacts()
        mutated_dir = self._mutated_copy(output_path, mutate)
        try:
            completed = self._verify(mutated_dir, predecessors)
            self.assertNotEqual(completed.returncode, 0)
        finally:
            shutil.rmtree(mutated_dir, ignore_errors=True)

    def test_resource_measurement_string_value_rejected(self) -> None:
        self._assert_mutation_rejected(
            lambda doc: doc["derivation"]["resource_measurements"].__setitem__(
                "first_admission_elapsed_ms", "1000"
            )
        )

    def test_resource_measurement_float_value_rejected(self) -> None:
        self._assert_mutation_rejected(
            lambda doc: doc["derivation"]["resource_measurements"].__setitem__(
                "first_admission_elapsed_ms", 1.5
            )
        )

    def test_resource_measurement_bool_value_rejected(self) -> None:
        self._assert_mutation_rejected(
            lambda doc: doc["derivation"]["resource_measurements"].__setitem__(
                "first_admission_elapsed_ms", True
            )
        )

    def test_resource_measurement_null_value_rejected(self) -> None:
        self._assert_mutation_rejected(
            lambda doc: doc["derivation"]["resource_measurements"].__setitem__(
                "first_admission_elapsed_ms", None
            )
        )

    def test_resource_measurement_negative_value_rejected(self) -> None:
        self._assert_mutation_rejected(
            lambda doc: doc["derivation"]["resource_measurements"].__setitem__(
                "first_admission_elapsed_ms", -1
            )
        )

    def test_resource_measurement_overflow_value_rejected(self) -> None:
        self._assert_mutation_rejected(
            lambda doc: doc["derivation"]["resource_measurements"].__setitem__(
                "first_admission_peak_temporary_disk_bytes", 1099511627777
            )
        )

    def test_resource_measurement_swapped_key_rejected(self) -> None:
        def mutate(doc: dict) -> None:
            measurements = doc["derivation"]["resource_measurements"]
            value = measurements.pop("first_admission_elapsed_ms")
            measurements["first_admission_elapsed_milliseconds"] = value

        self._assert_mutation_rejected(mutate)

    def test_resource_measurement_missing_key_rejected(self) -> None:
        def mutate(doc: dict) -> None:
            del doc["derivation"]["resource_measurements"]["first_admission_elapsed_ms"]

        self._assert_mutation_rejected(mutate)

    def test_resource_measurement_extra_key_rejected(self) -> None:
        def mutate(doc: dict) -> None:
            doc["derivation"]["resource_measurements"]["extra_field"] = 1

        self._assert_mutation_rejected(mutate)

    def test_arbitrary_metadata_at_top_level_rejected(self) -> None:
        def mutate(doc: dict) -> None:
            doc["metadata"] = {"note": "unexpected"}

        self._assert_mutation_rejected(mutate)

    def test_arbitrary_metadata_in_derivation_rejected(self) -> None:
        def mutate(doc: dict) -> None:
            doc["derivation"]["metadata"] = {"note": "unexpected"}

        self._assert_mutation_rejected(mutate)

    def test_unknown_key_in_correction_entry_rejected(self) -> None:
        def mutate(doc: dict) -> None:
            doc["corrections"][0]["extra"] = "unexpected"

        self._assert_mutation_rejected(mutate)

    def test_unknown_key_in_supersedes_rejected(self) -> None:
        def mutate(doc: dict) -> None:
            doc["corrections"][0]["supersedes"]["extra"] = "unexpected"

        self._assert_mutation_rejected(mutate)

    def test_unknown_key_in_gate_a_evidence_rejected(self) -> None:
        def mutate(doc: dict) -> None:
            doc["gate_a_evidence"]["extra"] = "unexpected"

        self._assert_mutation_rejected(mutate)

    def test_gate_a_evidence_status_mutation_rejected(self) -> None:
        def mutate(doc: dict) -> None:
            doc["gate_a_evidence"]["status"] = "changed"

        self._assert_mutation_rejected(mutate)

    def test_wrong_source_release_fingerprint_count_rejected(self) -> None:
        def mutate(doc: dict) -> None:
            doc["derivation"]["source_release_fingerprints"].pop()

        self._assert_mutation_rejected(mutate)

    def test_out_of_order_source_release_fingerprints_rejected(self) -> None:
        def mutate(doc: dict) -> None:
            fingerprints = doc["derivation"]["source_release_fingerprints"]
            fingerprints[0], fingerprints[1] = fingerprints[1], fingerprints[0]

        self._assert_mutation_rejected(mutate)

    def test_tampered_successor_hash_rejected(self) -> None:
        def mutate(doc: dict) -> None:
            doc["corrections"][0]["successor_sha256"] = "0" * 64

        self._assert_mutation_rejected(mutate)

    def test_tampered_predecessor_hash_rejected(self) -> None:
        def mutate(doc: dict) -> None:
            doc["corrections"][0]["supersedes"]["predecessor_sha256"] = "0" * 64

        self._assert_mutation_rejected(mutate)


class FailClosedNoPartialOutputTests(unittest.TestCase):
    """One mutated boundary condition at a time; every rejection leaves
    zero output files (D-07 collision/no-partial-output policy).
    """

    def test_wrong_revision_selection_rejected_with_no_output(self) -> None:
        with _admitted_store() as (store_root, releases), _synthetic_predecessors() as predecessors:
            bad_releases = list(releases)
            bad_releases[0] = bad_releases[0].rsplit(":", 1)[0] + ":2"
            with tempfile.TemporaryDirectory() as output_dir:
                output_path = Path(output_dir) / "artifacts"
                completed = _run_tool(_derive_args(store_root, bad_releases, predecessors, output_path))
                self.assertNotEqual(completed.returncode, 0)
                self.assertFalse(output_path.exists())

    def test_tampered_parquet_hash_rejected_with_no_output(self) -> None:
        with _admitted_store() as (store_root, releases), _synthetic_predecessors() as predecessors:
            promoted = read_promoted_releases(store_root)
            revision_dir = promoted[_RELEASE_DATES[0]].revision_dir
            parquet_path = store_root / revision_dir / "canonical" / "charities-may-operate.parquet"
            original = parquet_path.read_bytes()
            parquet_path.write_bytes(original + b"\x00tamper")
            try:
                with tempfile.TemporaryDirectory() as output_dir:
                    output_path = Path(output_dir) / "artifacts"
                    completed = _run_tool(_derive_args(store_root, releases, predecessors, output_path))
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertFalse(output_path.exists())
            finally:
                parquet_path.write_bytes(original)

    def test_output_collision_leaves_existing_file_untouched(self) -> None:
        with _admitted_store() as (store_root, releases), _synthetic_predecessors() as predecessors:
            with tempfile.TemporaryDirectory() as output_dir:
                output_path = Path(output_dir) / "artifacts"
                output_path.mkdir()
                sentinel_bytes = b"pre-existing-content"
                (output_path / _CORRECTION_INDEX).write_bytes(sentinel_bytes)

                completed = _run_tool(_derive_args(store_root, releases, predecessors, output_path))
                self.assertNotEqual(completed.returncode, 0)
                self.assertEqual((output_path / _CORRECTION_INDEX).read_bytes(), sentinel_bytes)
                for filename in (_AUGUST_SUCCESSOR, _SPIKE_001_SUCCESSOR, _SPIKE_002_SUCCESSOR):
                    self.assertFalse((output_path / filename).exists())

    def test_output_dir_aliased_to_store_rejected(self) -> None:
        with _admitted_store() as (store_root, releases), _synthetic_predecessors() as predecessors:
            completed = _run_tool(_derive_args(store_root, releases, predecessors, store_root))
            self.assertNotEqual(completed.returncode, 0)

    def test_store_inside_git_worktree_rejected(self) -> None:
        store_inside_repo = REPO_ROOT / ".evidence-repair-test-tmp-store"
        self.assertFalse(store_inside_repo.exists())
        store_inside_repo.mkdir()
        try:
            with _synthetic_predecessors() as predecessors, tempfile.TemporaryDirectory() as output_dir:
                releases = [f"{as_of_date}:1" for as_of_date in _RELEASE_DATES]
                completed = _run_tool(
                    _derive_args(store_inside_repo, releases, predecessors, Path(output_dir) / "artifacts")
                )
                self.assertNotEqual(completed.returncode, 0)
                combined = completed.stdout + completed.stderr
                self.assertNotIn(str(store_inside_repo).encode("utf-8"), combined)
        finally:
            store_inside_repo.rmdir()

    def test_missing_predecessor_file_rejected_with_no_echo(self) -> None:
        with _admitted_store() as (store_root, releases):
            with tempfile.TemporaryDirectory() as scratch_dir:
                missing_path = Path(scratch_dir) / f"does-not-exist-{SENTINEL_PATH_MARKER}.json"
                existing_path = Path(scratch_dir) / "present.json"
                existing_path.write_text("{}", encoding="utf-8")
                output_path = Path(scratch_dir) / "artifacts"

                completed = _run_tool(
                    _derive_args(
                        store_root,
                        releases,
                        (missing_path, existing_path, existing_path),
                        output_path,
                    )
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertFalse(output_path.exists())
                combined = completed.stdout + completed.stderr
                self.assertNotIn(SENTINEL_PATH_MARKER.encode("utf-8"), combined)


class StructuralOwnershipTests(unittest.TestCase):
    """The bundled SQL, not `calico_landing` or this module's own Python
    text, owns every analytical calculation (D-02/D-03).
    """

    def test_main_module_never_embeds_sql_text(self) -> None:
        source = MAIN_MODULE_PATH.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\bSELECT\b", source, re.IGNORECASE))
        self.assertIsNone(re.search(r"\bCREATE\s+VIEW\b", source, re.IGNORECASE))

    def test_bundled_sql_script_owns_the_select_statements(self) -> None:
        sql_text = SQL_SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIsNotNone(re.search(r"\bSELECT\b", sql_text, re.IGNORECASE))
        self.assertIsNotNone(re.search(r"\bCREATE\s+VIEW\b", sql_text, re.IGNORECASE))

    def test_no_production_landing_module_imports_evidence_tooling(self) -> None:
        for path in CALICO_LANDING_DIR.glob("*.py"):
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("evidence_repair", content, f"{path} imports evidence tooling")

    def test_package_import_performs_no_filesystem_io(self) -> None:
        with tempfile.TemporaryDirectory() as import_cwd:
            completed = subprocess.run(
                [sys.executable, "-c", "import tools.evidence_repair; print('import-ok')"],
                cwd=import_cwd,
                env=_cli_env(),
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"import-ok\n")
            self.assertEqual(completed.stderr, b"")
            self.assertEqual(list(Path(import_cwd).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
