"""Offline replay proof for capture outcomes and atomic publication."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from calico_capture.orchestrator import capture
from calico_capture.status import CaptureStatus
from calico_dbt.runner import BuildOutcome
from calico_publish.allowlist import load_allowlist
from calico_publish.cli import main as publish_main
from calico_publish.export import StagedExport
from calico_publish.transaction import CARRIED_FORWARD_PATHS, publish_tree
from tests.capture.fakes import FakeArchive
from tests.capture.test_tracer import (
    _BuildSpy,
    _CURRENT_DATE_CLOCK_TIMESTAMP,
    _status_contract_compliant_candidate,
)
from tests.fixtures.landing.fixture_builder import wrong_header
from tests.fixtures.publish.fixture_builder import BASELINE_DIR, extra_unapproved_column


class _TransactionSpy:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1
        kwargs["repo_dir"] = self.repo
        return publish_tree(**kwargs)


class _ReplayHarness:
    """Drive the production workflow seams against one disposable Git remote."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.remote = root / "remote.git"
        self.repo = root / "repository"
        self.staging = root / "staging"
        self.publisher = _TransactionSpy(self.repo)
        self._git(root, "init", "--bare", str(self.remote))
        self._git(root, "init", "--initial-branch=main", str(self.repo))
        self._git(self.repo, "config", "user.name", "Replay Fixture")
        self._git(
            self.repo,
            "config",
            "user.email",
            "replay-fixture" + "@" + "example.invalid",
        )
        self._git(self.repo, "remote", "add", "origin", str(self.remote))

        for relative_path in (
            *CARRIED_FORWARD_PATHS,
            "calico_capture/replay_fixture.py",
            "contracts/replay-fixture.json",
            "tests/replay-fixture.txt",
        ):
            destination = self.repo / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("synthetic fixture\n", encoding="utf-8", newline="\n")
        self._git(self.repo, "add", *CARRIED_FORWARD_PATHS)
        self._git(
            self.repo,
            "add",
            "calico_capture/replay_fixture.py",
            "contracts/replay-fixture.json",
            "tests/replay-fixture.txt",
        )
        self._git(self.repo, "commit", "-m", "Seed replay parent")
        self._git(self.repo, "push", "origin", "HEAD:refs/heads/published-data")

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
        )
        if completed.returncode != 0:
            raise AssertionError("disposable Git operation failed")
        return completed.stdout.strip()

    def snapshot(self) -> tuple[str, tuple[str, ...]]:
        head = self._git(
            self.repo, "ls-remote", "origin", "refs/heads/published-data"
        ).split()[0]
        tree = tuple(
            self._git(self.repo, "ls-tree", "-r", "--full-tree", head).splitlines()
        )
        return head, tree

    def commit_count(self, earlier: str, later: str) -> int:
        return int(self._git(self.repo, "rev-list", "--count", f"{earlier}..{later}"))

    def run(
        self,
        status: CaptureStatus,
        *,
        publication: Path = BASELINE_DIR,
        add_privacy_finding: bool = False,
    ) -> tuple[int | None, dict[str, object] | None]:
        # This is the same accepted-only condition as the hosted workflow.
        if status.outcome != "accepted":
            return None, None

        if self.staging.exists():
            shutil.rmtree(self.staging)
        self.staging.mkdir()
        shutil.copy2(publication / "publication-exports-v1.json", self.staging)

        def runner(**kwargs):
            kwargs["export"](Path("synthetic.duckdb"))
            return BuildOutcome(status="success", category=None, proof=None)

        def exporter(_database, allowlist, root):
            destination_root = Path(root) / "exports"
            destination_root.mkdir(parents=True)
            staged: list[StagedExport] = []
            for entry in allowlist.exports:
                source = publication / "exports" / entry.file_name
                destination = destination_root / entry.file_name
                shutil.copy2(source, destination)
                if add_privacy_finding and entry.export_name == "fixture_named_history":
                    with destination.open(encoding="utf-8", newline="") as stream:
                        rows = list(csv.reader(stream))
                    display_name = rows[0].index("display_name")
                    rows[1][display_name] = "4210" + " Placeholder Ave"
                    with destination.open("w", encoding="utf-8", newline="") as stream:
                        csv.writer(stream, lineterminator="\n").writerows(rows)
                payload = destination.read_bytes()
                staged.append(
                    StagedExport(
                        export_name=entry.export_name,
                        file_name=entry.file_name,
                        relative_path=f"exports/{entry.file_name}",
                        sha256=hashlib.sha256(payload).hexdigest(),
                        row_count=max(payload.count(b"\n") - 1, 0),
                    )
                )
            return tuple(staged)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = publish_main(
                [
                    "publish",
                    "--mode",
                    "fixture",
                    "--staging",
                    str(self.staging),
                    "--remote",
                    "origin",
                    "--target-ref",
                    "published-data",
                ],
                build_runner=runner,
                exporter=exporter,
                transaction_publisher=self.publisher,
            )
        documents = [
            json.loads(line)
            for line in stdout.getvalue().splitlines()
            if line.startswith("{")
        ]
        return code, documents[-1] if documents else None


class PublicationReplayTests(unittest.TestCase):
    def _accepted(self) -> CaptureStatus:
        with _status_contract_compliant_candidate() as candidate:
            return capture(
                trigger="local",
                archive=FakeArchive(),
                fetch_candidate=lambda: candidate.root,
                build=_BuildSpy(),
            )

    def _assert_skipped_without_tree_change(self, status: CaptureStatus) -> None:
        with tempfile.TemporaryDirectory() as directory:
            replay = _ReplayHarness(Path(directory))
            before = replay.snapshot()
            code, document = replay.run(status)
            after = replay.snapshot()
        self.assertIsNone(code)
        self.assertIsNone(document)
        self.assertEqual(replay.publisher.calls, 0)
        self.assertEqual(after, before)

    def test_no_new_release_skips_publication_and_preserves_recursive_tree(self) -> None:
        with _status_contract_compliant_candidate() as candidate:
            archive = FakeArchive()
            first = capture(
                trigger="local",
                archive=archive,
                fetch_candidate=lambda: candidate.root,
                build=_BuildSpy(),
            )
            status = capture(
                trigger="local",
                archive=archive,
                fetch_candidate=lambda: candidate.root,
                build=_BuildSpy(),
                clock=lambda: _CURRENT_DATE_CLOCK_TIMESTAMP,
            )
        self.assertEqual(first.outcome, "accepted")
        self.assertEqual(status.outcome, "no_new_release")
        self._assert_skipped_without_tree_change(status)

    def test_rejected_skips_publication_with_closed_reason_and_preserves_tree(self) -> None:
        with wrong_header() as candidate:
            status = capture(
                trigger="local",
                archive=FakeArchive(),
                fetch_candidate=lambda: candidate.root,
                build=_BuildSpy(),
            )
        self.assertEqual(status.outcome, "rejected")
        self.assertEqual(status.reason_category, "structural_rejection")
        self._assert_skipped_without_tree_change(status)

    def test_operational_error_skips_publication_and_preserves_recursive_tree(self) -> None:
        with _status_contract_compliant_candidate() as candidate:
            archive = FakeArchive()
            archive.fail_all_writes()
            status = capture(
                trigger="local",
                archive=archive,
                fetch_candidate=lambda: candidate.root,
                build=_BuildSpy(),
            )
        self.assertEqual(status.outcome, "operational_error")
        self._assert_skipped_without_tree_change(status)

    def test_accepted_gate_failure_makes_zero_transaction_calls(self) -> None:
        status = self._accepted()
        with tempfile.TemporaryDirectory() as directory, extra_unapproved_column() as bad:
            replay = _ReplayHarness(Path(directory))
            before = replay.snapshot()
            code, _ = replay.run(status, publication=bad.root)
            after = replay.snapshot()
        self.assertNotEqual(code, 0)
        self.assertEqual(replay.publisher.calls, 0)
        self.assertEqual(after, before)

    def test_accepted_privacy_failure_makes_zero_transaction_calls(self) -> None:
        status = self._accepted()
        with tempfile.TemporaryDirectory() as directory:
            replay = _ReplayHarness(Path(directory))
            before = replay.snapshot()
            code, _ = replay.run(status, add_privacy_finding=True)
            after = replay.snapshot()
        self.assertNotEqual(code, 0)
        self.assertEqual(replay.publisher.calls, 0)
        self.assertEqual(after, before)

    def test_accepted_clean_publishes_exact_tree_once_then_no_change(self) -> None:
        status = self._accepted()
        with tempfile.TemporaryDirectory() as directory:
            replay = _ReplayHarness(Path(directory))
            before_head, _ = replay.snapshot()
            first_code, first_document = replay.run(status)
            first_snapshot = replay.snapshot()
            second_code, second_document = replay.run(status)
            second_snapshot = replay.snapshot()

            allowlist = load_allowlist(BASELINE_DIR / "publication-exports-v1.json")
            expected_paths = {
                *CARRIED_FORWARD_PATHS,
                *(f"exports/{entry.file_name}" for entry in allowlist.exports),
                "manifest/published-manifest-v1.json",
            }
            actual_paths = {line.split("\t", 1)[1] for line in first_snapshot[1]}

            self.assertEqual(replay.commit_count(before_head, first_snapshot[0]), 1)
        self.assertEqual(first_code, 0)
        self.assertEqual(first_document, {"category": "publish.published"})
        self.assertEqual(second_code, 0)
        self.assertEqual(second_document, {"category": "publish.no_change"})
        self.assertEqual(replay.publisher.calls, 2)
        self.assertEqual(actual_paths, expected_paths)
        self.assertEqual(second_snapshot, first_snapshot)


if __name__ == "__main__":
    unittest.main()
