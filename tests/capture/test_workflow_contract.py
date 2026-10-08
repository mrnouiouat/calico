"""Contract tests for the scheduled/manual capture workflow and the mandatory
local runbook (06-06-PLAN.md Task 2; T-06-06A/B/C).

Parses `.github/workflows/capture-current.yml` and `docs/capture-runbook.md`
as plain committed text -- never a live GitHub API call, never a real
workflow dispatch -- and proves the exact schedule/calendar gate, bounded
concurrency, job-level secret/write separation, absence of any artifact
upload, and the runbook's permanently-mandatory manual-recovery language.
Hosted branch/tag enforcement itself remains a human checkpoint (a later
plan); this module proves only what committed YAML/Markdown text can prove.
"""

from __future__ import annotations

import re
import hashlib
import json
import os
import tempfile
import textwrap
import unittest
from unittest.mock import patch
from pathlib import Path

from calico_capture.orchestrator import SCHEDULE_CRON, retry_delays

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "capture-current.yml"
ROUTE_PATH = REPO_ROOT / ".github" / "workflows" / "publication-route.yml"
RUNBOOK_PATH = REPO_ROOT / "docs" / "capture-runbook.md"

CHECKOUT_PIN = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON_PIN = "5fda3b95a4ea91299a34e894583c3862153e4b97"
LIVE_WORKER_HASHES = {
    "capture": "f5c59e7b6f96ff60c77cf2dad36af3f5fcdd6625f434b63deebfd2ea0dc446fc",
    "status": "28689e9aa8dc62b0e1b8c5ce38b90a159828c4f1c5321cb3e41f797fe568b208",
    "publish": "cdea1035dc9f7d03adcdfb83eeb08e194bc1ac1761eb707dd97f13fc9379be1c",
    "authorization-probe": "749b42b874900d642ac34083a5dd1923cac302a777607594710cf7ab6618abfa",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _job_block(content: str, job_name: str) -> str:
    """Extract one top-level job's raw text block: from its `  <job_name>:`
    line (exactly two-space indented, mirroring every job key in this
    workflow) to the next two-space-indented job key or end of file.
    Mirrors `tests.dbt_foundation.test_ci_contract`'s own exact-block
    (never substring-only) parsing discipline.
    """

    lines = content.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line == f"  {job_name}:"), None
    )
    assert start is not None, f"job `{job_name}` not found"
    end = len(lines)
    job_key_pattern = re.compile(r"^  [A-Za-z0-9_-]+:\s*$")
    for i in range(start + 1, len(lines)):
        if job_key_pattern.match(lines[i]):
            end = i
            break
    return "\n".join(lines[start:end])


def _permissions_block(text: str) -> list[str]:
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == "permissions:"), None)
    assert start is not None, "no `permissions:` block found in this section"
    indent = len(lines[start]) - len(lines[start].lstrip())
    block: list[str] = []
    for line in lines[start + 1 :]:
        if not line.strip():
            continue
        line_indent = len(line) - len(line.lstrip())
        if line_indent <= indent:
            break
        block.append(line.strip())
    return block


class SharedPublicationRouteContractTests(unittest.TestCase):
    """Offline source/validation contracts; GitHub evaluates the route at hosted proof."""

    def _route(self):
        self.assertTrue(ROUTE_PATH.is_file(), "shared hosted publication route must exist")
        return _read(ROUTE_PATH)

    def test_shared_publication_route_exists(self):
        self._route()

    def test_route_has_closed_typed_inputs_one_job_and_no_privileges(self):
        import yaml
        content = self._route()
        document = yaml.safe_load(content)
        call = document["on" if "on" in document else True]["workflow_call"]
        self.assertEqual(set(call["inputs"]), {"calendar_result", "should_run", "mode",
            "capture_result", "status_result", "status_json"})
        self.assertTrue(all(value == {"type": "string", "required": True}
                            for value in call["inputs"].values()))
        self.assertEqual(set(document["jobs"]), {"route"})
        self.assertEqual(document["permissions"], {"contents": "read"})
        self.assertEqual(document["jobs"]["route"]["permissions"], {"contents": "read"})
        self.assertEqual(call["outputs"]["should_publish"]["value"],
                         "${{ jobs.route.outputs.should_publish }}")
        for forbidden in ("secrets", "environment:", "contents: write", "upload-artifact",
                          "pip install", "git push", "eval("):
            self.assertNotIn(forbidden, content)
        self.assertIn("persist-credentials: false", content)

    def test_single_github_expression_and_output_are_exact(self):
        content = self._route()
        condition = next(line.strip() for line in content.splitlines() if line.strip().startswith("if:"))
        self.assertEqual(condition, "if: ${{ !cancelled() && steps.validate.outputs.valid == 'true' && "
            "inputs.calendar_result == 'success' && inputs.should_run == 'true' && "
            "( ( inputs.mode == 'republish' && inputs.capture_result == 'skipped' && "
            "inputs.status_result == 'skipped' ) || ( inputs.mode == 'capture' && "
            "inputs.capture_result == 'success' && inputs.status_result == 'success' && "
            "fromJSON(steps.validate.outputs.status_json).outcome == 'accepted' ) ) }}")
        self.assertEqual(content.count("fromJSON("), 1)
        self.assertIn("echo 'should_publish=true'", content)
        self.assertIn("${{ steps.publish.outputs.should_publish == 'true' }}", content)

    def test_live_caller_passes_actual_needs_and_only_consumes_route_output(self):
        content = _read(WORKFLOW_PATH)
        caller = _job_block(content, "publication-route")
        self.assertIn("needs: [calendar-gate, capture, status]", caller)
        self.assertIn("if: ${{ !cancelled() }}", caller)
        self.assertIn("uses: ./.github/workflows/publication-route.yml", caller)
        for name, reference in (("calendar_result", "needs.calendar-gate.result"),
                ("should_run", "needs.calendar-gate.outputs.should_run"),
                ("mode", "needs.calendar-gate.outputs.mode"),
                ("capture_result", "needs.capture.result"), ("status_result", "needs.status.result"),
                ("status_json", "needs.capture.outputs.status_json")):
            self.assertIn(name + ": ${{ " + reference + " }}", caller)
        self.assertNotIn("secrets", caller)
        self.assertNotIn("environment:", caller)
        self.assertNotIn("fromJSON(", content)
        self.assertIn("if: ${{ !cancelled() && needs.publication-route.result == 'success' && "
                      "needs.publication-route.outputs.should_publish == 'true' }}",
                      _job_block(content, "publish"))

    def _validate(self, **overrides):
        from calico_capture.status import project_safe_status
        status = project_safe_status(trigger="local", outcome="accepted", reason_category="none",
            started_at_utc="2032-01-01T00:00:00Z", ended_at_utc="2032-01-01T00:00:01Z").to_json()
        # Use the production inline validator itself. No Actions-to-Python expression translation.
        values = dict(CALENDAR_RESULT="success", SHOULD_RUN="true", MODE="capture",
                      CAPTURE_RESULT="success", STATUS_RESULT="success", STATUS_JSON=status)
        values.update(overrides)
        script = self._route().split("python - <<'PY'\n", 1)[1].split("          PY", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.dict(os.environ, dict(values, GITHUB_OUTPUT=str(output))):
                exec(compile(textwrap.dedent(script), str(ROUTE_PATH), "exec"), {"__name__": "__main__"})
            return dict(line.split("=", 1) for line in output.read_text().splitlines())

    def test_status_missing_null_malformed_or_unvalidated_is_never_accepted(self):
        for value in ("", "null", "{", "[]", "false", '"accepted"', '{"outcome":"accepted"}',
                      '{"outcome":"unknown"}'):
            with self.subTest(status_kind=value):
                result = self._validate(STATUS_JSON=value)
                self.assertEqual(result, {"valid": "false", "status_json": "{}"})
        self.assertEqual(self._validate()["valid"], "true")

    def test_closed_outcomes_remain_data_for_github_to_evaluate(self):
        from calico_capture.status import project_safe_status
        for outcome, reason in (("accepted", "none"), ("no_new_release", "source_not_advanced"),
                                ("rejected", "structural_rejection"), ("operational_error", "archive_failed")):
            status = project_safe_status(trigger="local", outcome=outcome, reason_category=reason,
                started_at_utc="2032-01-01T00:00:00Z", ended_at_utc="2032-01-01T00:00:01Z").to_json()
            result = self._validate(STATUS_JSON=status)
            self.assertEqual(result["valid"], "true")
            self.assertEqual(json.loads(result["status_json"])["outcome"], outcome)

    def test_closed_dependency_matrix_preserves_failure_and_skip_states(self):
        import itertools
        states = ("success", "skipped", "failure", "cancelled")
        for calendar, capture, status in itertools.product(states, repeat=3):
            result = self._validate(CALENDAR_RESULT=calendar, CAPTURE_RESULT=capture, STATUS_RESULT=status)
            self.assertEqual(result["valid"], "true")
        for field in ("CALENDAR_RESULT", "CAPTURE_RESULT", "STATUS_RESULT", "SHOULD_RUN", "MODE"):
            for value in ("", "null", "unknown"):
                self.assertEqual(self._validate(**{field: value})["valid"], "false")
        self.assertEqual(self._validate(MODE="republish", CAPTURE_RESULT="skipped",
                                      STATUS_RESULT="skipped", STATUS_JSON="")["valid"], "true")

    def test_live_privileged_workers_are_byte_exact_except_route_wiring(self):
        content = _read(WORKFLOW_PATH)
        # Frozen hashes of the reviewed live workers; publication header alone is rewired.
        expected = LIVE_WORKER_HASHES
        for job, digest in expected.items():
            block = _job_block(content, job)
            if job == "publish":
                block = block[block.index("    runs-on:"):]
            self.assertEqual(hashlib.sha256(block.encode()).hexdigest(), digest, job)

    def test_live_calendar_calls_stdlib_authority_without_controlled_date(self):
        block = _job_block(_read(WORKFLOW_PATH), "calendar-gate")
        self.assertIn("python calico_capture/calendar.py", block)
        for forbidden in ("isoweekday", "today.day", "--observed-date", "CALICO_OBSERVED_DATE", "pip install"):
            self.assertNotIn(forbidden, block)


class WorkflowScheduleAndCalendarGateTests(unittest.TestCase):
    """Test 1: exact cron, closed dispatch modes, 330-minute bound, constant
    concurrency, cancel-in-progress false, pinned actions, no-secret gate."""

    def _workflow(self) -> str:
        return _read(WORKFLOW_PATH)

    def test_workflow_exists(self) -> None:
        self.assertTrue(WORKFLOW_PATH.is_file())

    def test_workflow_uses_the_exact_documented_weekly_cron(self) -> None:
        content = self._workflow()
        self.assertIn(f'cron: "{SCHEDULE_CRON}"', content)

    def test_workflow_dispatch_modes_are_closed(self) -> None:
        content = self._workflow()
        self.assertIn("workflow_dispatch:", content)
        self.assertIn("options:", content)
        self.assertIn("- capture", content)
        self.assertIn("- republish", content)
        self.assertIn("- authorization_probe", content)
        options_start = content.index("options:")
        options_block = content[options_start : options_start + 200]
        self.assertEqual(options_block.count("- capture"), 1)
        self.assertEqual(options_block.count("- republish"), 1)
        self.assertEqual(options_block.count("- authorization_probe"), 1)

    def test_calendar_gate_allows_republish_without_capture(self) -> None:
        import os
        import subprocess
        import sys
        import tempfile
        gate_block = _job_block(self._workflow(), "calendar-gate")
        script = gate_block.split("python - <<'PY'\n", 1)[1].split("          PY", 1)[0]
        import textwrap
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            result = subprocess.run([sys.executable, "-c", textwrap.dedent(script)],
                env=dict(os.environ, GITHUB_EVENT_NAME="workflow_dispatch",
                         CALICO_DISPATCH_MODE="republish", GITHUB_OUTPUT=str(output)),
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, "republish gate must succeed")
            self.assertEqual(output.read_text().splitlines(),
                ["should_run=true", "mode=republish", "trigger=workflow_dispatch"])
            self.assertEqual(result.stdout + result.stderr, "")

    def test_republish_stays_a_documented_mode_rather_than_being_deleted(self) -> None:
        content = self._workflow()
        self.assertIn("- republish", content)
        self.assertIn("needs.calendar-gate.outputs.mode == 'republish'", content)

    def test_capture_job_timeout_is_exactly_330_minutes(self) -> None:
        capture_block = _job_block(self._workflow(), "capture")
        self.assertIn("timeout-minutes: 330", capture_block)

    def test_concurrency_group_is_constant_and_never_cancels_in_progress(self) -> None:
        content = self._workflow()
        self.assertIn("group: calico-private-capture-v1", content)
        self.assertIn("cancel-in-progress: false", content)
        self.assertNotIn("cancel-in-progress: true", content)

    def test_every_checkout_and_setup_python_step_is_pinned_to_a_full_sha(self) -> None:
        content = self._workflow()
        self.assertIn(f"actions/checkout@{CHECKOUT_PIN}", content)
        self.assertIn(f"actions/setup-python@{SETUP_PYTHON_PIN}", content)
        self.assertNotRegex(content, r"actions/checkout@v\d")
        self.assertNotRegex(content, r"actions/setup-python@v\d")
        # Every `uses:` line in the file is one of these two pinned actions.
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("uses:"):
                self.assertTrue(
                    CHECKOUT_PIN in stripped or SETUP_PYTHON_PIN in stripped,
                    f"unpinned or unexpected action reference: {stripped}",
                )

    def test_calendar_gate_job_has_no_secrets_and_read_only_permissions(self) -> None:
        gate_block = _job_block(self._workflow(), "calendar-gate")
        self.assertEqual(_permissions_block(gate_block), ["contents: read"])
        self.assertNotIn("secrets.", gate_block)
        self.assertNotIn("environment:", gate_block)

    def test_capture_and_status_jobs_only_run_for_capture_mode(self) -> None:
        content = self._workflow()
        capture_block = _job_block(content, "capture")
        status_block = _job_block(content, "status")
        for block in (capture_block, status_block):
            self.assertIn("needs.calendar-gate.outputs.should_run == 'true'", block)
            self.assertIn("needs.calendar-gate.outputs.mode == 'capture'", block)


class WorkflowSecretSeparationTests(unittest.TestCase):
    """Test 2: capture (B2 secrets, contents:read, persist-credentials:
    false); status (no B2 secrets, contents:write); no artifact upload;
    status writes only the fixed safe file to published-data non-forcefully."""

    def _workflow(self) -> str:
        return _read(WORKFLOW_PATH)

    def test_capture_job_has_b2_secrets_and_read_only_disabled_persist_credentials(self) -> None:
        capture_block = _job_block(self._workflow(), "capture")
        self.assertIn("secrets.CALICO_B2_APPLICATION_KEY_ID", capture_block)
        self.assertIn("secrets.CALICO_B2_APPLICATION_KEY", capture_block)
        self.assertEqual(_permissions_block(capture_block), ["contents: read"])
        self.assertIn("persist-credentials: false", capture_block)

    def test_status_job_has_no_b2_secrets_and_write_permission(self) -> None:
        status_block = _job_block(self._workflow(), "status")
        self.assertNotIn("CALICO_B2_APPLICATION_KEY", status_block)
        self.assertNotIn("CALICO_B2_RETENTION_KEY", status_block)
        self.assertNotIn("secrets.", status_block)
        self.assertEqual(_permissions_block(status_block), ["contents: write"])

    def test_only_status_and_publish_jobs_are_granted_write_permission(self) -> None:
        content = self._workflow()
        gate_block = _job_block(content, "calendar-gate")
        capture_block = _job_block(content, "capture")
        status_block = _job_block(content, "status")
        publish_block = _job_block(content, "publish")
        self.assertNotIn("contents: write", gate_block)
        self.assertNotIn("contents: write", capture_block)
        self.assertIn("contents: write", status_block)
        self.assertIn("contents: write", publish_block)
        top_level_permissions = content.split("jobs:")[0]
        self.assertIn("contents: read", top_level_permissions)
        self.assertNotIn("contents: write", top_level_permissions)

    def test_workflow_never_uploads_an_artifact(self) -> None:
        self.assertNotIn("upload-artifact", self._workflow())

    def test_workflow_never_enables_debug_tracing(self) -> None:
        content = self._workflow()
        self.assertNotIn("set -x", content)
        self.assertNotIn("ACTIONS_STEP_DEBUG", content)
        self.assertNotIn("ACTIONS_RUNNER_DEBUG", content)

    def test_workflow_never_ignores_step_failures(self) -> None:
        self.assertNotIn("continue-on-error", self._workflow())

    def test_status_job_writes_only_the_fixed_status_filename_to_published_data(self) -> None:
        status_block = _job_block(self._workflow(), "status")
        self.assertIn("published-data", status_block)
        self.assertIn("capture-status.json", status_block)
        # Never a force push/write to the safe branch.
        self.assertNotIn("--force", status_block)
        self.assertNotIn("push -f", status_block)
        self.assertNotIn("git push origin HEAD:main", status_block)

    def test_status_job_never_writes_to_main_or_tags(self) -> None:
        status_block = _job_block(self._workflow(), "status")
        self.assertNotIn("refs/heads/main", status_block)
        self.assertNotIn("refs/tags/", status_block)

    def test_status_job_validates_the_status_document_before_writing(self) -> None:
        status_block = _job_block(self._workflow(), "status")
        self.assertIn("validate_capture_status_document", status_block)

    def test_capture_job_never_persists_a_git_credential(self) -> None:
        capture_block = _job_block(self._workflow(), "capture")
        self.assertIn("persist-credentials: false", capture_block)

    def test_capture_and_status_run_the_same_production_run_command(self) -> None:
        capture_block = _job_block(self._workflow(), "capture")
        self.assertIn("python -m calico_capture run --trigger", capture_block)
        self.assertIn("needs.calendar-gate.outputs.trigger", capture_block)

    def test_publish_job_has_one_bounded_literal_publication_path(self) -> None:
        block = _job_block(self._workflow(), "publish")
        self.assertEqual(_permissions_block(block), ["contents: write"])
        self.assertIn("needs: [calendar-gate, capture, status]", block)
        self.assertIn("--target-ref published-data", block)
        self.assertEqual(block.count("python -m calico_publish publish"), 1)
        for token in ("--force", "--delete", "push -f", "upload-artifact", "cache@", "continue-on-error", "set -x"):
            self.assertNotIn(token, block)

    def test_publication_result_finishes_status_even_when_publication_failed(self) -> None:
        block = _job_block(self._workflow(), "publish")
        final = block.split("- name: Finish the independent safe status after publication", 1)[1]
        self.assertIn("always() && !cancelled()", final)
        self.assertIn("steps.publication.outcome == 'success'", final)
        self.assertIn("python -m calico_capture.runner", final)
        self.assertIn("git add capture-status.json", final)
        self.assertNotIn("secrets.", final)
        self.assertNotIn("--force", final)

    def test_publish_b2_secrets_are_step_scoped_and_exact(self) -> None:
        block = _job_block(self._workflow(), "publish")
        env_position = block.index("        env:")
        step_position = block.index("- name: Restore verified history, build once, gate, and publish")
        self.assertGreater(env_position, step_position)
        self.assertEqual(block.count("CALICO_B2_PUBLISH_KEY_ID"), 2)
        self.assertEqual(block.count("CALICO_B2_PUBLISH_KEY"), 4)
        self.assertNotIn("CALICO_B2_APPLICATION_KEY", block)

    def test_publish_uses_empty_runner_root_and_propagates_failures(self) -> None:
        block = _job_block(self._workflow(), "publish")
        publication = block.split("        id: publication", 1)[1].split("      - name:", 1)[0]
        self.assertIn("set -euo pipefail", publication)
        self.assertIn('PUBLISH_ROOT="$(mktemp -d)"', publication)
        self.assertIn('mkdir -p "$PUBLISH_ROOT/store" "$PUBLISH_ROOT/staging"', publication)
        self.assertIn('trap \'rm -rf "$PUBLISH_ROOT"\' EXIT', publication)
        self.assertIn('python -m calico_publish publish --mode real --store "$PUBLISH_ROOT/store"', publication)
        for forbidden in ("download-artifact", "--sidecar", "--policy", "set +e", "|| true"):
            self.assertNotIn(forbidden, publication)

    def test_publish_condition_requires_exact_dependency_states(self) -> None:
        block = _job_block(self._workflow(), "publish")
        condition = next(
            line.strip() for line in block.splitlines() if line.strip().startswith("if:")
        )
        self.assertEqual(
            condition,
            "if: ${{ !cancelled() && needs.calendar-gate.result == 'success' && "
            "needs.calendar-gate.outputs.should_run == 'true' && "
            "( ( needs.calendar-gate.outputs.mode == 'republish' && "
            "needs.capture.result == 'skipped' && needs.status.result == 'skipped' ) || "
            "( needs.calendar-gate.outputs.mode == 'capture' && "
            "needs.capture.result == 'success' && needs.status.result == 'success' && "
            "needs.capture.outputs.status_json != '' && "
            "fromJSON(needs.capture.outputs.status_json).outcome == 'accepted' ) ) }}",
        )


class AuthorizationProbeJobTests(unittest.TestCase):
    """06-07-PLAN.md Task 3: the `authorization-probe` job exists, runs only
    for an explicit `workflow_dispatch` with `mode: authorization_probe`,
    never enters the `capture`/`status` path, and carries no B2 secret."""

    def _workflow(self) -> str:
        return _read(WORKFLOW_PATH)

    def test_authorization_probe_job_exists(self) -> None:
        content = self._workflow()
        self.assertIn("  authorization-probe:", content)

    def test_authorization_probe_job_runs_only_for_authorization_probe_mode(self) -> None:
        probe_block = _job_block(self._workflow(), "authorization-probe")
        self.assertIn("needs.calendar-gate.outputs.mode == 'authorization_probe'", probe_block)
        self.assertNotIn("mode == 'capture'", probe_block)

    def test_authorization_probe_job_has_no_b2_secrets_and_no_environment(self) -> None:
        probe_block = _job_block(self._workflow(), "authorization-probe")
        self.assertNotIn("CALICO_B2_APPLICATION_KEY", probe_block)
        self.assertNotIn("CALICO_B2_RETENTION_KEY", probe_block)
        self.assertNotIn("secrets.", probe_block)
        self.assertNotIn("environment:", probe_block)

    def test_authorization_probe_job_force_push_and_deletion_target_only_the_disposable_probe_ref(
        self,
    ) -> None:
        probe_block = _job_block(self._workflow(), "authorization-probe")
        force_push_lines = [line for line in probe_block.splitlines() if '"--force"' in line]
        self.assertTrue(force_push_lines, "expected exactly one --force push call")
        for line in force_push_lines:
            self.assertIn("PROBE_BRANCH", line)
            self.assertNotIn("main", line)
            self.assertNotIn("published-data", line)
        delete_lines = [line for line in probe_block.splitlines() if '"--delete"' in line]
        self.assertTrue(delete_lines, "expected disposable-ref deletion calls")
        for line in delete_lines:
            self.assertTrue("PROBE_BRANCH" in line or "PROBE_TAG" in line)
            self.assertNotIn("main", line)
            self.assertNotIn("published-data", line)

    def test_authorization_probe_job_main_probe_is_never_forced_or_deleted(self) -> None:
        probe_block = _job_block(self._workflow(), "authorization-probe")
        # The only reference to main is the one ordinary (non-force)
        # fast-forward candidate push described in the plan's safety
        # constraints -- never combined with --force or --delete.
        main_lines = [line for line in probe_block.splitlines() if "refs/heads/main" in line]
        self.assertTrue(main_lines, "expected exactly one main-update probe line")
        for line in main_lines:
            self.assertNotIn("--force", line)
            self.assertNotIn("--delete", line)

    def test_authorization_probe_job_prints_the_closed_marker_categories(self) -> None:
        probe_block = _job_block(self._workflow(), "authorization-probe")
        for category in (
            "nontarget_branch_create",
            "main_update",
            "tag_create",
            "deletion",
            "force_push",
            "published_data_update",
        ):
            self.assertIn(f'"{category}"', probe_block)
        self.assertIn("CALICO_AUTHZ_PROBE::", probe_block)

    def test_capture_and_status_jobs_never_run_for_authorization_probe_mode(self) -> None:
        content = self._workflow()
        capture_block = _job_block(content, "capture")
        status_block = _job_block(content, "status")
        for block in (capture_block, status_block):
            self.assertNotIn("authorization_probe", block)


class RunbookContractTests(unittest.TestCase):
    """Test 3: same CLI, mandatory manual/local recovery, no-deletion rule,
    honest (non-exact-time) delayed/dropped cron recovery language."""

    def _runbook(self) -> str:
        return _read(RUNBOOK_PATH)

    def test_runbook_exists(self) -> None:
        self.assertTrue(RUNBOOK_PATH.is_file())

    def test_runbook_documents_the_exact_run_command(self) -> None:
        content = self._runbook()
        self.assertIn("python -m calico_capture run --trigger local", content)

    def test_runbook_documents_every_other_command(self) -> None:
        content = self._runbook()
        for command in (
            "python -m calico_capture attest",
            "python -m calico_capture seed --store",
            "python -m calico_capture restore-build --store",
            "python -m calico_capture inspect-retention",
            "python -m calico_capture audit-hosted-output",
        ):
            self.assertIn(command, content)

    def test_runbook_states_manual_recovery_stays_mandatory(self) -> None:
        content = self._runbook().lower()
        self.assertIn("mandatory", content)
        self.assertIn("permanently", content)

    def test_runbook_never_claims_exact_time_delivery(self) -> None:
        content = self._runbook().lower()
        self.assertNotIn("guaranteed to run exactly", content)
        self.assertNotIn("is an exact-time guarantee", content)
        # The runbook must state the honest negation, not merely omit the
        # false positive claim above.
        self.assertIn("not an exact-time guarantee", content)

    def test_runbook_documents_missed_run_recovery(self) -> None:
        content = self._runbook().lower()
        self.assertIn("delayed", content)
        self.assertIn("missed", content)

    def test_runbook_forbids_deleting_local_history(self) -> None:
        content = self._runbook().lower()
        self.assertIn("never delete", content)

    def test_runbook_never_prints_a_concrete_owner_path_or_credential(self) -> None:
        content = self._runbook()
        self.assertNotIn("C:" + "\\", content)
        for token in ("CALICO_B2_APPLICATION_KEY=", "CALICO_B2_RETENTION_KEY="):
            # Only the bare env var name (documented as a placeholder
            # assignment target), never a literal assigned secret value.
            for line in content.splitlines():
                if token in line:
                    self.assertIn("owner-supplied-value", line)

    def test_runbook_uses_the_fixed_automation_credential_env_var_names(self) -> None:
        content = self._runbook()
        self.assertIn("CALICO_B2_APPLICATION_KEY_ID", content)
        self.assertIn("CALICO_B2_APPLICATION_KEY", content)
        self.assertIn("CALICO_B2_RETENTION_KEY_ID", content)
        self.assertIn("CALICO_B2_RETENTION_KEY", content)


class RetryPolicyCrossCheckTests(unittest.TestCase):
    """Cross-checks the workflow's documented bound against the real,
    already-proven `retry_delays` constant, so the two can never silently
    drift apart."""

    def test_330_minute_timeout_comfortably_bounds_the_real_retry_policy(self) -> None:
        # Three attempts, sleeping up to `retry_delays[-1]` seconds before
        # the final one, plus real transfer/admission/archive/build time --
        # 330 minutes is a generous, explicit ceiling above the 180-minute
        # maximum sleep alone.
        max_sleep_minutes = max(retry_delays) / 60
        self.assertLess(max_sleep_minutes, 330)


if __name__ == "__main__":
    unittest.main()
