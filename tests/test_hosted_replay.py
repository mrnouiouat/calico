"""Behavior contracts for isolated actual-SQL replay evidence."""

import importlib.util
import copy
import io
import json
import os
import tempfile
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr
import unittest

_CHECKPOINTS = {}
_TEST_ENVIRONMENT = None


def setUpModule():
    global _TEST_ENVIRONMENT
    # Verification explicitly removes workstation adapters; it never uses them.
    _TEST_ENVIRONMENT = patch.dict(os.environ)
    _TEST_ENVIRONMENT.start()
    for name in tuple(os.environ):
        if name.startswith(("CALICO_B2_", "B2_", "AWS_", "AZURE_", "GOOGLE_APPLICATION_")) or name in {
            "GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "GIT_SSH", "GIT_SSH_COMMAND", "GIT_ASKPASS",
            "SSH_ASKPASS", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_DIR", "GIT_WORK_TREE",
            "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_INDEX_FILE",
            "GIT_TEMPLATE_DIR", "GIT_EXEC_PATH"}:
            os.environ.pop(name, None)
    os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    os.environ["TMPDIR"] = str(Path(tempfile.gettempdir()).resolve())
    os.environ["RUNNER_TEMP"] = os.environ["TMPDIR"]


def tearDownModule():
    _TEST_ENVIRONMENT.stop()


def _run_tuple():
    from tools import hosted_replay as replay
    return replay.ReplayRunTuple("fixture/replay", 1, 1, "a" * 40)


def _checkpoint(name):
    from tools import hosted_replay as replay
    if name not in _CHECKPOINTS:
        parent = Path(tempfile.gettempdir()).resolve()
        if name == "published":
            prepared = _checkpoint("accepted")
            value = replay.run_publication_worker(runner_temp=parent, run_tuple=_run_tuple(), scenario="accepted",
                route_authorized=True, expected_input_digest=prepared["input_digest"],
                expected_provenance_digest=prepared["provenance_digest"])
        else:
            value = replay.prepare_hosted_checkpoint(runner_temp=parent, run_tuple=_run_tuple(), scenario=name)
        _CHECKPOINTS[name] = value
    return _CHECKPOINTS[name]


def _live_boundary_fixture():
    """Synthetic contract inputs only; these are not live observations."""
    from calico_publish.allowlist import load_allowlist
    product = Path(__file__).resolve().parents[1]
    names = sorted(entry.export_name for entry in load_allowlist(product / "contracts/publication-exports-v3.json").exports)
    return {"published_data_commit": "b" * 40, "published_data_tree": "c" * 40,
        "archive_inventory_status": "not_observed_by_replay", "archive_inventory_sha256": None,
        "protected_refs": [{"ref": "refs/heads/main", "sha": "a" * 40},
            {"ref": "refs/heads/published-data", "sha": "b" * 40},
            {"ref": "refs/heads/release", "sha": "d" * 40},
            {"ref": "refs/tags/v1", "sha": "e" * 40}],
        "published_manifest_sha256": "f" * 64,
        "exports": [{"export_name": name, "sha256": "a" * 64, "row_count": index + 1}
                    for index, name in enumerate(names)],
        "controls": [{"file_name": "authorization-probe-status.json", "sha256": "b" * 64},
            {"file_name": "capture-status.json", "sha256": "c" * 64}]}


def _api_fixture():
    from tools import hosted_replay as replay
    run_tuple = _run_tuple()
    run = {"id": run_tuple.run_id, "run_attempt": run_tuple.run_attempt, "head_sha": run_tuple.head_sha,
           "repository": {"full_name": run_tuple.repository}, "event": "workflow_dispatch",
           "status": "completed", "conclusion": "success"}
    rows = [{"id": index + 1, "name": name + (" / route" if name.startswith("route-") else ""),
             "run_id": run_tuple.run_id, "head_sha": run_tuple.head_sha,
             "steps": [] if name.startswith("publish-") and name != "publish-accepted" else
                 [{"number": 1, "name": "Controlled fixture step", "status": "completed", "conclusion": "success"}],
             "status": "completed", "conclusion": "skipped" if name.startswith("publish-") and name != "publish-accepted" else "success"}
            for index, name in enumerate(replay._JOBS)]
    outputs = {"prepare-" + scenario: _checkpoint(scenario).to_json() for scenario in replay._SCENARIOS}
    outputs["publish-accepted"] = _checkpoint("published").to_json()
    outputs.update({"route-" + scenario: json.dumps({"should_publish": scenario == "accepted"}) for scenario in replay._ROUTES})
    outputs["audit-safe-evidence"] = json.dumps({"cleanup_verified": True})
    logs = {replay._job_key(row["name"]): b"Fixture job completed\n" for row in rows if row["conclusion"] != "skipped"}
    boundary = _live_boundary_fixture()
    schema = json.loads((Path(replay.__file__).resolve().parents[1] / "contracts/hosted-replay-envelope-v1.schema.json").read_text())
    historical = {key: value["const"] for key, value in schema["properties"]["historical_real_republish"]["properties"].items()}
    return dict(run=run, jobs={"total_count": len(rows), "jobs": rows}, artifacts={"total_count": 0, "artifacts": []},
                raw_logs=logs, safe_job_outputs=outputs, live_before=boundary, live_after=copy.deepcopy(boundary),
                historical_real_republish=historical)


class HostedReplayAcceptedTracerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools import hosted_replay as replay
        cls.replay = replay
        cls.run_tuple = replay.ReplayRunTuple("fixture/replay", 1, 1, "a" * 40)
        cls.temp = Path(tempfile.gettempdir()).resolve()
        cls.prepared = _checkpoint("accepted")
        cls.published = _checkpoint("published")

    def test_staged_replay_authority_exists(self):
        self.assertIsNotNone(importlib.util.find_spec("tools.hosted_replay"),
                             "staged capture and publication authority is required")

    def test_actual_capture_builds_named_sql_outputs_without_candidate_publication(self):
        doc = self.prepared.to_dict()
        self.assertEqual(doc["input_profile"], "fixture")
        self.assertEqual(doc["outcomes"][0]["outcome"], "accepted")
        self.assertEqual(doc["outcomes"][0]["build_calls"], 1)
        self.assertEqual(doc["outcomes"][0]["publication_calls"], 0)
        self.assertEqual(doc["local_parent"], doc["local_commit"])
        self.assertEqual(len(doc["source_catalog"]), 4)
        self.assertEqual((doc["dbt_model_count"], doc["dbt_test_count"]), (32, 228))
        self.assertTrue(all(row["row_count"] > 0 for row in doc["exports"]
            if row["export_name"] in {"dim_public_organizations", "fct_public_status_observations"}))

    def test_authorized_fresh_worker_matches_exact_source_provenance_and_exports(self):
        before, after = self.prepared.to_dict(), self.published.to_dict()
        for key in ("input_digest", "provenance_digest", "source_catalog", "exports", "analytical_sha256"):
            self.assertEqual(before[key], after[key])
        self.assertTrue(after["reconstructed"])
        self.assertEqual(after["evidence_class"], "fresh_job_reconstruction")
        self.assertEqual(after["outcomes"][0]["publication_calls"], 1)
        self.assertNotEqual(after["local_commit"], after["local_parent"])

    def test_authorization_and_input_mismatch_fail_before_workspace_creation(self):
        for kwargs in ({"route_authorized": False}, {"route_authorized": 1}, {"scenario": "repeat"},
                       {"expected_input_digest": "0" * 64}, {"expected_provenance_digest": None}):
            arguments = dict(runner_temp=self.temp, run_tuple=self.run_tuple, scenario="accepted", route_authorized=True,
                expected_input_digest=self.prepared["input_digest"], expected_provenance_digest=self.prepared["provenance_digest"])
            arguments.update(kwargs)
            with self.subTest(case=list(kwargs)), patch.object(self.replay, "replay_workspace") as workspace:
                with self.assertRaises(self.replay.ReplayError):
                    self.replay.run_publication_worker(**arguments)
                workspace.assert_not_called()

    def test_provenance_mismatch_cannot_publish_candidate(self):
        original = self.replay._publish
        publications = []
        def observe(workspace, staging, **kwargs):
            publications.append(staging.name)
            return original(workspace, staging, **kwargs)
        with patch.object(self.replay, "_publish", side_effect=observe):
            with self.assertRaises(self.replay.ReplayError):
                self.replay.run_publication_worker(runner_temp=self.temp, run_tuple=self.run_tuple, scenario="accepted",
                    route_authorized=True, expected_input_digest=self.prepared["input_digest"], expected_provenance_digest="0" * 64)
        self.assertEqual(publications, ["baseline-publication"])


class HostedReplayEvidenceContractTests(unittest.TestCase):
    def test_driver_contract_exists(self):
        from pathlib import Path
        self.assertTrue((Path(__file__).resolve().parents[1] / "contracts" /
                         "hosted-replay-driver-v1.schema.json").is_file(),
                        "closed driver contract is required before downstream use")

    def test_closed_decoder_rejects_unsafe_shapes_order_and_empty_named_outputs(self):
        from tools import hosted_replay as replay
        doc = HostedReplayAcceptedTracerTests.prepared.to_dict() if hasattr(HostedReplayAcceptedTracerTests, "prepared") else None
        if doc is None:
            doc = replay.prepare_hosted_checkpoint(runner_temp=Path(tempfile.gettempdir()).resolve(),
                run_tuple=replay.ReplayRunTuple("fixture/replay", 1, 1, "a" * 40), scenario="accepted").to_dict()
        cases = []
        for key in doc:
            missing, null = copy.deepcopy(doc), copy.deepcopy(doc)
            missing.pop(key)
            null[key] = None
            cases.extend([missing, null])
        extra = copy.deepcopy(doc)
        extra["extra"] = "unsafe"
        cases.append(extra)
        for key in ("exports", "source_catalog", "byte_audits"):
            reordered = copy.deepcopy(doc)
            reordered[key].reverse()
            cases.append(reordered)
        empty = copy.deepcopy(doc)
        empty["exports"][0]["row_count"] = 0
        cases.append(empty)
        for candidate in cases:
            with self.assertRaises(replay.ReplayError):
                replay.validate_driver(candidate)
        with self.assertRaises(replay.ReplayError):
            replay.validate_driver('{"schema_version":1,"schema_version":2}')

    def test_neutral_fixture_has_complete_policy_and_empty_committed_excluded_fields(self):
        from tests.fixtures.hosted_replay import hosted_replay_fixture
        fixture = hosted_replay_fixture()
        policy = json.loads(fixture.policy)
        self.assertEqual({row["classification"] for row in policy["classifications"]},
                         {"eligible", "unclassified", "ambiguous_natural_person"})
        for index in range(len(fixture.releases)):
            for raw in fixture.source_payloads(index).values():
                for line in raw.decode("cp1252").splitlines()[1:]:
                    self.assertEqual(line.split(",")[2:4], ["", ""])

    def test_no_default_fixture_or_fabricated_build_provenance_is_used(self):
        from tools import hosted_replay as replay
        source = Path(replay.__file__).read_text()
        self.assertFalse("BuildOutcome(" in source)
        self.assertFalse("eval(" in source)
        self.assertTrue('fixture_store_factory=captured_store' in source)


class HostedReplaySequenceTests(unittest.TestCase):
    def test_same_store_three_outcome_authority_exists(self):
        from tools import hosted_replay as replay
        self.assertTrue(callable(getattr(replay, "run_replay_sequence", None)),
                        "same-store accepted/repeat/rejected integration authority is required")

    def test_same_store_sequence_preserves_accepted_analytical_publication(self):
        from tools import hosted_replay as replay
        doc = replay.run_replay_sequence(runner_temp=Path(tempfile.gettempdir()).resolve()).to_dict()
        self.assertEqual(doc["evidence_class"], "supporting_local_sequence")
        self.assertEqual([row["outcome"] for row in doc["outcomes"]], ["accepted", "no_new_release", "rejected"])
        self.assertEqual([row["build_calls"] for row in doc["outcomes"]], [1, 0, 0])
        self.assertEqual([row["publication_calls"] for row in doc["outcomes"]], [1, 0, 0])
        self.assertEqual(len({row["analytical_sha256"] for row in doc["outcomes"]}), 1)

    def test_different_bytes_on_same_date_form_a_distinct_revision(self):
        from tools import hosted_replay as replay
        from calico_landing.admission import admit, load_default_status_contract
        fixture = replay.hosted_replay_fixture()
        with replay.replay_workspace(Path(tempfile.gettempdir()).resolve()) as workspace:
            store = workspace.root / "revision-store"
            store.mkdir()
            first = fixture.materialize(workspace.root / "first", 0)
            changed = fixture.materialize(workspace.root / "changed", 0, changed=True)
            a = admit(first, store, status_contract=load_default_status_contract())
            b = admit(changed, store, status_contract=load_default_status_contract())
            self.assertEqual((a.status, b.status, b.release_revision), ("accepted", "accepted", 2))
            self.assertNotEqual(a.revision_fingerprint, b.revision_fingerprint)


class HostedReplayReconstructionTests(unittest.TestCase):
    def test_repeat_and_rejection_reconstruct_explicit_baselines_without_candidate_build_or_publish(self):
        for scenario in ("repeat", "rejected"):
            doc = _checkpoint(scenario).to_dict()
            self.assertEqual(doc["baseline_label"], "isolated_fixture_baseline")
            self.assertEqual(doc["local_commit"], doc["local_parent"])
            self.assertEqual(doc["analytical_sha256"], doc["baseline_analytical_sha256"])
            self.assertEqual((doc["outcomes"][0]["build_calls"], doc["outcomes"][0]["publication_calls"]), (0, 0))

    def test_prepare_worker_run_tuple_changes_cannot_reconstruct(self):
        from tools import hosted_replay as replay
        doc = _checkpoint("accepted")
        with patch.object(replay, "replay_workspace") as workspace:
            with self.assertRaises(replay.ReplayError):
                replay.run_publication_worker(runner_temp=Path(tempfile.gettempdir()).resolve(),
                    run_tuple=replay.ReplayRunTuple("fixture/replay", 2, 1, "a" * 40), scenario="accepted",
                    route_authorized=True, expected_input_digest=doc["input_digest"], expected_provenance_digest=doc["provenance_digest"])
            workspace.assert_not_called()


class HostedReplayIsolationTests(unittest.TestCase):
    def test_unowned_worktree_and_alias_roots_fail_before_candidate_work(self):
        from tools import hosted_replay as replay
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            bad = replay.ReplayWorkspace(root, replay.FakeArchive(), root / "remote.git", root / "repository", "published-data")
            with self.assertRaises(replay.ReplayError):
                replay.validate_replay_workspace(bad)
            alias = root / "alias"
            alias.symlink_to(root, target_is_directory=True)
            for path in (alias, Path(replay.__file__).resolve().parents[1]):
                with self.assertRaises(replay.ReplayError):
                    with replay.replay_workspace(path):
                        self.fail("unsafe root admitted")

    def test_live_remotes_arbitrary_refs_archive_types_and_links_are_rejected(self):
        from tools import hosted_replay as replay
        from dataclasses import replace
        with replay.replay_workspace(Path(tempfile.gettempdir()).resolve()) as workspace:
            scp_remote = "git" + "@" + "example.invalid:repo"
            for changes in ({"remote": "https://example.invalid/repo"}, {"remote": scp_remote},
                            {"remote": "ssh://example.invalid/repo"}, {"remote": workspace.root.parent / "other.git"},
                            {"target_ref": "main"}, {"archive": object()}):
                with self.subTest(kind=list(changes)), self.assertRaises(replay.ReplayError):
                    replay.validate_replay_workspace(replace(workspace, **changes))
            link = workspace.root / "unsafe-link"
            link.symlink_to(workspace.root.parent, target_is_directory=True)
            with self.assertRaises(replay.ReplayError):
                replay.validate_replay_workspace(workspace)

    def test_credentials_and_local_helpers_fail_before_any_fixture_write(self):
        from tools import hosted_replay as replay
        for key in ("CALICO_B2_APPLICATION_KEY", "GH_TOKEN", "SSH_AUTH_SOCK", "GIT_CONFIG_COUNT", "GIT_SSH_COMMAND"):
            with patch.dict(os.environ, {key: "synthetic-blocked-adapter"}), patch.object(replay.tempfile, "TemporaryDirectory") as temporary:
                with self.assertRaises(replay.ReplayError):
                    with replay.replay_workspace(Path(tempfile.gettempdir()).resolve()):
                        self.fail("credential admitted")
                temporary.assert_not_called()
        with replay.replay_workspace(Path(tempfile.gettempdir()).resolve()) as workspace:
            replay._git(workspace.repo, "config", "credential.helper", "blocked-fixture-helper")
            with self.assertRaises(replay.ReplayError):
                replay.validate_replay_workspace(workspace)

    def test_owned_roots_are_removed_on_interrupt(self):
        from tools import hosted_replay as replay
        root = None
        with self.assertRaises(KeyboardInterrupt):
            with replay.replay_workspace(Path(tempfile.gettempdir()).resolve()) as workspace:
                root = workspace.root
                raise KeyboardInterrupt
        self.assertFalse(root.exists())

    def test_push_url_receive_hooks_and_worktree_repo_cannot_redirect_publication(self):
        from tools import hosted_replay as replay
        with replay.replay_workspace(Path(tempfile.gettempdir()).resolve()) as workspace:
            replay._git(workspace.repo, "config", "remote.origin.pushurl", "https://example.invalid/repo")
            with self.assertRaises(replay.ReplayError):
                replay.validate_replay_workspace(workspace)
            replay._git(workspace.repo, "config", "--unset", "remote.origin.pushurl")
            hook = workspace.remote / "hooks/pre-receive"
            hook.write_text("blocked fixture hook", encoding="utf-8")
            with self.assertRaises(replay.ReplayError):
                replay.validate_replay_workspace(workspace)


class HostedReplayPrivacyTests(unittest.TestCase):
    def test_canaries_are_domain_separated_text_without_identifier_digits(self):
        from tools import hosted_replay as replay
        values = [replay.derive_replay_canary(_run_tuple(), job, category) for job in replay._JOBS for category in replay._CATEGORIES]
        self.assertEqual(len(set(values)), len(values))
        self.assertTrue(all(value.startswith("CALICO_REPLAY_CANARY_") and not any(char.isdigit() for char in value) for value in values))
        self.assertTrue(all(value not in _checkpoint("published").to_json() for value in values))

    def test_every_actual_surface_detects_injected_private_canary(self):
        from tools import hosted_replay as replay
        canary = replay.derive_replay_canary(_run_tuple(), "prepare-accepted", "excluded_identifier")
        for surface in (*replay._SURFACES, "raw_log"):
            with self.subTest(surface=surface), self.assertRaises(replay.ReplayError):
                replay.audit_bytes({surface: ("safe\n" + canary).encode()}, [canary])

    def test_zero_length_streams_have_actual_empty_hash_and_required_documents_are_nonempty(self):
        from tools import hosted_replay as replay
        canary = replay.derive_replay_canary(_run_tuple(), "prepare-accepted", "contact")
        audits = replay.audit_bytes({"stdout": b"", "stderr": b""}, [canary])
        self.assertTrue(all(row["byte_length"] == 0 and row["sha256"] == replay._digest(b"") for row in audits))
        for surface in ("status", "summary", "publication"):
            with self.assertRaises(replay.ReplayError):
                replay.audit_bytes({surface: b""}, [canary])

    def test_runtime_source_canaries_exist_privately_but_not_in_actual_published_evidence(self):
        from tools import hosted_replay as replay
        fixture = replay._fixture_for(_run_tuple())
        token = replay.derive_replay_canary(_run_tuple(), "prepare-accepted", "exception")
        self.assertTrue(any(token.encode() in raw for raw in fixture.source_payloads(0).values()))
        self.assertTrue(token not in _checkpoint("published").to_json())


class HostedReplayCredentialDiagnosticTests(unittest.TestCase):
    def test_every_rejected_environment_adapter_keeps_a_closed_reason(self):
        from tools import hosted_replay as replay
        groups = {
            "b2_environment": ("CALICO_B2_KEY", "B2_KEY"),
            "aws_environment": ("AWS_ACCESS_KEY_ID",),
            "azure_environment": ("AZURE_CLIENT_SECRET",),
            "google_environment": ("GOOGLE_APPLICATION_CREDENTIALS",),
            "azure_user_agent": ("AZURE_HTTP_USER_AGENT",),
            "azure_extension_directory": ("AZURE_EXTENSION_DIR",),
            "token_environment": ("GH_TOKEN", "GITHUB_TOKEN"),
            "ssh_environment": ("SSH_AUTH_SOCK", "GIT_SSH", "GIT_SSH_COMMAND", "GIT_ASKPASS", "SSH_ASKPASS"),
            "git_override": ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_DIR", "GIT_WORK_TREE",
                             "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_INDEX_FILE"),
            "git_config_path": ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"),
            "git_template_or_exec": ("GIT_TEMPLATE_DIR", "GIT_EXEC_PATH"),
        }
        token = replay.derive_replay_canary(_run_tuple(), "prepare-accepted", "exception")
        for reason, keys in groups.items():
            for key in keys:
                with patch.dict(os.environ, {key: token}, clear=True), \
                        patch.object(replay.subprocess, "run") as command, \
                        patch.object(replay.tempfile, "TemporaryDirectory") as owned, \
                        self.assertRaises(replay.ReplayError) as caught:
                    with replay.replay_workspace(Path(tempfile.gettempdir()).resolve()):
                        self.fail("rejected adapter reached fixture writes")
                command.assert_not_called()
                owned.assert_not_called()
                self.assertEqual(caught.exception.category, "replay.credentials_rejected")
                self.assertEqual(caught.exception.credential_reason, "replay.credential_reason." + reason)

    def test_malicious_environment_names_and_values_never_cross_the_cli(self):
        from tools import hosted_replay as replay
        token = replay.derive_replay_canary(_run_tuple(), "prepare-accepted", "exception")
        for prefix, reason in (("AWS_", "aws_environment"), ("AZURE_", "azure_environment"),
                              ("B2_", "b2_environment"), ("CALICO_B2_", "b2_environment"),
                              ("GOOGLE_APPLICATION_", "google_environment")):
            errors, output = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {prefix + token: token + "\n" + token}, clear=True), \
                    redirect_stderr(errors), redirect_stdout(output):
                result = replay.main(HostedReplayCliTests()._prepare_arguments())
            expected = "replay.credentials_rejected\nreplay.credential_reason." + reason + "\n"
            self.assertTrue(result == 1 and output.getvalue() == "" and errors.getvalue() == expected,
                            "environment diagnostics must contain only fixed enums")

    def test_empty_adapters_and_null_git_paths_still_pass(self):
        from tools import hosted_replay as replay
        with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "", "AZURE_HTTP_USER_AGENT": "",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}, clear=True), \
                patch.object(replay.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            replay._credentials()

    def test_git_config_matches_and_read_errors_have_distinct_closed_locations(self):
        from tools import hosted_replay as replay
        token = replay.derive_replay_canary(_run_tuple(), "prepare-accepted", "exception").encode()
        for scope in ("inherited", "repository", "bare"):
            for code in (0, 128):
                with patch.object(replay.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], code, stdout=token, stderr=token)), self.assertRaises(replay.ReplayError) as caught:
                    replay._credential_config_check(Path(tempfile.gettempdir()).resolve(), scope,
                                                    local=scope != "inherited")
                self.assertEqual(caught.exception.category, "replay.credentials_rejected")
                self.assertEqual(caught.exception.credential_reason, "replay.credential_reason." + scope +
                                 "_config" + ("_unreadable" if code else ""))

    def test_cli_and_reporter_forward_exact_closed_reason_pairs_only(self):
        from tools import hosted_replay as replay
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "failure.stderr"
            for reason in replay._CREDENTIAL_REASONS:
                output, errors = io.StringIO(), io.StringIO()
                with patch.object(replay, "prepare_hosted_checkpoint", side_effect=replay.ReplayError(
                        "replay.credentials_rejected", reason)), redirect_stdout(output), redirect_stderr(errors):
                    result = replay.main(HostedReplayCliTests()._prepare_arguments())
                expected = "replay.credentials_rejected\n" + reason + "\n"
                self.assertEqual((result, output.getvalue(), errors.getvalue()), (1, "", expected))
                path.write_text(expected, encoding="ascii")
                output, errors = io.StringIO(), io.StringIO()
                with redirect_stdout(output), redirect_stderr(errors):
                    result = replay.main(["report-failure", "--input", str(path)])
                self.assertEqual((result, output.getvalue(), errors.getvalue()), (0, "", expected))

    def test_unknown_or_malformed_reason_is_never_forwarded(self):
        from tools import hosted_replay as replay
        token = replay.derive_replay_canary(_run_tuple(), "prepare-accepted", "exception")
        for category, expected in (("replay.credentials_rejected", "replay.credentials_rejected\n"),
                                   ("replay.failed", "replay.failed\n")):
            for reason in (token, [token], None, "replay.credential_reason.unknown"):
                output, errors = io.StringIO(), io.StringIO()
                with patch.object(replay, "prepare_hosted_checkpoint", side_effect=replay.ReplayError(
                        category, reason)), redirect_stdout(output), redirect_stderr(errors):
                    result = replay.main(HostedReplayCliTests()._prepare_arguments())
                self.assertTrue(result == 1 and output.getvalue() == "" and errors.getvalue() == expected,
                                "unknown diagnostic reasons must remain private")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "failure.stderr"
            for raw in ("replay.credentials_rejected\n" + token + "\n", "replay.failed\n" +
                        next(iter(replay._CREDENTIAL_REASONS)) + "\n", "replay.credentials_rejected\n" +
                        next(iter(replay._CREDENTIAL_REASONS)) + "\n" + token,
                        "replay.credentials_rejected\n" + token * 100):
                path.write_text(raw, encoding="ascii")
                output, errors = io.StringIO(), io.StringIO()
                with redirect_stdout(output), redirect_stderr(errors):
                    result = replay.main(["report-failure", "--input", str(path)])
                self.assertTrue(result == 0 and output.getvalue() == "" and errors.getvalue() == "replay.failed\n",
                                "malformed diagnostic pairs must fail closed")


class HostedReplayCliTests(unittest.TestCase):
    def _prepare_arguments(self):
        return ["prepare-checkpoint", "--runner-temp", str(Path(tempfile.gettempdir()).resolve()),
            "--scenario", "accepted", "--repository", "fixture/replay", "--run-id", "1",
            "--run-attempt", "1", "--head-sha", "a" * 40]

    def test_cli_preserves_only_closed_failure_categories(self):
        from tools import hosted_replay as replay
        for category in replay._FAILURE_CATEGORIES:
            output, errors = io.StringIO(), io.StringIO()
            with patch.object(replay, "prepare_hosted_checkpoint", side_effect=replay.ReplayError(category)), \
                    redirect_stdout(output), redirect_stderr(errors):
                result = replay.main(self._prepare_arguments())
            self.assertEqual(result, 1)
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(errors.getvalue(), category + "\n")

    def test_cli_never_echoes_injected_exception_canary_or_unknown_category(self):
        from tools import hosted_replay as replay
        token = replay.derive_replay_canary(_run_tuple(), "prepare-accepted", "exception")
        detail = token + "\n" + "synthetic" + "-secret-message"
        for error in (RuntimeError(detail), ValueError(detail), OSError(detail),
                      replay.ReplayError(detail), replay.ReplayError([detail]), KeyboardInterrupt(detail)):
            output, errors = io.StringIO(), io.StringIO()
            with patch.object(replay, "prepare_hosted_checkpoint", side_effect=error), \
                    redirect_stdout(output), redirect_stderr(errors):
                result = replay.main(self._prepare_arguments())
            self.assertEqual(result, 1)
            self.assertTrue(output.getvalue() == "" and errors.getvalue() == "replay.failed\n",
                            "exceptions must expose only the generic failure category")

    def test_report_failure_forwards_exact_enum_and_refuses_arbitrary_stderr(self):
        from tools import hosted_replay as replay
        token = replay.derive_replay_canary(_run_tuple(), "prepare-accepted", "exception").encode()
        cases = [(value.encode() + b"\n", value) for value in replay._FAILURE_CATEGORIES]
        cases += [(raw, "replay.failed") for raw in (b"", token, token * 200,
            b"replay.credentials_rejected", b"replay.credentials_rejected\n" + token,
            b"replay.credentials_rejected\n\n", b"\xff", b"replay.unknown\n")]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "failure.stderr"
            for raw, category in cases:
                path.write_bytes(raw)
                output, errors = io.StringIO(), io.StringIO()
                with redirect_stdout(output), redirect_stderr(errors):
                    result = replay.main(["report-failure", "--input", str(path)])
                self.assertEqual(result, 0)
                self.assertTrue(output.getvalue() == "" and errors.getvalue() == category + "\n",
                                "diagnostic forwarding must expose only one approved category")

    def test_inherited_global_git_config_rejects_before_writes_and_null_scope_isolated(self):
        from tools import hosted_replay as replay
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            (home / ".gitconfig").write_text("[http]\n\tsslVerify = false\n", encoding="utf-8")
            with patch.dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home)):
                os.environ.pop("GIT_CONFIG_GLOBAL", None)
                os.environ.pop("GIT_CONFIG_SYSTEM", None)
                output, errors = io.StringIO(), io.StringIO()
                with patch.object(replay.tempfile, "TemporaryDirectory") as owned, \
                        redirect_stdout(output), redirect_stderr(errors):
                    result = replay.main(self._prepare_arguments())
                owned.assert_not_called()
                self.assertEqual((result, output.getvalue(), errors.getvalue()),
                                 (1, "", "replay.credentials_rejected\nreplay.credential_reason.inherited_config\n"))
                with patch.dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull):
                    replay._credentials()
                    # Explicit Git scope never neutralizes a token or SSH adapter.
                    for key in ("GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "GIT_SSH_COMMAND"):
                        with patch.dict(os.environ, {key: "synthetic-blocked-adapter"}), \
                                self.assertRaises(replay.ReplayError) as caught:
                            replay._credentials()
                        self.assertEqual(caught.exception.category, "replay.credentials_rejected")

    def test_both_cli_schema_validators_accept_actual_closed_documents(self):
        from tools import hosted_replay as replay
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            documents = {"driver": _checkpoint("accepted").to_dict(),
                         "envelope": replay.collect_hosted_envelope(**_api_fixture()).to_dict()}
            for kind, document in documents.items():
                path = root / (kind + ".json")
                path.write_text(json.dumps(document), encoding="utf-8")
                result = subprocess.run([sys.executable, "-m", "tools.hosted_replay", "validate-" + kind,
                    "--input", str(path)], capture_output=True, check=False)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout), {"category": "replay." + kind + "_verified"})
                self.assertEqual(result.stderr, b"")

    def test_cli_fails_without_echoing_invalid_arguments_canaries_or_exception_details(self):
        from tools import hosted_replay as replay
        token = replay.derive_replay_canary(_run_tuple(), "prepare-accepted", "private_path")
        result = subprocess.run([sys.executable, "-m", "tools.hosted_replay", "validate-driver",
            "--input", token], capture_output=True, check=False)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (1, b"", b"replay.failed\n"))

    def test_envelope_validator_accepts_the_documented_evidence_flag_and_input_flag(self):
        from tools import hosted_replay as replay
        document = replay.collect_hosted_envelope(**_api_fixture()).to_dict()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "envelope.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            for flag in ("--evidence", "--input"):
                result = subprocess.run([sys.executable, "-m", "tools.hosted_replay", "validate-envelope",
                    flag, str(path)], capture_output=True, check=False)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout), {"category": "replay.envelope_verified"})
                self.assertEqual(result.stderr, b"")

    def test_collect_cli_rescans_raw_logs_and_removes_owned_contract_fixture_root(self):
        from tools import hosted_replay as replay
        fixture = _api_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            logs = fixture.pop("raw_logs")
            directory = root / "logs"
            directory.mkdir()
            for name, raw in logs.items():
                (directory / (name + ".log")).write_bytes(raw)
            bundle = root / "bundle.json"
            bundle.write_text(json.dumps(fixture), encoding="utf-8")
            result = subprocess.run([sys.executable, "-m", "tools.hosted_replay", "collect-envelope",
                "--input", str(bundle), "--logs-dir", str(directory)], capture_output=True, check=False)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, b"")
            doc = replay.validate_envelope(result.stdout)
            self.assertEqual(doc["artifact_count"], 0)
        self.assertFalse(root.exists())


class HostedReplayHostMetadataTests(unittest.TestCase):
    def _metadata(self):
        root = "/".join(("", "home", "runner", "work", "replay", "replay"))
        temporary = "/".join(("", "home", "runner", "work", "_temp"))
        unique = "a" * 8 + "-" + "b" * 4 + "-4" + "c" * 3 + "-8" + "d" * 3 + "-" + "e" * 12
        return root, temporary, unique

    def _canaries(self):
        from tools import hosted_replay as replay
        return tuple(replay.derive_replay_canary(_run_tuple(), job, category)
                     for job in replay._JOBS for category in replay._CATEGORIES)

    def test_public_platform_paths_preserve_original_byte_hash_length_and_hits(self):
        from tools import hosted_replay as replay
        root, temporary, unique = self._metadata()
        paths = (root, root + "/.git/", root + "/.git.path", root + "/.git/worktrees/*.path",
                 temporary + "/" + unique, temporary + "/git-credentials-" + unique + ".config")
        raw = ("\n".join(paths) + "\n").encode()
        records = replay.audit_bytes({"raw_log": raw}, self._canaries(), hosted_run_tuple=_run_tuple())
        self.assertEqual(records, [{"surface": "raw_log", "byte_length": len(raw),
            "sha256": replay._digest(raw), "canary_hits": 0}])

    def test_platform_classification_requires_host_log_context_and_never_applies_to_runtime(self):
        from tools import hosted_replay as replay
        raw = self._metadata()[0].encode()
        with self.assertRaises(replay.ReplayError):
            replay.audit_bytes({"raw_log": raw}, self._canaries())
        for surface in replay._SURFACES:
            with self.assertRaises(replay.ReplayError):
                replay.audit_bytes({surface: raw}, self._canaries())
            with self.assertRaises(replay.ReplayError):
                replay.audit_bytes({surface: raw}, self._canaries(), hosted_run_tuple=_run_tuple())
        with self.assertRaises(replay.ReplayError):
            replay.audit_bytes({"raw_log": raw}, self._canaries(), hosted_run_tuple={})

    def test_unknown_private_nested_and_foreign_checkout_paths_remain_rejected(self):
        from tools import hosted_replay as replay
        root, temporary, unique = self._metadata()
        private = "/".join(("", "Users", "fixture-owner", "private-source.csv"))
        unknown = (private, root + "/private-source.csv", root + "/.git/config",
            root + "/../private-source.csv", temporary + "/private-source.csv",
            temporary + "/" + unique + "/private-source.csv",
            temporary + "/calico-replay-status.abcdef", root.replace("replay/replay", "other/other"),
            temporary + "/git-credentials-" + unique + ".config/private-source.csv")
        for path in unknown:
            for raw in (path.encode(), (root + " " + path).encode()):
                with self.assertRaises(replay.ReplayError) as caught:
                    replay.audit_bytes({"raw_log": raw}, self._canaries(), hosted_run_tuple=_run_tuple())
                self.assertEqual(caught.exception.category, "replay.privacy_failed")

    def test_canaries_and_other_excluded_categories_inside_metadata_are_never_concealed(self):
        from tools import hosted_replay as replay
        root, temporary, unique = self._metadata()
        canary = self._canaries()[0]
        for raw in ((root + "/" + canary).encode(), (temporary + "/" + unique + canary).encode()):
            with self.assertRaises(replay.ReplayError) as caught:
                replay.audit_bytes({"raw_log": raw}, self._canaries(), hosted_run_tuple=_run_tuple())
            self.assertEqual(caught.exception.category, "replay.canary_found")
        excluded = ("12" + "-" + "345" + "6789", "123" + " Harbor" + " Road",
                    "fixture" + "@" + "example.invalid", "https://example.invalid/?" + "e" + "in=value")
        for text in excluded:
            raw = (root + " " + text).encode()
            with self.assertRaises(replay.ReplayError) as caught:
                replay.audit_bytes({"raw_log": raw}, self._canaries(), hosted_run_tuple=_run_tuple())
            self.assertEqual(caught.exception.category, "replay.privacy_failed")

    def test_invalid_encoding_and_non_newline_controls_cannot_shift_private_path_locations(self):
        from tools import hosted_replay as replay
        root = self._metadata()[0]
        private = "/".join(("", "Users", "fixture-owner", "private-source.csv"))
        for raw in (root.encode() + b"\n\xff", (root + "\r" + private + "\n" + root).encode(),
                    (root + "\x85" + private + "\n" + root).encode()):
            with self.assertRaises(replay.ReplayError):
                replay.audit_bytes({"raw_log": raw}, self._canaries(), hosted_run_tuple=_run_tuple())


class HostedReplayTransactionFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools import hosted_replay as replay
        cls.replay = replay
        cls.context = replay.replay_workspace(Path(tempfile.gettempdir()).resolve())
        cls.workspace = cls.context.__enter__()
        fixture = replay._fixture_for(_run_tuple())
        cls.baseline = replay._seed(cls.workspace, fixture, len(fixture.releases)-1)
        cls.state = replay._candidate(cls.workspace, fixture, cls.baseline, "accepted")

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)

    def tip(self):
        return self.replay._git(self.workspace.repo, "ls-remote", "origin", "refs/heads/published-data").split()[0]

    def test_missing_and_tampered_required_policy_cannot_reach_dbt(self):
        replay = self.replay
        sidecar = self.baseline.store / "public-eligibility-v1.json"
        original = sidecar.read_bytes()
        for payload in (None, b"{}", b"{invalid"):
            try:
                if payload is None:
                    sidecar.unlink()
                else:
                    sidecar.write_bytes(payload)
                with patch.object(replay, "build") as sql:
                    with self.assertRaises(Exception):
                        replay._build_exports(self.workspace, self.baseline.store, self.workspace.root / "blocked-staging")
                    sql.assert_not_called()
            finally:
                sidecar.write_bytes(original)

    def test_unapproved_column_and_invalid_status_preserve_prior_publication(self):
        replay = self.replay
        for name, mutation in (("exports/dim_public_organizations.csv", lambda raw: raw.replace(b"organization_name", b"unapproved_column", 1)),
                               ("capture-status.json", lambda _: b"{}")):
            path = self.state.staging / name
            raw, before = path.read_bytes(), self.tip()
            try:
                path.write_bytes(mutation(raw))
                with self.assertRaises(Exception):
                    replay._publish(self.workspace, self.state.staging)
                self.assertEqual(self.tip(), before)
            finally:
                path.write_bytes(raw)

    def test_post_scan_mutation_is_rejected_before_transaction(self):
        replay = self.replay
        path = self.state.staging / "exports/dim_public_organizations.csv"
        raw, before = path.read_bytes(), self.tip()
        original_scan = replay.scan_paths
        def mutate(*args):
            result = original_scan(*args)
            path.write_bytes(raw + b"\n")
            return result
        try:
            with patch.object(replay, "scan_paths", side_effect=mutate), patch.object(replay, "publish_tree") as transaction:
                with self.assertRaises(replay.ReplayError):
                    replay._publish(self.workspace, self.state.staging)
                transaction.assert_not_called()
            self.assertEqual(self.tip(), before)
        finally:
            path.write_bytes(raw)

    def test_interrupted_write_cannot_advance_remote(self):
        replay, before = self.replay, self.tip()
        def interrupt(stage):
            if stage == "before_write_tree":
                raise replay.ReplayError("replay.controlled_interruption")
        with self.assertRaises(replay.ReplayError):
            replay._publish(self.workspace, self.state.staging, failure_hook=interrupt)
        self.assertEqual(self.tip(), before)

    def test_status_timestamp_change_is_separate_from_analytical_identity_and_noop_proof(self):
        replay = self.replay
        path = self.baseline.staging / "capture-status.json"
        raw = path.read_bytes()
        analytical = replay._analytical(self.baseline.staging)
        try:
            doc = json.loads(raw)
            doc["ended_at_utc"] = "2031-03-18T00:01:00Z"
            path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertNotEqual(replay._digest(path.read_bytes()), replay._digest(raw))
            self.assertEqual(replay._analytical(self.baseline.staging), analytical)
            before = self.tip()
            outcome = replay._publish(self.workspace, self.baseline.staging)
            self.assertEqual(outcome.status, "no_change")
            self.assertEqual(self.tip(), before)
        finally:
            path.write_bytes(raw)

    def test_recursive_publication_audit_reads_carried_control_bytes(self):
        replay = self.replay
        path = self.workspace.repo / "authorization-probe-status.json"
        original = path.read_bytes()
        token = replay.derive_replay_canary(_run_tuple(), "prepare-accepted", "contact")
        def advance(raw):
            parent = self.tip()
            replay._git(self.workspace.repo, "read-tree", parent)
            path.write_bytes(raw)
            replay._git(self.workspace.repo, "add", "authorization-probe-status.json")
            tree = replay._git(self.workspace.repo, "write-tree")
            commit = replay._git(self.workspace.repo, "commit-tree", tree, "-p", parent, "-m", "Controlled fixture byte audit")
            replay._git(self.workspace.repo, "push", "origin", commit + ":refs/heads/published-data")
        try:
            advance(token.encode())
            with self.assertRaises(replay.ReplayError):
                replay.audit_bytes({"publication": replay._published_bytes(self.workspace)}, [token])
        finally:
            advance(original)

    def test_conflicting_nonforce_advances_never_install_candidate_exports(self):
        replay = self.replay
        calls = []
        def conflict(stage):
            if stage != "before_push":
                return
            parent = self.tip()
            tree = replay._git(self.workspace.repo, "rev-parse", parent + "^{tree}")
            competing = replay._git(self.workspace.repo, "commit-tree", tree, "-p", parent, "-m", "Controlled fixture conflict " + str(len(calls)))
            replay._git(self.workspace.repo, "push", "origin", competing + ":refs/heads/published-data")
            calls.append(competing)
        with self.assertRaises(Exception):
            replay._publish(self.workspace, self.state.staging, failure_hook=conflict)
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.tip(), calls[-1])
        tree = replay._git(self.workspace.repo, "rev-parse", self.tip() + "^{tree}")
        self.assertEqual(tree, self.baseline.tree)


class HostedReplayLiveBoundaryTests(unittest.TestCase):
    def test_complete_sorted_safe_measurements_are_accepted_without_archive_claim(self):
        from tools import hosted_replay as replay
        boundary = _live_boundary_fixture()
        self.assertEqual(replay.validate_live_boundary(boundary), boundary)
        replay.validate_live_boundaries(boundary, copy.deepcopy(boundary))
        self.assertEqual(len(boundary["exports"]), 12)
        self.assertEqual(len(boundary["controls"]), 2)
        self.assertIsNone(boundary["archive_inventory_sha256"])

    def test_each_required_measurement_is_closed_typed_and_nonnull_except_unobserved_archive(self):
        from tools import hosted_replay as replay
        original = _live_boundary_fixture()
        for key in original:
            with self.subTest(field=key, mutation="missing"):
                changed = copy.deepcopy(original)
                changed.pop(key)
                with self.assertRaises(replay.ReplayError):
                    replay.validate_live_boundary(changed)
            if key != "archive_inventory_sha256":
                changed = copy.deepcopy(original)
                changed[key] = None
                with self.subTest(field=key, mutation="null"), self.assertRaises(replay.ReplayError):
                    replay.validate_live_boundary(changed)
        for patch_values in ({"extra": "unapproved"}, {"archive_inventory_sha256": "d" * 64},
                             {"archive_inventory_status": "measured"}):
            with self.assertRaises(replay.ReplayError):
                replay.validate_live_boundary({**original, **patch_values})
        for key in ("published_data_commit", "published_data_tree", "published_manifest_sha256"):
            for value in (False, 1, "", "unapproved", "A" * len(original[key]), "0" * (len(original[key]) - 1)):
                with self.subTest(field=key, mutation="malformed"), self.assertRaises(replay.ReplayError):
                    replay.validate_live_boundary({**original, key: value})

    def test_refs_are_complete_unique_sorted_safe_and_match_independent_publication_commit(self):
        from tools import hosted_replay as replay
        original = _live_boundary_fixture()
        for ref in ("refs/heads/main", "refs/heads/published-data"):
            changed = copy.deepcopy(original)
            changed["protected_refs"] = [row for row in changed["protected_refs"] if row["ref"] != ref]
            with self.assertRaises(replay.ReplayError):
                replay.validate_live_boundary(changed)
        for ref in ("main", "refs/remotes/origin/main", "refs/heads/../main", "refs/heads//main",
                    "refs/heads/.hidden", "refs/heads/main.lock", "refs/tags/v1.", "refs/tags/v1|unsafe",
                    "refs/heads/main\n", "refs/heads/main@{1}", "refs/heads/main/", "refs/heads/\\main"):
            changed = copy.deepcopy(original)
            changed["protected_refs"][-1]["ref"] = ref
            changed["protected_refs"].sort(key=lambda row: row["ref"])
            with self.subTest(mutation="unsafe_ref"), self.assertRaises(replay.ReplayError):
                replay.validate_live_boundary(changed)
        changed = copy.deepcopy(original)
        changed["protected_refs"][-1]["ref"] = "refs/tags/" + replay.derive_replay_canary(
            _run_tuple(), "prepare-accepted", "private_path")
        with self.assertRaises(replay.ReplayError):
            replay.validate_live_boundary(changed)
        mutations = [lambda d: d["protected_refs"].reverse(),
            lambda d: d["protected_refs"].append(copy.deepcopy(d["protected_refs"][-1])),
            lambda d: d["protected_refs"][0].update(sha="A" * 40),
            lambda d: d["protected_refs"][0].update(sha=True),
            lambda d: d["protected_refs"][0].update(extra="unapproved"),
            lambda d: d["protected_refs"][0].pop("sha"),
            lambda d: d.update(published_data_commit="0" * 40)]
        for mutate in mutations:
            changed = copy.deepcopy(original)
            mutate(changed)
            with self.assertRaises(replay.ReplayError):
                replay.validate_live_boundary(changed)

    def test_exports_and_controls_reject_incomplete_duplicate_unapproved_and_malformed_rows(self):
        from tools import hosted_replay as replay
        original = _live_boundary_fixture()
        for field, name_key in (("exports", "export_name"), ("controls", "file_name")):
            mutations = [lambda rows: rows.pop(), lambda rows: rows.append(copy.deepcopy(rows[0])),
                lambda rows: rows.__setitem__(1, copy.deepcopy(rows[0])), lambda rows: rows.reverse(),
                lambda rows: rows[0].update({name_key: "unapproved"}),
                lambda rows: rows[0].update(sha256="A" * 64), lambda rows: rows[0].update(sha256=None),
                lambda rows: rows[0].update(sha256="a" * 63), lambda rows: rows[0].update(extra="unapproved"),
                lambda rows: rows[0].pop("sha256"), lambda rows: rows.__setitem__(0, None)]
            if field == "exports":
                mutations.extend(lambda rows, value=value: rows[0].update(row_count=value)
                                 for value in (True, -1, 1.5, "1", None))
            for mutate in mutations:
                changed = copy.deepcopy(original)
                mutate(changed[field])
                with self.subTest(field=field), self.assertRaises(replay.ReplayError):
                    replay.validate_live_boundary(changed)

    def test_drift_in_every_commit_tree_ref_manifest_export_and_control_is_rejected(self):
        from tools import hosted_replay as replay
        before = _live_boundary_fixture()
        after_values = []
        for field, value in (("published_data_tree", "0" * 40), ("published_manifest_sha256", "0" * 64)):
            changed = copy.deepcopy(before)
            changed[field] = value
            after_values.append(changed)
        for index in range(len(before["protected_refs"])):
            changed = copy.deepcopy(before)
            changed["protected_refs"][index]["sha"] = "0" * 40
            if changed["protected_refs"][index]["ref"] == "refs/heads/published-data":
                changed["published_data_commit"] = "0" * 40
            after_values.append(changed)
        for field in ("exports", "controls"):
            for index in range(len(before[field])):
                for value_key in (["sha256", "row_count"] if field == "exports" else ["sha256"]):
                    changed = copy.deepcopy(before)
                    changed[field][index][value_key] = "0" * 64 if value_key == "sha256" else 100
                    after_values.append(changed)
        removed = copy.deepcopy(before)
        removed["protected_refs"].pop()
        added = copy.deepcopy(before)
        added["protected_refs"].append({"ref": "refs/tags/v2", "sha": "f" * 40})
        after_values.extend([removed, added])
        for changed in after_values:
            replay.validate_live_boundary(changed)
            with self.assertRaises(replay.ReplayError) as caught:
                replay.validate_live_boundaries(before, changed)
            self.assertEqual(caught.exception.category, "replay.live_boundary_changed")

    def test_collection_checks_boundary_before_inspecting_api_outputs(self):
        from tools import hosted_replay as replay
        before = _live_boundary_fixture()
        after = copy.deepcopy(before)
        after["controls"][1]["sha256"] = "0" * 64
        with patch.object(replay, "_run_identity") as api:
            with self.assertRaises(replay.ReplayError):
                replay.collect_hosted_envelope(run=None, jobs=None, artifacts=None, raw_logs=None,
                    safe_job_outputs=None, live_before=before, live_after=after, historical_real_republish=None)
            api.assert_not_called()

    def test_boundary_json_duplicate_members_fail_before_projection(self):
        from tools import hosted_replay as replay
        raw = json.dumps(_live_boundary_fixture())
        raw = raw.replace('"published_manifest_sha256":', '"published_manifest_sha256":"' + "a" * 64 + '","published_manifest_sha256":')
        with self.assertRaises(replay.ReplayError):
            replay.validate_live_boundary(replay._decode(raw))


class HostedEnvelopeJobsApiTests(unittest.TestCase):
    def test_closed_envelope_collector_exists(self):
        from tools import hosted_replay as replay
        self.assertTrue(callable(getattr(replay, "collect_hosted_envelope", None)),
                        "Jobs API and actual-byte envelope authority is required")

    def test_actual_shaped_jobs_preserve_successful_worker_and_five_host_skips(self):
        from tools import hosted_replay as replay
        doc = replay.collect_hosted_envelope(**_api_fixture()).to_dict()
        self.assertEqual([row["worker_conclusion"] for row in doc["worker_matrix"]], ["success"] + ["skipped"] * 5)
        self.assertEqual(doc["event"], "workflow_dispatch")
        self.assertEqual(doc["evidence_classes"]["actual_schedule"], "not_observed_by_replay")
        self.assertEqual(doc["live_before"], doc["live_after"])

    def test_malformed_jobs_artifacts_and_nonmatching_run_identity_fail_closed(self):
        from tools import hosted_replay as replay
        original = _api_fixture()
        mutations = [lambda d: d["jobs"].update(total_count=0),
            lambda d: d["jobs"]["jobs"][0].update(run_id=2),
            lambda d: d["jobs"]["jobs"][0].update(head_sha="b" * 40),
            lambda d: d["jobs"]["jobs"][0].update(steps=[]),
            lambda d: d["jobs"]["jobs"][0].update(id=True),
            lambda d: d["jobs"]["jobs"][0].update(conclusion="skipped"),
            lambda d: d["jobs"]["jobs"][-1].update(name=d["jobs"]["jobs"][0]["name"]),
            lambda d: d["artifacts"].update(total_count=1, artifacts=[{"id": 1}]),
            lambda d: d["run"].update(run_attempt=2),
            lambda d: d["live_after"].update(published_data_commit="e" * 40),
            lambda d: d["run"].update(event="schedule")]
        for mutate in mutations:
            candidate = copy.deepcopy(original)
            mutate(candidate)
            with self.assertRaises(replay.ReplayError):
                replay.collect_hosted_envelope(**candidate)

    def test_skipped_workers_cannot_fabricate_safe_outputs_or_byte_audits(self):
        from tools import hosted_replay as replay
        candidate = _api_fixture()
        candidate["safe_job_outputs"]["publish-repeat"] = _checkpoint("published").to_json()
        with self.assertRaises(replay.ReplayError):
            replay.collect_hosted_envelope(**candidate)


class HostedEnvelopeByteAuditTests(unittest.TestCase):
    def test_collector_rescans_actual_downloaded_bytes_and_rejects_canary_hit(self):
        from tools import hosted_replay as replay
        fixture = _api_fixture()
        token = replay.derive_replay_canary(_run_tuple(), "prepare-accepted", "private_path")
        fixture["raw_logs"]["prepare-accepted"] += token.encode()
        with self.assertRaises(replay.ReplayError):
            replay.collect_hosted_envelope(**fixture)

    def test_claimed_zeroes_missing_hashes_and_empty_publication_audits_are_rejected(self):
        from tools import hosted_replay as replay
        original = _api_fixture()
        mutations = [lambda d: d.update(byte_audits=[]),
            lambda d: d["byte_audits"][0].pop("sha256"),
            lambda d: d["byte_audits"][0].pop("byte_length"),
            lambda d: d["byte_audits"][0].update(byte_length=0),
            lambda d: d.update(privacy_match_count=0)]
        for mutate in mutations:
            fixture = copy.deepcopy(original)
            doc = json.loads(fixture["safe_job_outputs"]["prepare-accepted"])
            mutate(doc)
            fixture["safe_job_outputs"]["prepare-accepted"] = json.dumps(doc)
            with self.assertRaises(replay.ReplayError):
                replay.collect_hosted_envelope(**fixture)

    def test_unsafe_duplicate_oversized_outputs_and_missing_raw_logs_fail(self):
        from tools import hosted_replay as replay
        for raw in ('{"should_publish":true,"should_publish":false}', "{" + " " * 70000 + "}",
                    '{"should_publish":true,"raw_path":"unsafe"}'):
            fixture = _api_fixture()
            fixture["safe_job_outputs"]["route-accepted"] = raw
            with self.assertRaises(replay.ReplayError):
                replay.collect_hosted_envelope(**fixture)
        fixture = _api_fixture()
        fixture["raw_logs"].pop("prepare-accepted")
        with self.assertRaises(replay.ReplayError):
            replay.collect_hosted_envelope(**fixture)

    def test_closed_envelope_rejects_missing_extra_null_reordered_and_relabelled_evidence(self):
        from tools import hosted_replay as replay
        original = replay.collect_hosted_envelope(**_api_fixture()).to_dict()
        for mutate in (lambda d: d.pop("raw_log_audits"), lambda d: d.update(extra=0),
                       lambda d: d.update(run_tuple=None), lambda d: d["checkpoints"].reverse(),
                       lambda d: d["worker_matrix"].reverse(), lambda d: d.update(evidence_class="actual_schedule")):
            candidate = copy.deepcopy(original)
            mutate(candidate)
            with self.assertRaises(replay.ReplayError):
                replay.validate_envelope(candidate)
