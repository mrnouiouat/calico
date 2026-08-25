"""Non-echo regression suite for the tools.privacy_scan CLI.

Invokes the stable public command in a subprocess and asserts, at the byte
level across both stdout and stderr, that the synthetic secret and its
obvious case/encoded variants never appear -- for scanner findings, policy
errors, Git errors, and internal errors alike (D-10).

The CLI resolves the target Git repository from its own process working
directory (matching real local/CI usage: run it from inside the repo being
scanned), so each subprocess here is invoked with `cwd` set to the disposable
fixture repository and `PYTHONPATH` pointing at the product root so that
`python -m tools.privacy_scan` can still be imported.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SYNTHETIC_FEIN_MARKER = "94-" + "7654321"
SYNTHETIC_FEIN_VARIANTS = [
    SYNTHETIC_FEIN_MARKER,
    SYNTHETIC_FEIN_MARKER.replace("-", ""),
    SYNTHETIC_FEIN_MARKER.upper(),
    SYNTHETIC_FEIN_MARKER.lower(),
]

REPO_ROOT = Path(__file__).resolve().parents[3]


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )


class TempGitRepo:
    def __init__(self) -> None:
        self._dir: tempfile.TemporaryDirectory[str] | None = None
        self.path: Path | None = None

    def __enter__(self) -> "TempGitRepo":
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name)
        _run(["init", "--initial-branch=main"], self.path)
        _run(["config", "user.email", "synthetic" + "@example.invalid"], self.path)
        _run(["config", "user.name", "Synthetic Test User"], self.path)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._dir is not None:
            self._dir.cleanup()

    def write_file(self, relative_path: str, content: bytes) -> None:
        assert self.path is not None
        target = self.path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def add(self, relative_path: str) -> None:
        _run(["add", "--", relative_path], self.path)

    def commit(self, message: str) -> str:
        _run(["commit", "-m", message, "--allow-empty"], self.path)
        result = _run(["rev-parse", "HEAD"], self.path)
        return result.stdout.decode("ascii").strip()


def _write_default_policy(path: Path) -> None:
    payload = {
        "policy_version": 1,
        "max_blob_bytes": 1048576,
        "forbidden_paths": [
            {"kind": "prefix", "value": "data/raw/", "category": "raw_source_data"},
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _cli_env() -> dict[str, str]:
    env = {"PYTHONPATH": str(REPO_ROOT)}
    for key in ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP"):
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    return env


def _invoke_cli(repo_dir: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "tools.privacy_scan", *args],
        cwd=repo_dir,
        env=_cli_env(),
        capture_output=True,
        check=False,
    )


class TestNonEchoOnFinding(unittest.TestCase):
    def test_synthetic_fein_never_appears_in_output(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("notes.txt", f"FEIN on file: {SYNTHETIC_FEIN_MARKER}\n".encode("utf-8"))
            repo.add("notes.txt")
            commit = repo.commit("add notes")

            policy_path = repo.path / "policy.json"
            _write_default_policy(policy_path)

            completed = _invoke_cli(repo.path, ["--tree", commit, "--policy", str(policy_path)])

        combined = completed.stdout + completed.stderr
        for variant in SYNTHETIC_FEIN_VARIANTS:
            self.assertNotIn(variant.encode("utf-8"), combined)
        self.assertNotEqual(completed.returncode, 0)


class TestNonEchoOnPolicyError(unittest.TestCase):
    def test_invalid_policy_does_not_echo_bad_value(self) -> None:
        marker = "synthetic-marker-zzqx-9138"
        with TempGitRepo() as repo:
            repo.write_file("notes.txt", b"hello\n")
            repo.add("notes.txt")
            commit = repo.commit("add notes")

            bad_policy = {
                "policy_version": 1,
                "max_blob_bytes": 1048576,
                "forbidden_paths": [{"kind": "regex", "value": marker, "category": "raw_source_data"}],
            }
            policy_path = repo.path / "policy.json"
            policy_path.write_text(json.dumps(bad_policy), encoding="utf-8")

            completed = _invoke_cli(repo.path, ["--tree", commit, "--policy", str(policy_path)])

        combined = completed.stdout + completed.stderr
        self.assertNotIn(marker.encode("utf-8"), combined)
        self.assertNotEqual(completed.returncode, 0)


class TestNonEchoOnGitError(unittest.TestCase):
    def test_invalid_treeish_does_not_echo_marker(self) -> None:
        marker = "synthetic-marker-zzqx-9138"
        with TempGitRepo() as repo:
            repo.write_file("notes.txt", b"hello\n")
            repo.add("notes.txt")
            repo.commit("add notes")

            policy_path = repo.path / "policy.json"
            _write_default_policy(policy_path)

            completed = _invoke_cli(
                repo.path,
                ["--tree", "not-a-real-treeish-" + marker, "--policy", str(policy_path)],
            )

        combined = completed.stdout + completed.stderr
        self.assertNotIn(marker.encode("utf-8"), combined)
        self.assertNotEqual(completed.returncode, 0)


class TestNonEchoOnInternalError(unittest.TestCase):
    def test_missing_policy_file_does_not_echo_marker_path(self) -> None:
        marker = "synthetic-marker-zzqx-9138"
        with TempGitRepo() as repo:
            repo.write_file("notes.txt", b"hello\n")
            repo.add("notes.txt")
            commit = repo.commit("add notes")

            missing_policy_path = repo.path / f"does-not-exist-{marker}.json"

            completed = _invoke_cli(
                repo.path,
                ["--tree", commit, "--policy", str(missing_policy_path)],
            )

        combined = completed.stdout + completed.stderr
        self.assertNotIn(marker.encode("utf-8"), combined)
        self.assertNotEqual(completed.returncode, 0)


class TestCleanTreeExitsZero(unittest.TestCase):
    def test_clean_tree_exits_zero(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("notes.txt", b"Organization: Synthetic Placeholder Charity Fund\n")
            repo.add("notes.txt")
            commit = repo.commit("add notes")

            policy_path = repo.path / "policy.json"
            _write_default_policy(policy_path)

            completed = _invoke_cli(repo.path, ["--tree", commit, "--policy", str(policy_path)])

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, b"")
        self.assertEqual(completed.stderr, b"")


if __name__ == "__main__":
    unittest.main()
