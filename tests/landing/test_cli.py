"""Subprocess observable and non-echo CLI contract for `calico_landing` (D-04/D-05/D-06).

Invokes the stable public `python -m calico_landing admit --candidate-input
<dir> --store <dir>` command in a subprocess and asserts, at the byte level
across combined stdout and stderr, the exact machine JSON key set/status,
the fixed human status vocabulary, and the locked four-exit-code contract
for accepted, identical-rerun no-new-release, rejected, XLSX-deferred, and
internal-error outcomes -- plus package-import side-effect freedom and the
byte absence of every synthetic sentinel this suite's fixtures embed
(mirrors `tests/tools/privacy_scan/test_non_echo.py`'s subprocess/non-echo
pattern).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from calico_landing.store import read_promoted_releases
from tests.fixtures.landing import fixture_builder as fb

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_CANDIDATE_ROOT = REPO_ROOT / "tests" / "fixtures" / "landing" / "valid"

#: Every synthetic sentinel any fixture in this suite embeds in a field
#: value or path -- none may ever appear in combined CLI output.
SYNTHETIC_PRIVATE_MARKERS = (
    fb.SENTINEL_DUPLICATE_KEY,
    fb.SENTINEL_UNKNOWN_FAMILY_KEY,
    fb.SENTINEL_MISMATCHED_DATE,
    fb.CP1252_HIGH_BYTE_NAME,
)

_RESULT_KEYS = {
    "schema_version",
    "status",
    "as_of_date",
    "release_revision",
    "revision_fingerprint",
    "reasons",
}
_REASON_KEYS = {"code", "logical_list", "safe_line_number", "safe_location", "safe_count"}


def _recompute_content_length(candidate_root: Path) -> None:
    """Resynchronize a mutated candidate's manifest `content_length` fields
    with the mutated CSVs' actual on-disk byte counts (mirrors
    `tests/landing/test_admission.py::_recompute_content_length`; duplicated
    locally since `fixture_builder.py` is Plan 08's already-committed file
    and out of this plan's ownership).

    `fixture_builder`'s mutation helpers rewrite CSV bytes without updating
    the copied baseline manifest's declared `content_length`. Every CLI
    test below that targets a check other than D-05 `transfer.length_mismatch`
    calls this first so the CLI's `admit()` call reaches the specific rule
    the fixture exists to exercise.
    """

    manifest_path = candidate_root / fb.MANIFEST_FILENAME
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in document["objects"].values():
        csv_path = candidate_root / entry["relative_path"]
        entry["content_length"] = csv_path.stat().st_size
    manifest_path.write_text(json.dumps(document), encoding="utf-8")


def _normalize(data: bytes) -> bytes:
    """Normalize platform line endings so byte-level assertions are stable
    across Windows (`print()` emits `\\r\\n` to a piped stream) and POSIX.
    """

    return data.replace(b"\r\n", b"\n")


def _cli_env() -> dict[str, str]:
    env = {}
    for key in ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP"):
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    return env


def _invoke_admit(candidate_input: Path, store: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "calico_landing",
            "admit",
            "--candidate-input",
            str(candidate_input),
            "--store",
            str(store),
        ],
        cwd=REPO_ROOT,
        env=_cli_env(),
        capture_output=True,
        check=False,
    )


def _assert_no_sentinels(test: unittest.TestCase, combined: bytes, extra: tuple[str, ...] = ()) -> None:
    for marker in SYNTHETIC_PRIVATE_MARKERS + extra:
        test.assertNotIn(marker.encode("utf-8"), combined)


class AcceptedRevisionCliTests(unittest.TestCase):
    def test_accepted_revision_exact_json_and_status_and_exit(self) -> None:
        with tempfile.TemporaryDirectory() as store_dir:
            store_root = Path(store_dir)
            completed = _invoke_admit(BASELINE_CANDIDATE_ROOT, store_root)

            self.assertEqual(completed.returncode, 0)

            document = json.loads(completed.stdout.decode("utf-8"))
            self.assertEqual(set(document), _RESULT_KEYS)
            self.assertEqual(document["status"], "accepted")
            self.assertEqual(document["as_of_date"], "2020-01-15")
            self.assertEqual(document["release_revision"], 1)
            self.assertIsInstance(document["revision_fingerprint"], str)
            self.assertEqual(document["reasons"], [])
            self.assertEqual(_normalize(completed.stdout).count(b"\n"), 1)

            self.assertEqual(
                _normalize(completed.stderr), b"accepted as_of=2020-01-15 revision=1\n"
            )

            _assert_no_sentinels(
                self, completed.stdout + completed.stderr, extra=(str(store_root),)
            )

    def test_identical_rerun_is_no_new_release_with_unchanged_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as store_dir:
            store_root = Path(store_dir)
            first = _invoke_admit(BASELINE_CANDIDATE_ROOT, store_root)
            self.assertEqual(first.returncode, 0)

            pointer_path = store_root / "promoted-releases.json"
            pointer_before = pointer_path.read_bytes()

            second = _invoke_admit(BASELINE_CANDIDATE_ROOT, store_root)
            self.assertEqual(second.returncode, 2)

            document = json.loads(second.stdout.decode("utf-8"))
            self.assertEqual(set(document), _RESULT_KEYS)
            self.assertEqual(document["status"], "no_new_release")
            self.assertEqual(document["release_revision"], 1)
            self.assertEqual(document["reasons"], [])

            self.assertEqual(
                _normalize(second.stderr), b"no_new_release as_of=2020-01-15 revision=1\n"
            )

            self.assertEqual(pointer_path.read_bytes(), pointer_before)
            _assert_no_sentinels(self, first.stdout + first.stderr + second.stdout + second.stderr)


class RejectionCliTests(unittest.TestCase):
    def test_duplicate_key_rejected_with_ordered_reason_and_no_echo(self) -> None:
        with fb.duplicate_key_within_list() as candidate, tempfile.TemporaryDirectory() as store_dir:
            _recompute_content_length(candidate.root)
            store_root = Path(store_dir)
            completed = _invoke_admit(candidate.root, store_root)

            self.assertEqual(completed.returncode, 1)

            document = json.loads(completed.stdout.decode("utf-8"))
            self.assertEqual(set(document), _RESULT_KEYS)
            self.assertEqual(document["status"], "rejected")
            self.assertEqual(document["release_revision"], None)
            self.assertEqual(document["revision_fingerprint"], None)
            reason_codes = [reason["code"] for reason in document["reasons"]]
            self.assertIn("registration.duplicate", reason_codes)
            for reason in document["reasons"]:
                self.assertEqual(set(reason), _REASON_KEYS)

            self.assertTrue(
                _normalize(completed.stderr).decode("utf-8").startswith("rejected reasons=")
            )

            self.assertFalse((store_root / "promoted-releases.json").exists())
            _assert_no_sentinels(
                self, completed.stdout + completed.stderr, extra=(str(candidate.root),)
            )

    def test_unchanged_pointer_when_later_admission_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as store_dir:
            store_root = Path(store_dir)
            accepted = _invoke_admit(BASELINE_CANDIDATE_ROOT, store_root)
            self.assertEqual(accepted.returncode, 0)
            pointer_before = (store_root / "promoted-releases.json").read_bytes()

            with fb.duplicate_key_within_list() as candidate:
                _recompute_content_length(candidate.root)
                rejected = _invoke_admit(candidate.root, store_root)
                self.assertEqual(rejected.returncode, 1)

            self.assertEqual(
                (store_root / "promoted-releases.json").read_bytes(), pointer_before
            )
            promoted = read_promoted_releases(store_root)
            self.assertEqual(len(promoted), 1)

    def test_xlsx_candidate_rejected_before_csv_parsing(self) -> None:
        with fb.mutated_candidate() as candidate, tempfile.TemporaryDirectory() as store_dir:
            manifest_path = candidate.manifest_path()
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
            xlsx_path = candidate.root / "charities-may-operate.xlsx"
            xlsx_path.write_bytes(candidate.csv_path("charities-may-operate").read_bytes())
            document["objects"]["charities-may-operate"]["relative_path"] = (
                "charities-may-operate.xlsx"
            )
            document["objects"]["charities-may-operate"]["content_length"] = (
                xlsx_path.stat().st_size
            )
            manifest_path.write_text(json.dumps(document), encoding="utf-8")

            store_root = Path(store_dir)
            completed = _invoke_admit(candidate.root, store_root)

            self.assertEqual(completed.returncode, 1)
            result_document = json.loads(completed.stdout.decode("utf-8"))
            self.assertEqual(result_document["status"], "rejected")
            reason_codes = [reason["code"] for reason in result_document["reasons"]]
            self.assertEqual(reason_codes, ["contract.unsupported_xlsx"])
            _assert_no_sentinels(self, completed.stdout + completed.stderr)


class OperationalErrorCliTests(unittest.TestCase):
    def test_store_inside_git_worktree_is_operational_error(self) -> None:
        store_inside_repo = REPO_ROOT / ".gitignore-exempt-tmp-store-for-cli-test"
        self.assertFalse(store_inside_repo.exists())
        try:
            completed = _invoke_admit(BASELINE_CANDIDATE_ROOT, store_inside_repo)

            self.assertEqual(completed.returncode, 3)
            document = json.loads(completed.stdout.decode("utf-8"))
            self.assertEqual(set(document), _RESULT_KEYS)
            self.assertEqual(document["status"], "operational_error")
            self.assertEqual(document["reasons"], [])
            self.assertEqual(_normalize(completed.stderr), b"operational_error reasons=0\n")
            _assert_no_sentinels(
                self, completed.stdout + completed.stderr, extra=(str(store_inside_repo),)
            )
        finally:
            if store_inside_repo.exists():
                store_inside_repo.rmdir()


class PackageImportSideEffectFreeTests(unittest.TestCase):
    def test_package_import_performs_no_filesystem_io(self) -> None:
        with tempfile.TemporaryDirectory() as import_cwd:
            env = _cli_env()
            env["PYTHONPATH"] = str(REPO_ROOT)
            completed = subprocess.run(
                [sys.executable, "-c", "import calico_landing; print('import-ok')"],
                cwd=import_cwd,
                env=env,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(_normalize(completed.stdout), b"import-ok\n")
            self.assertEqual(completed.stderr, b"")
            self.assertEqual(list(Path(import_cwd).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
