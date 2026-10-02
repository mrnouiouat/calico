"""Offline replay proof for capture outcomes and atomic publication."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
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

_WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github/workflows/capture-current.yml"


def _published_workflow_expression() -> str:
    lines = _WORKFLOW_PATH.read_text(encoding="utf-8").splitlines()
    publish_index = lines.index("  publish:")
    condition = next(
        line.strip() for line in lines[publish_index + 1 :] if line.strip().startswith("if:")
    )
    return condition.removeprefix("if: ${{ ").removesuffix(" }}")


def _evaluate_published_workflow_expression(
    *,
    mode: str,
    should_run: str,
    capture_result: str,
    status_result: str,
    status_json: str,
    cancelled: bool = False,
    calendar_result: str = "success",
) -> bool:
    """Evaluate the committed publish condition, rather than duplicating it."""

    expression = _published_workflow_expression()
    if not re.fullmatch(r"[A-Za-z0-9_.'()=!&| ${}\-]+", expression):
        raise AssertionError("workflow condition left the supported closed grammar")
    translated = expression.replace(
        "fromJSON(needs.capture.outputs.status_json).outcome",
        "_outcome(status_json)",
    )
    for reference, variable in (
        ("needs.calendar-gate.outputs.should_run", "should_run"),
        ("needs.calendar-gate.outputs.mode", "mode"),
        ("needs.calendar-gate.result", "calendar_result"),
        ("needs.capture.outputs.status_json", "status_json"),
        ("needs.capture.result", "capture_result"),
        ("needs.status.result", "status_result"),
    ):
        translated = translated.replace(reference, variable)
    translated = translated.replace("!cancelled()", "not cancelled")
    translated = translated.replace("&&", "and").replace("||", "or")

    def _outcome(document: str) -> object:
        parsed = json.loads(document)
        return parsed.get("outcome") if isinstance(parsed, dict) else None

    return bool(
        eval(
            translated,
            {"__builtins__": {}, "_outcome": _outcome},
            {
                "mode": mode,
                "should_run": should_run,
                "capture_result": capture_result,
                "status_result": status_result,
                "status_json": status_json,
                "cancelled": cancelled,
                "calendar_result": calendar_result,
            },
        )
    )


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
        mode: str = "capture",
        capture_result: str = "success",
        status_result: str = "success",
    ) -> tuple[int | None, dict[str, object] | None]:
        if not _evaluate_published_workflow_expression(
            mode=mode,
            should_run="true",
            capture_result=capture_result,
            status_result=status_result,
            status_json=status.to_json(),
        ):
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
    def test_republish_literal_expression_rejects_every_failed_dependency_or_cancellation(self):
        import itertools
        states = ("success", "skipped", "failure", "cancelled")
        for calendar, captured, status, cancelled in itertools.product(states, states, states, (False, True)):
            with self.subTest(calendar=calendar, captured=captured, status=status, cancelled=cancelled):
                self.assertEqual(_evaluate_published_workflow_expression(
                    mode="republish", should_run="true", calendar_result=calendar,
                    capture_result=captured, status_result=status, status_json="", cancelled=cancelled),
                    calendar == "success" and captured == status == "skipped" and not cancelled)

    def test_capture_literal_expression_requires_accepted_and_successful_dependencies(self):
        import itertools
        states = ("success", "skipped", "failure", "cancelled")
        for calendar, captured, status, outcome in itertools.product(states, states, states,
                ("accepted", "no_new_release", "rejected", "operational_error", "")):
            with self.subTest(calendar=calendar, captured=captured, status=status, outcome=outcome):
                self.assertEqual(_evaluate_published_workflow_expression(
                    mode="capture", should_run="true", calendar_result=calendar,
                    capture_result=captured, status_result=status,
                    status_json=json.dumps({"outcome": outcome}) if outcome else ""),
                    calendar == captured == status == "success" and outcome == "accepted")

    def test_republish_actual_real_cli_blocks_missing_or_corrupt_private_policy(self):
        from tests.capture.test_restore import archived_catalog_fixture
        from calico_capture.private_policy import private_policy_manifest_key
        from calico_capture.status import project_safe_status
        from calico_publish import cli
        for kind in ("missing", "corrupt"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                source = root / "source"
                source.mkdir()
                archive, catalog, binding = archived_catalog_fixture(source)
                manifest_key = private_policy_manifest_key(binding[0])
                if kind == "missing":
                    del archive._versions[manifest_key]
                else:
                    archive.set_read_override(manifest_key, b"invalid private policy")
                store = root / "store"
                store.mkdir()
                status = project_safe_status(trigger="local", outcome="no_new_release",
                    reason_category="source_not_advanced", started_at_utc="2032-01-01T00:00:00Z",
                    ended_at_utc="2032-01-01T00:00:01Z").to_dict()
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(cli, "load_prior_publication_binding", return_value=binding), \
                     patch.object(cli, "build", side_effect=AssertionError("build must not start")) as build, \
                     patch.object(cli, "export_all", side_effect=AssertionError("export must not start")) as export, \
                     patch.object(cli, "publish_tree", side_effect=AssertionError("transaction must not start")) as transaction, \
                     redirect_stdout(stdout), redirect_stderr(stderr):
                    code = publish_main(["publish", "--mode", "real", "--store", str(store),
                        "--staging", str(root / "staging"), "--remote", "origin", "--target-ref", "published-data"],
                        archive_factory=lambda: archive, catalog_loader=lambda: catalog,
                        control_loader=lambda **kwargs: status)
                self.assertEqual(code, 1)
                expected = "preflight.public_eligibility_" + ("missing" if kind == "missing" else "invalid")
                self.assertEqual(json.loads(stdout.getvalue()), {"category": expected})
                self.assertEqual(stderr.getvalue(), expected + "\n")
                build.assert_not_called()
                export.assert_not_called()
                transaction.assert_not_called()
                self.assertFalse((store / "public-eligibility-v1.json").exists())
                self.assertNotIn("synthetic-private-key", stdout.getvalue() + stderr.getvalue())

    def test_cold_accepted_capture_restores_full_catalog_and_policy_through_production_preflight(self):
        from tests.capture.test_restore import archived_catalog_fixture
        from calico_dbt.catalog import build_catalog_from_manifests
        from calico_dbt.preflight import prepare_runtime_input
        calls = []
        with tempfile.TemporaryDirectory() as temporary, _status_contract_compliant_candidate() as candidate:
            root = Path(temporary).resolve()
            source = root / "source"
            source.mkdir()
            archive, catalog, binding = archived_catalog_fixture(source)
            def check_store(store, selected_catalog):
                with tempfile.TemporaryDirectory() as preflight:
                    bound = prepare_runtime_input(store_root=store, catalog=selected_catalog,
                        temp_root=Path(preflight).resolve(), require_eligibility_sidecar=True)
                return bound.verified_release_count
            def fetch():
                # Locate the caller-owned store through the restore boundary's
                # observable argument recorded below; no restore function is mocked.
                self.assertEqual(check_store(restored_roots[0], catalog), 3)
                calls.append("fetch_after_full_restore")
                return candidate.root
            def build(store):
                manifests = []
                for path in sorted((store / "releases").glob("*/rev-*/manifest.json")):
                    raw = path.read_bytes()
                    document = json.loads(raw)
                    manifests.append((document["as_of_date"], document["release_revision"],
                                      document["revision_fingerprint"], raw))
                # The admitted synthetic revision extends only this test catalog.
                # SQL receives the same verified input/preflight shape as real mode.
                selected_catalog = build_catalog_from_manifests(manifests)
                self.assertEqual(check_store(store, selected_catalog), 4)
                calls.append("production_preflight")
                return BuildOutcome(status="success", category=None, proof=None)
            from calico_capture.restore import restore_catalog_with_private_policy
            restored_roots = []
            def observe(*args, **kwargs):
                result = restore_catalog_with_private_policy(*args, **kwargs)
                restored_roots.append(args[1])
                return result
            with patch("calico_capture.restore._default_catalog_loader", return_value=catalog), \
                 patch("calico_capture.restore.load_prior_publication_binding", return_value=binding), \
                 patch("calico_capture.restore.restore_catalog_with_private_policy", side_effect=observe):
                status = capture(trigger="local", archive=archive, fetch_candidate=fetch,
                                 build=build, sleeper=lambda seconds: None)
        self.assertEqual(status.outcome, "accepted")
        self.assertEqual(calls, ["fetch_after_full_restore", "production_preflight"])
        self.assertTrue(_evaluate_published_workflow_expression(mode="capture", should_run="true",
            capture_result="success", status_result="success", status_json=status.to_json()))
        self.assertNotIn("synthetic-private-key", status.to_json())

    def test_committed_workflow_expression_routes_every_dependency_state(self) -> None:
        cases = (
            ("republish", "true", "skipped", "skipped", "", False, True),
            ("republish", "true", "success", "skipped", "", False, False),
            ("republish", "true", "skipped", "success", "", False, False),
            ("capture", "true", "success", "success", "accepted", False, True),
            ("capture", "true", "success", "failure", "accepted", False, False),
            ("capture", "true", "failure", "success", "accepted", False, False),
            ("capture", "true", "success", "success", "rejected", False, False),
            ("capture", "true", "success", "success", "no_new_release", False, False),
            ("capture", "true", "success", "success", "operational_error", False, False),
            ("capture", "true", "skipped", "success", "accepted", False, False),
            ("capture", "true", "success", "skipped", "accepted", False, False),
            ("capture", "true", "cancelled", "success", "accepted", False, False),
            ("republish", "true", "skipped", "skipped", "", True, False),
            ("republish", "true", "failure", "skipped", "", False, False),
            ("capture", "false", "success", "success", "accepted", False, False),
            ("capture", "true", "success", "success", "accepted", True, False),
        )
        for mode, should_run, capture_result, status_result, outcome, cancelled, expected in cases:
            with self.subTest(
                mode=mode,
                capture_result=capture_result,
                status_result=status_result,
                outcome=outcome,
            ):
                status_json = "" if not outcome else json.dumps({"outcome": outcome})
                self.assertEqual(
                    _evaluate_published_workflow_expression(
                        mode=mode,
                        should_run=should_run,
                        capture_result=capture_result,
                        status_result=status_result,
                        status_json=status_json,
                        cancelled=cancelled,
                    ),
                    expected,
                )

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
            replay = _ReplayHarness(Path(directory).resolve())
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
            from tests.capture.test_orchestrator import DefaultRestoreDiscoveryTests
            helper = DefaultRestoreDiscoveryTests()
            try:
                helper.prepare_archive(archive)
                status = capture(
                    trigger="local", archive=archive, fetch_candidate=lambda: candidate.root,
                    build=_BuildSpy(), clock=lambda: _CURRENT_DATE_CLOCK_TIMESTAMP)
            finally:
                helper.doCleanups()
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
            replay = _ReplayHarness(Path(directory).resolve())
            before = replay.snapshot()
            code, _ = replay.run(status, publication=bad.root)
            after = replay.snapshot()
        self.assertNotEqual(code, 0)
        self.assertEqual(replay.publisher.calls, 0)
        self.assertEqual(after, before)

    def test_accepted_privacy_failure_makes_zero_transaction_calls(self) -> None:
        status = self._accepted()
        with tempfile.TemporaryDirectory() as directory:
            replay = _ReplayHarness(Path(directory).resolve())
            before = replay.snapshot()
            code, _ = replay.run(status, add_privacy_finding=True)
            after = replay.snapshot()
        self.assertNotEqual(code, 0)
        self.assertEqual(replay.publisher.calls, 0)
        self.assertEqual(after, before)

    def test_accepted_clean_publishes_exact_tree_once_then_no_change(self) -> None:
        status = self._accepted()
        with tempfile.TemporaryDirectory() as directory:
            replay = _ReplayHarness(Path(directory).resolve())
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
