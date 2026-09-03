"""Non-echoing operator/hosted CLI contract tests
(06-06-PLAN.md Task 1; T-06-06B/D).

Every command routes to an already-tested service; these tests exercise
that wiring plus the non-echo boundary. Private inputs (B2 credentials) are
never supplied via `argv` and are proven absent from every printed
document, even when a caught exception's construction path touched them.
No test contacts the live source, the live Backblaze service, or any
private archive -- `FakeArchive`, hand-built `InputCatalog`/
`CatalogReleaseAnchor` records, and small in-memory fakes stand in for
every external boundary. `B2Archive.authorize` and `b2sdk.v3.B2Api.
authorize_account` are mocked (never really called) for the two default-
credential non-echo proofs.

Run:
    py -V:3.13 -m unittest tests.capture.test_cli -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import calico_capture.cli as cli
from calico_capture.archive import ArchiveError, synchronize_verified_transaction
from calico_capture.b2 import B2Archive
from calico_capture.restore import RestoreError
from calico_capture.status import CaptureStatus, project_safe_status
from calico_dbt.catalog import CatalogReleaseAnchor, InputCatalog, build_catalog_from_manifests
from calico_landing.admission import admit
from tests.capture.fakes import FakeArchive
from tests.fixtures.landing.fixture_builder import mutated_candidate


def _admit_baseline_into_fresh_store() -> tuple[Path, "object", tempfile.TemporaryDirectory]:
    store_tmp = tempfile.TemporaryDirectory(prefix="calico-cli-test-store-")
    store_root = Path(store_tmp.name).resolve()
    with mutated_candidate() as candidate:
        result = admit(candidate.root, store_root)
    assert result.status == "accepted", result.status
    return store_root, result, store_tmp


def _manifest_bytes_for(store_root: Path, result) -> bytes:
    manifest_path = (
        store_root
        / "releases"
        / result.as_of_date
        / f"rev-{result.release_revision:04d}-{result.revision_fingerprint[:8]}"
        / "manifest.json"
    )
    return manifest_path.read_bytes()


def _catalog_for(store_root: Path, result) -> InputCatalog:
    manifest_bytes = _manifest_bytes_for(store_root, result)
    return build_catalog_from_manifests(
        [(result.as_of_date, result.release_revision, result.revision_fingerprint, manifest_bytes)]
    )


class _FakeBuildOutcome:
    def __init__(self, *, succeeded: bool) -> None:
        self.succeeded = succeeded


class _FakeRetentionState:
    def __init__(self, *, lifecycle_rules, is_file_lock_enabled) -> None:
        self.lifecycle_rules = lifecycle_rules
        self.is_file_lock_enabled = is_file_lock_enabled


class _FakeRetentionBucket:
    def __init__(self, state: _FakeRetentionState) -> None:
        self._state = state

    def get_fresh_state(self) -> _FakeRetentionState:
        return self._state


def _run_capture_cli(argv: list[str]) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = cli.main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


class ParserContractTests(unittest.TestCase):
    def test_every_command_is_registered(self) -> None:
        parser = cli._build_parser()
        subparser_actions = [
            action
            for action in parser._actions  # noqa: SLF001 - inspecting argparse structure only
            if action.dest == "command"
        ]
        self.assertEqual(len(subparser_actions), 1)
        self.assertEqual(
            set(subparser_actions[0].choices.keys()),
            {"run", "attest", "seed", "restore-build", "inspect-retention", "audit-hosted-output"},
        )

    def test_run_trigger_is_closed_vocabulary(self) -> None:
        parser = cli._build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["run", "--trigger", "not-a-real-trigger"])

    def test_run_requires_trigger(self) -> None:
        parser = cli._build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["run"])


class RunCommandTests(unittest.TestCase):
    def test_run_delegates_to_capture_with_the_injected_archive(self) -> None:
        fake_archive = FakeArchive()
        expected_status = project_safe_status(
            trigger="local",
            outcome="accepted",
            reason_category="none",
            started_at_utc="2026-09-02T17:17:00Z",
            ended_at_utc="2026-09-02T17:18:00Z",
            last_accepted_as_of_date="2026-09-02",
            last_accepted_release_revision=1,
        )
        captured_kwargs = {}

        def _fake_capture(**kwargs):
            captured_kwargs.update(kwargs)
            return expected_status

        with mock.patch.object(cli, "capture", _fake_capture):
            status = cli._run_capture("local", archive_factory=lambda: fake_archive)

        self.assertIs(status, expected_status)
        self.assertEqual(captured_kwargs["trigger"], "local")
        self.assertIs(captured_kwargs["archive"], fake_archive)

    def test_run_archive_construction_failure_never_calls_capture(self) -> None:
        called = []

        def _fake_capture(**kwargs):  # pragma: no cover - must never run
            called.append(kwargs)
            raise AssertionError("capture() must not be called when archive construction fails")

        def _failing_factory():
            raise ArchiveError("archive.scope_rejected")

        with mock.patch.object(cli, "capture", _fake_capture):
            status = cli._run_capture("schedule", archive_factory=_failing_factory)

        self.assertEqual(called, [])
        self.assertIsInstance(status, CaptureStatus)
        self.assertEqual(status.outcome, "operational_error")
        self.assertEqual(status.reason_category, "archive_error")
        self.assertEqual(status.trigger, "schedule")

    def test_cmd_run_prints_only_validated_status_json_and_maps_exit_code(self) -> None:
        exit_code, out, err = _run_capture_cli(["run", "--trigger", "local"])
        # No credentials configured in this test process -> archive
        # construction fails closed before capture() is ever invoked.
        self.assertEqual(exit_code, 3)
        document = json.loads(out)
        self.assertEqual(document["outcome"], "operational_error")
        self.assertEqual(document["reason_category"], "archive_error")
        self.assertIn("operational_error", err)

    def test_run_default_factory_never_echoes_credential_on_authorization_failure(self) -> None:
        sentinel = "sentinel-application-key-9f3d2a"
        env = {cli.AUTOMATION_KEY_ID_ENV: sentinel, cli.AUTOMATION_KEY_ENV: sentinel}
        with mock.patch.dict(os.environ, env):
            with mock.patch.object(
                B2Archive, "authorize", side_effect=ArchiveError("archive.authorization_failed")
            ):
                exit_code, out, err = _run_capture_cli(["run", "--trigger", "local"])
        self.assertEqual(exit_code, 3)
        combined = out + err
        self.assertNotIn(sentinel, combined)
        document = json.loads(out)
        self.assertEqual(document["reason_category"], "archive_error")


class AttestCommandTests(unittest.TestCase):
    def test_attest_missing_credentials_fails_closed(self) -> None:
        document, exit_code = cli._attest(archive_factory=cli._default_automation_archive_factory)
        self.assertEqual(exit_code, 1)
        self.assertEqual(document["category"], cli._CREDENTIAL_MISSING_CATEGORY)

    def test_attest_reports_safe_scope_fields_on_success(self) -> None:
        # `_attest` requires a real `B2Archive` instance to read `.scope`
        # off of (a plain `FakeArchive` has no such attribute) -- subclass
        # the real class with a stub constructor/property so no live B2
        # authorization ever runs.
        class _StubScope:
            bucket_name = "RegistryData"
            name_prefix = "archive/v1/"
            capabilities = frozenset({"listFiles", "readFiles", "writeFiles"})

        class _StubB2Archive(B2Archive):
            def __init__(self) -> None:  # noqa: SLF001 - test double, no super().__init__ needed
                pass

            @property
            def scope(self):
                return _StubScope()

        document, exit_code = cli._attest(archive_factory=lambda: _StubB2Archive())
        self.assertEqual(exit_code, 0)
        self.assertEqual(document["category"], "attest.scope_verified")
        self.assertEqual(document["bucket_name"], "RegistryData")
        self.assertEqual(document["name_prefix"], "archive/v1/")
        self.assertEqual(document["capability_count"], 3)

    def test_attest_never_echoes_credential_on_authorization_failure(self) -> None:
        sentinel = "sentinel-application-key-attest-77"
        env = {cli.AUTOMATION_KEY_ID_ENV: sentinel, cli.AUTOMATION_KEY_ENV: sentinel}
        with mock.patch.dict(os.environ, env):
            with mock.patch.object(
                B2Archive, "authorize", side_effect=ArchiveError("archive.scope_rejected")
            ):
                exit_code, out, err = _run_capture_cli(["attest"])
        self.assertEqual(exit_code, 1)
        combined = out + err
        self.assertNotIn(sentinel, combined)


class SeedCommandTests(unittest.TestCase):
    def test_seed_rejects_a_store_inside_a_git_worktree(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        document, exit_code = cli._seed(
            repo_root,
            archive_factory=lambda: FakeArchive(),
            catalog_loader=lambda: InputCatalog(contract_version=1, releases=()),
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(document["category"], "seed.invalid_store")

    def test_seed_synchronizes_every_locally_present_verified_release(self) -> None:
        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            catalog = _catalog_for(store_root, result)
            archive = FakeArchive()
            document, exit_code = cli._seed(
                store_root, archive_factory=lambda: archive, catalog_loader=lambda: catalog
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(document["category"], "seed.completed")
            self.assertEqual(document["verified_release_count"], 1)
            self.assertEqual(document["synchronized_release_count"], 1)
            self.assertEqual(document["skipped_release_count"], 0)
            self.assertTrue(archive.all_keys())
        finally:
            store_tmp.cleanup()

    def test_seed_skips_a_catalog_release_with_no_local_manifest(self) -> None:
        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            present_anchor = _catalog_for(store_root, result).releases[0]
            absent_anchor = CatalogReleaseAnchor(
                as_of_date="2099-01-06",
                release_revision=1,
                revision_fingerprint="0" * 64,
                revision_manifest_sha256="0" * 64,
            )
            catalog = InputCatalog(contract_version=1, releases=(present_anchor, absent_anchor))
            archive = FakeArchive()
            document, exit_code = cli._seed(
                store_root, archive_factory=lambda: archive, catalog_loader=lambda: catalog
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(document["verified_release_count"], 1)
            self.assertEqual(document["synchronized_release_count"], 1)
            self.assertEqual(document["skipped_release_count"], 1)
        finally:
            store_tmp.cleanup()

    def test_seed_fails_closed_on_local_manifest_hash_mismatch(self) -> None:
        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            tampered_anchor = CatalogReleaseAnchor(
                as_of_date=result.as_of_date,
                release_revision=result.release_revision,
                revision_fingerprint=result.revision_fingerprint,
                revision_manifest_sha256="f" * 64,  # deliberately wrong
            )
            catalog = InputCatalog(contract_version=1, releases=(tampered_anchor,))
            archive = FakeArchive()
            document, exit_code = cli._seed(
                store_root, archive_factory=lambda: archive, catalog_loader=lambda: catalog
            )
            self.assertEqual(exit_code, 1)
            self.assertEqual(document["category"], "catalog.manifest_hash_mismatch")
            # Never echoes the local store path.
            self.assertNotIn(str(store_root), json.dumps(document))
        finally:
            store_tmp.cleanup()


class RestoreBuildCommandTests(unittest.TestCase):
    def test_restore_build_rejects_a_store_inside_a_git_worktree(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        document, exit_code = cli._restore_build(
            repo_root,
            archive_factory=lambda: FakeArchive(),
            catalog_loader=lambda: InputCatalog(contract_version=1, releases=()),
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(document["category"], "restore_build.invalid_store")

    def test_restore_build_rejects_an_empty_catalog(self) -> None:
        with tempfile.TemporaryDirectory(prefix="calico-cli-restore-build-") as tmp:
            document, exit_code = cli._restore_build(
                Path(tmp).resolve(),
                archive_factory=lambda: FakeArchive(),
                catalog_loader=lambda: InputCatalog(contract_version=1, releases=()),
            )
        self.assertEqual(exit_code, 1)
        self.assertEqual(document["category"], "restore_build.empty_catalog")

    def test_restore_build_restores_and_runs_the_real_build_only_on_the_final_anchor(self) -> None:
        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            archive = FakeArchive()
            synchronize_verified_transaction(archive, store_root, result)
            catalog = _catalog_for(store_root, result)

            build_calls: list[Path] = []

            def _final_build(root: Path) -> _FakeBuildOutcome:
                build_calls.append(root)
                return _FakeBuildOutcome(succeeded=True)

            with tempfile.TemporaryDirectory(prefix="calico-cli-restore-build-dest-") as dest_tmp:
                dest_root = Path(dest_tmp).resolve()
                document, exit_code = cli._restore_build(
                    dest_root,
                    archive_factory=lambda: archive,
                    catalog_loader=lambda: catalog,
                    final_build=_final_build,
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(document["category"], "restore_build.completed")
            self.assertEqual(document["restored_transaction_count"], 1)
            self.assertGreater(document["object_count"], 0)
            self.assertEqual(len(build_calls), 1)
        finally:
            store_tmp.cleanup()

    def test_restore_build_fails_closed_when_the_final_build_fails(self) -> None:
        store_root, result, store_tmp = _admit_baseline_into_fresh_store()
        try:
            archive = FakeArchive()
            synchronize_verified_transaction(archive, store_root, result)
            catalog = _catalog_for(store_root, result)

            with tempfile.TemporaryDirectory(prefix="calico-cli-restore-build-dest-") as dest_tmp:
                dest_root = Path(dest_tmp).resolve()
                document, exit_code = cli._restore_build(
                    dest_root,
                    archive_factory=lambda: archive,
                    catalog_loader=lambda: catalog,
                    final_build=lambda root: _FakeBuildOutcome(succeeded=False),
                )
            self.assertEqual(exit_code, 1)
            self.assertEqual(document["category"], "restore.build_failed")
        finally:
            store_tmp.cleanup()


class InspectRetentionCommandTests(unittest.TestCase):
    def test_inspect_retention_missing_credentials_fails_closed(self) -> None:
        document, exit_code = cli._inspect_retention(
            session_factory=cli._default_retention_session_factory
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(document["category"], cli._CREDENTIAL_MISSING_CATEGORY)

    def test_inspect_retention_reports_closed_safe_categories(self) -> None:
        state = _FakeRetentionState(
            lifecycle_rules=[{"fileNamePrefix": "archive/v1/", "daysFromHidingToDeleting": 30}],
            is_file_lock_enabled=False,
        )
        document, exit_code = cli._inspect_retention(
            session_factory=lambda: _FakeRetentionBucket(state)
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(document["lifecycle_category"], "lifecycle_archive_deletion_rule_present")
        self.assertEqual(document["object_lock_category"], "object_lock_disabled")

    def test_inspect_retention_never_echoes_credential_on_authorization_failure(self) -> None:
        from b2sdk.v3 import B2Api
        from b2sdk.v3.exception import B2Error

        sentinel = "sentinel-retention-key-42"
        env = {cli.RETENTION_KEY_ID_ENV: sentinel, cli.RETENTION_KEY_ENV: sentinel}
        with mock.patch.dict(os.environ, env):
            with mock.patch.object(B2Api, "authorize_account", side_effect=B2Error()):
                exit_code, out, err = _run_capture_cli(["inspect-retention"])
        self.assertEqual(exit_code, 1)
        combined = out + err
        self.assertNotIn(sentinel, combined)


class AuditHostedOutputCommandTests(unittest.TestCase):
    def _write(self, tmp_dir: Path, name: str, content: str) -> str:
        path = tmp_dir / name
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_audit_rejects_a_malformed_or_extra_field_status_document(self) -> None:
        with tempfile.TemporaryDirectory(prefix="calico-cli-audit-") as tmp:
            tmp_path = Path(tmp)
            status_path = self._write(
                tmp_path, "status.json", json.dumps({"schema_version": 1, "extra_field": True})
            )
            log_path = self._write(tmp_path, "log.txt", "calendar-gate skipped\ncapture completed\n")
            document, exit_code = cli._audit_hosted_output(log_path, status_path, None)
        self.assertEqual(exit_code, 1)
        self.assertEqual(document["category"], "audit.status_invalid")

    def test_audit_rejects_non_allowlisted_log_content(self) -> None:
        status = project_safe_status(
            trigger="workflow_dispatch",
            outcome="no_new_release",
            reason_category="source_not_advanced",
            started_at_utc="2026-09-02T17:17:00Z",
            ended_at_utc="2026-09-02T17:18:00Z",
        )
        with tempfile.TemporaryDirectory(prefix="calico-cli-audit-") as tmp:
            tmp_path = Path(tmp)
            status_path = self._write(tmp_path, "status.json", status.to_json())
            log_path = self._write(
                tmp_path, "log.txt", "Traceback (most recent call last):\nsomething failed\n"
            )
            document, exit_code = cli._audit_hosted_output(log_path, status_path, None)
        self.assertEqual(exit_code, 1)
        self.assertEqual(document["category"], "audit.log_forbidden_content")

    def test_audit_rejects_a_leaked_credential_sentinel(self) -> None:
        status = project_safe_status(
            trigger="schedule",
            outcome="accepted",
            reason_category="none",
            started_at_utc="2026-09-02T17:17:00Z",
            ended_at_utc="2026-09-02T17:18:00Z",
            last_accepted_as_of_date="2026-09-02",
            last_accepted_release_revision=1,
        )
        sentinel = "sentinel-hosted-audit-leak-55"
        with tempfile.TemporaryDirectory(prefix="calico-cli-audit-") as tmp:
            tmp_path = Path(tmp)
            status_path = self._write(tmp_path, "status.json", status.to_json())
            log_path = self._write(tmp_path, "log.txt", f"capture completed value={sentinel}\n")
            with mock.patch.dict(os.environ, {"CALICO_TEST_SENTINEL_ENV": sentinel}):
                document, exit_code = cli._audit_hosted_output(
                    log_path, status_path, "CALICO_TEST_SENTINEL_ENV"
                )
        self.assertEqual(exit_code, 1)
        self.assertEqual(document["category"], "audit.credential_leak_detected")

    def test_audit_passes_a_clean_status_and_log(self) -> None:
        status = project_safe_status(
            trigger="schedule",
            outcome="accepted",
            reason_category="none",
            started_at_utc="2026-09-02T17:17:00Z",
            ended_at_utc="2026-09-02T17:18:00Z",
            last_accepted_as_of_date="2026-09-02",
            last_accepted_release_revision=1,
        )
        with tempfile.TemporaryDirectory(prefix="calico-cli-audit-") as tmp:
            tmp_path = Path(tmp)
            status_path = self._write(tmp_path, "status.json", status.to_json())
            log_path = self._write(tmp_path, "log.txt", "calendar-gate ran\ncapture completed\nstatus updated\n")
            document, exit_code = cli._audit_hosted_output(log_path, status_path, None)
        self.assertEqual(exit_code, 0)
        self.assertEqual(document["category"], "audit.pass")

    def test_audit_reports_missing_files_without_echoing_the_path(self) -> None:
        document, exit_code = cli._audit_hosted_output(
            "calico-cli-test-does-not-exist/log.txt",
            "calico-cli-test-does-not-exist/status.json",
            None,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(document["category"], "audit.file_not_found")
        self.assertNotIn("does-not-exist", json.dumps(document))


class MainDispatchTests(unittest.TestCase):
    def test_unknown_command_exits_nonzero(self) -> None:
        with self.assertRaises(SystemExit):
            cli.main(["not-a-real-command"])

    def test_no_command_exits_nonzero(self) -> None:
        with self.assertRaises(SystemExit):
            cli.main([])


if __name__ == "__main__":
    unittest.main()
