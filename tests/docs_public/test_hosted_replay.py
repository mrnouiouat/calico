"""Observable contracts for the smaller, generated hosted replay evidence."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from functools import cache
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
_ENVIRONMENT = None


def setUpModule():
    global _ENVIRONMENT
    _ENVIRONMENT = patch.dict(os.environ)
    _ENVIRONMENT.start()
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
    _ENVIRONMENT.stop()


@cache
def _envelope_bytes():
    from tools import hosted_replay as driver
    from tests import test_hosted_replay as fixtures
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    # A synthetic deployment must precede the generated evidence it cites.
    for relative in ("docs/evidence/gate-e/hosted-replay-v1.json",
                     "docs/provenance/HOSTED-REPLAY-EVIDENCE.md"):
        introductions = subprocess.check_output(
            ["git", "log", "--reverse", "--diff-filter=A", "--format=%H", "HEAD", "--", relative],
            cwd=ROOT, text=True).splitlines()
        if introductions:
            head = subprocess.check_output(["git", "rev-parse", introductions[0] + "^"],
                                           cwd=ROOT, text=True).strip()
            break
    identity = driver.ReplayRunTuple("mrnouiouat/calico", 1, 1, head)
    with patch.object(fixtures, "_run_tuple", return_value=identity), patch.object(fixtures, "_CHECKPOINTS", {}):
        inputs = fixtures._api_fixture()
        commit = inputs["historical_real_republish"]["published_data_commit"]
        tree = subprocess.check_output(["git", "rev-parse", commit + "^{tree}"], cwd=ROOT, text=True).strip()
        inputs["live_before"] = {**inputs["live_before"], "published_data_commit": commit, "published_data_tree": tree}
        next(row for row in inputs["live_before"]["protected_refs"] if row["ref"] == "refs/heads/published-data")["sha"] = commit
        inputs["live_after"] = copy.deepcopy(inputs["live_before"])
        return driver.collect_hosted_envelope(**inputs).to_json()


def envelope():
    from tools import hosted_replay as driver
    return driver.HostedEnvelope(driver.validate_envelope(_envelope_bytes()))


def public_module():
    from tools.docs_public import hosted_replay
    return hosted_replay


class HostedReplayProjectionContractTests(unittest.TestCase):
    def test_public_boundary_authority_exists(self):
        self.assertIsNotNone(importlib.util.find_spec("tools.docs_public.hosted_replay"),
                             "a separate public projection authority is required")

    def test_independent_closed_public_schema_exists(self):
        path = ROOT / "contracts/hosted-replay-public-v1.schema.json"
        self.assertTrue(path.is_file(), "the independent public evidence schema is required")
        schema = json.loads(path.read_text())
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["schema_version"]["const"], "hosted-replay-public-v1")

    def test_projection_preserves_exact_jobs_digests_audits_and_live_equality(self):
        public = public_module().project_hosted_replay_public(envelope()).to_dict()
        private = envelope().to_dict()
        self.assertEqual(public["run"]["head_sha"], private["run_tuple"]["head_sha"])
        self.assertEqual(public["jobs"], private["jobs"])
        self.assertEqual(public["worker_matrix"], private["worker_matrix"])
        self.assertEqual(public["live_boundary"], private["live_before"])
        self.assertTrue(public["live_boundary_unchanged"])
        for actual, source in zip(public["checkpoints"], private["checkpoints"]):
            for key in ("input_digest", "provenance_digest", "exports", "analytical_sha256", "outcomes"):
                self.assertEqual(actual[key], source["driver"][key])
            self.assertNotIn("driver", actual)
            self.assertTrue(all(row["byte_length"] > 0 for row in actual["byte_audits"]
                                if row["surface"] in {"status", "summary", "publication"}))
        self.assertLess(len(json.dumps(public)), len(json.dumps(private)))

    def test_all_public_objects_are_closed_required_nonnull_and_ordered(self):
        module = public_module()
        original = module.project_hosted_replay_public(envelope()).to_dict()
        schema = json.loads((ROOT / "contracts/hosted-replay-public-v1.schema.json").read_text())
        def check(value, contract, path=()):
            if isinstance(value, dict):
                for key in value:
                    for mutation in ("missing", "null"):
                        changed = copy.deepcopy(original)
                        target = changed
                        for part in path:
                            target = target[part]
                        if mutation == "missing":
                            target.pop(key)
                        elif contract["properties"][key].get("type") == "null":
                            target[key] = "unapproved"
                        else:
                            target[key] = None
                        with self.assertRaises(module.HostedReplayPublicError):
                            module.decode_hosted_replay_public(changed)
                changed = copy.deepcopy(original)
                target = changed
                for part in path:
                    target = target[part]
                target["unapproved_field"] = "excluded"
                with self.assertRaises(module.HostedReplayPublicError):
                    module.decode_hosted_replay_public(changed)
                if len(value) > 1:
                    changed = copy.deepcopy(original)
                    target = changed
                    for part in path[:-1]:
                        target = target[part]
                    reversed_object = dict(reversed(list(value.items())))
                    if path:
                        target[path[-1]] = reversed_object
                    else:
                        changed = reversed_object
                    with self.assertRaises(module.HostedReplayPublicError):
                        module.decode_hosted_replay_public(changed)
                for key, item in value.items():
                    check(item, contract["properties"][key], (*path, key))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    check(item, contract["items"], (*path, index))
        check(original, schema)

    def test_wrong_variants_and_tampered_private_envelopes_are_rejected(self):
        module = public_module()
        for candidate in (envelope().to_dict(), envelope()["checkpoints"][0]["driver"], None):
            with self.assertRaises(module.HostedReplayPublicError):
                module.decode_hosted_replay_public(candidate)
        changed = envelope().to_dict()
        changed["jobs"][0]["conclusion"] = "failure"
        with self.assertRaises(module.HostedReplayPublicError):
            module.project_hosted_replay_public(changed)

    def test_value_object_is_immutable_and_does_not_alias_envelope(self):
        module = public_module()
        source = envelope().to_dict()
        public = module.project_hosted_replay_public(source)
        saved = public.to_json()
        source["jobs"][0]["conclusion"] = "failure"
        public.to_dict()["jobs"][0]["conclusion"] = "failure"
        self.assertEqual(public.to_json(), saved)
        with self.assertRaises(AttributeError):
            public.encoded = b"changed"

    def test_duplicate_nonfinite_and_raw_private_fields_fail_without_echo(self):
        module = public_module()
        for raw in ('{"schema_version":1,"schema_version":2}', '{"unapproved":NaN}'):
            with self.assertRaises(module.HostedReplayPublicError) as caught:
                module.decode_hosted_replay_public(raw)
            self.assertEqual(str(caught.exception), caught.exception.category)
        original = module.project_hosted_replay_public(envelope()).to_dict()
        for field in ("raw_logs", "job_outputs", "local_path", "canary", "approval_locator", "owner"):
            changed = copy.deepcopy(original)
            changed[field] = "unapproved"
            with self.assertRaises(module.HostedReplayPublicError):
                module.decode_hosted_replay_public(changed)

    def test_reordered_lists_and_empty_audits_fail_closed(self):
        module = public_module()
        original = module.project_hosted_replay_public(envelope()).to_dict()
        for key in ("jobs", "worker_matrix", "checkpoints", "log_audit_commitments"):
            changed = copy.deepcopy(original)
            changed[key].reverse()
            with self.assertRaises(module.HostedReplayPublicError):
                module.decode_hosted_replay_public(changed)
        for key in ("exports", "byte_audits"):
            changed = copy.deepcopy(original)
            changed["checkpoints"][0][key].reverse()
            with self.assertRaises(module.HostedReplayPublicError):
                module.decode_hosted_replay_public(changed)
        changed = copy.deepcopy(original)
        changed["log_audit_commitments"][0]["audit"]["byte_length"] = 0
        with self.assertRaises(module.HostedReplayPublicError):
            module.decode_hosted_replay_public(changed)


class HostedReplayEvidenceClassTests(unittest.TestCase):
    def test_complete_live_boundary_survives_exact_projection_and_structured_rendering(self):
        module = public_module()
        source = envelope().to_dict()
        projected = module.project_hosted_replay_public(source)
        public = module.decode_hosted_replay_public(projected.to_json()).to_dict()
        boundary = public["live_boundary"]
        self.assertEqual(boundary, source["live_before"])
        self.assertEqual(boundary, source["live_after"])
        rendered = module.render_hosted_replay_markdown(public)
        self.assertIn(f'Published-data commit: `{boundary["published_data_commit"]}`.', rendered)
        self.assertIn(f'Published-data tree: `{boundary["published_data_tree"]}`.', rendered)
        self.assertIn(f'Published manifest raw-content SHA-256: `{boundary["published_manifest_sha256"]}`.', rendered)
        self.assertIn("Private archive inventory: not observed by this replay; no archive digest is asserted.", rendered)
        for row in boundary["protected_refs"]:
            self.assertIn(f'| {row["ref"]} | {row["sha"]} |', rendered)
        for row in boundary["exports"]:
            self.assertIn(f'| {row["export_name"]} | {row["row_count"]} | {row["sha256"]} |', rendered)
        for row in boundary["controls"]:
            self.assertIn(f'| {row["file_name"]} | {row["sha256"]} |', rendered)
        self.assertNotIn("[{'", rendered)
        self.assertNotIn("{'", rendered)
        source["live_before"]["protected_refs"][0]["sha"] = "0" * 40
        self.assertEqual(module.decode_hosted_replay_public(projected).to_dict(), public)

    def test_public_decoder_rejects_incomplete_duplicate_reordered_and_inconsistent_live_inventory(self):
        module = public_module()
        original = module.project_hosted_replay_public(envelope()).to_dict()
        mutations = [lambda d: d["protected_refs"].pop(0),
            lambda d: d["protected_refs"].reverse(),
            lambda d: d["protected_refs"].append(copy.deepcopy(d["protected_refs"][-1])),
            lambda d: d["protected_refs"][0].update(ref="refs/heads/../main"),
            lambda d: d.update(published_data_commit="0" * 40),
            lambda d: d["exports"].reverse(), lambda d: d["exports"].pop(),
            lambda d: d["exports"].__setitem__(1, copy.deepcopy(d["exports"][0])),
            lambda d: d["exports"][0].update(export_name="unapproved"),
            lambda d: d["controls"].reverse(), lambda d: d["controls"].pop(),
            lambda d: d["controls"].__setitem__(1, copy.deepcopy(d["controls"][0])),
            lambda d: d.update(archive_inventory_sha256="0" * 64)]
        for mutate in mutations:
            changed = copy.deepcopy(original)
            mutate(changed["live_boundary"])
            with self.assertRaises(module.HostedReplayPublicError):
                module.decode_hosted_replay_public(changed)

    def test_manifest_export_and_control_drift_cannot_be_projected(self):
        module = public_module()
        original = envelope().to_dict()
        candidates = []
        manifest = copy.deepcopy(original)
        manifest["live_after"]["published_manifest_sha256"] = "0" * 64
        candidates.append(manifest)
        for field in ("protected_refs", "exports", "controls"):
            for index in range(len(original["live_after"][field])):
                for key in (["sha256", "row_count"] if field == "exports" else ["sha" if field == "protected_refs" else "sha256"]):
                    changed = copy.deepcopy(original)
                    changed["live_after"][field][index][key] = 100 if key == "row_count" else "0" * (40 if key == "sha" else 64)
                    if field == "protected_refs" and changed["live_after"][field][index]["ref"] == "refs/heads/published-data":
                        changed["live_after"]["published_data_commit"] = "0" * 40
                    candidates.append(changed)
        for candidate in candidates:
            with self.assertRaises(module.HostedReplayPublicError):
                module.project_hosted_replay_public(candidate)

    def test_rendering_is_deterministic_and_separates_four_evidence_classes(self):
        module = public_module()
        public = module.project_hosted_replay_public(envelope())
        rendered = module.render_hosted_replay_markdown(public)
        self.assertEqual(rendered, module.render_hosted_replay_markdown(public.to_dict()))
        for phrase in ("workflow_dispatch", "fixture_hosted_replay", "live_source_observation",
                       "actual_schedule_observation", "real_restore_republish_observation",
                       "reconstructed", "status timestamps", "historical"):
            self.assertIn(phrase, rendered)

    def test_fixture_capture_and_dispatched_calendar_cannot_be_relabelled(self):
        module = public_module()
        original = module.project_hosted_replay_public(envelope()).to_dict()
        for key in ("live_source_observation", "actual_schedule_observation"):
            changed = copy.deepcopy(original)
            changed["evidence_classes"][key] = "measured"
            with self.assertRaises(module.HostedReplayPublicError):
                module.decode_hosted_replay_public(changed)
        changed = copy.deepcopy(original)
        changed["run"]["event"] = "schedule"
        with self.assertRaises(module.HostedReplayPublicError):
            module.decode_hosted_replay_public(changed)

    def test_immutable_historical_tuple_is_preserved_and_cannot_be_changed(self):
        module = public_module()
        original = module.project_hosted_replay_public(envelope()).to_dict()
        private = envelope()["historical_real_republish"]
        for key in ("run_id", "head_sha", "published_data_commit", "published_manifest_sha256"):
            self.assertEqual(original["historical_real_republish"][key], private[key])
            changed = copy.deepcopy(original)
            changed["historical_real_republish"][key] = 2 if key == "run_id" else "0" * len(private[key])
            with self.assertRaises(module.HostedReplayPublicError):
                module.decode_hosted_replay_public(changed)


class HostedReplayGeneratedPairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "envelope.json"
        self.json_path = self.root / "generated.json"
        self.markdown_path = self.root / "generated.md"

    def generate(self):
        self.source.write_text(envelope().to_json(), encoding="utf-8")
        public_module().generate_hosted_replay_pair(self.source, self.json_path, self.markdown_path, root=ROOT)

    def test_generation_and_check_authorities_exist_before_observation(self):
        module = public_module()
        self.assertTrue(callable(getattr(module, "generate_hosted_replay_pair", None)),
                        "atomic pair generation authority is required before observation")
        self.assertTrue(callable(getattr(module, "check_hosted_replay_pair", None)))

    def test_exact_round_trip_checks_envelope_public_and_markdown(self):
        self.generate()
        module = public_module()
        module.check_hosted_replay_pair(self.json_path, self.markdown_path)
        module.check_hosted_replay_against_envelope(self.source, self.json_path, self.markdown_path, root=ROOT)
        self.assertEqual(self.json_path.read_text(), module.project_hosted_replay_public(envelope()).to_json())
        self.assertEqual(self.markdown_path.read_text(), module.render_hosted_replay_markdown(self.json_path.read_bytes()))

    def test_drift_in_either_file_fails_without_repair(self):
        self.generate()
        module = public_module()
        for path in (self.json_path, self.markdown_path):
            original = path.read_bytes()
            path.write_bytes(original + b"\n")
            with self.assertRaises(module.HostedReplayPublicError):
                module.check_hosted_replay_pair(self.json_path, self.markdown_path)
            self.assertEqual(path.read_bytes(), original + b"\n")
            path.write_bytes(original)

    def test_changed_job_digest_run_or_boundary_fails_before_writes(self):
        self.generate()
        module = public_module()
        before = (self.json_path.read_bytes(), self.markdown_path.read_bytes())
        candidates = []
        changed = envelope().to_dict()
        changed["jobs"][0]["conclusion"] = "failure"
        candidates.append(changed)
        changed = envelope().to_dict()
        changed["checkpoints"][-1]["driver"]["input_digest"] = "0" * 64
        candidates.append(changed)
        changed = envelope().to_dict()
        changed["live_after"]["published_data_tree"] = "0" * 40
        candidates.append(changed)
        for candidate in candidates:
            self.source.write_text(json.dumps(candidate))
            with self.assertRaises(module.HostedReplayPublicError):
                module.generate_hosted_replay_pair(self.source, self.json_path, self.markdown_path, root=ROOT)
            self.assertEqual((self.json_path.read_bytes(), self.markdown_path.read_bytes()), before)

    def test_semantically_valid_but_stale_pair_fails_against_envelope(self):
        self.generate()
        module = public_module()
        original = envelope().to_dict()
        for field in ("job", "audit", "boundary"):
            changed = copy.deepcopy(original)
            if field == "job":
                changed["jobs"][0]["job_id"] += 100
            elif field == "audit":
                changed["raw_log_audits"][0]["audit"]["sha256"] = "0" * 64
            else:
                changed["live_before"]["published_manifest_sha256"] = "0" * 64
                changed["live_after"]["published_manifest_sha256"] = "0" * 64
            self.source.write_text(json.dumps(changed))
            with self.assertRaises(module.HostedReplayPublicError):
                module.check_hosted_replay_against_envelope(self.source, self.json_path, self.markdown_path, root=ROOT)

    def test_second_replace_failure_rolls_back_both_existing_outputs(self):
        self.generate()
        module = public_module()
        self.json_path.write_bytes(b"prior-json\n")
        self.markdown_path.write_bytes(b"prior-markdown\n")
        original = os.replace
        writes = []
        def fail_second(source, destination):
            writes.append(destination)
            if len(writes) == 2:
                raise OSError("controlled replacement failure")
            return original(source, destination)
        with patch.object(module.os, "replace", side_effect=fail_second):
            with self.assertRaises(module.HostedReplayPublicError):
                module.generate_hosted_replay_pair(self.source, self.json_path, self.markdown_path, root=ROOT)
        self.assertEqual(self.json_path.read_bytes(), b"prior-json\n")
        self.assertEqual(self.markdown_path.read_bytes(), b"prior-markdown\n")
        self.assertFalse(list(self.root.glob(".hosted-replay-*.tmp")))

    def test_new_pair_failure_leaves_neither_output(self):
        module = public_module()
        self.source.write_text(envelope().to_json())
        original = os.replace
        calls = []
        def fail_second(source, destination):
            calls.append(destination)
            if len(calls) == 2:
                raise OSError("controlled replacement failure")
            return original(source, destination)
        with patch.object(module.os, "replace", side_effect=fail_second):
            with self.assertRaises(module.HostedReplayPublicError):
                module.generate_hosted_replay_pair(self.source, self.json_path, self.markdown_path, root=ROOT)
        self.assertFalse(self.json_path.exists())
        self.assertFalse(self.markdown_path.exists())

    def test_post_scan_staged_byte_change_never_publishes_pair(self):
        module = public_module()
        self.source.write_text(envelope().to_json())
        original = module._stage
        def changed_staged_bytes(path, raw):
            temporary = original(path, raw)
            temporary.write_bytes(b"changed after validation")
            return temporary
        with patch.object(module, "_stage", side_effect=changed_staged_bytes):
            with self.assertRaises(module.HostedReplayPublicError):
                module.generate_hosted_replay_pair(self.source, self.json_path, self.markdown_path, root=ROOT)
        self.assertFalse(self.json_path.exists())
        self.assertFalse(self.markdown_path.exists())

    def test_readers_reject_an_in_progress_pair(self):
        self.generate()
        module = public_module()
        with module._pair_lock(self.json_path, self.markdown_path):
            with self.assertRaises(module.HostedReplayPublicError):
                module.check_hosted_replay_pair(self.json_path, self.markdown_path)

    def test_existing_repository_scans_reject_invalid_generated_evidence(self):
        from tools.citation_scan import scanner
        module = public_module()
        public_path = self.root / module.JSON_PATH
        markdown_path = self.root / module.MARKDOWN_PATH
        public_path.parent.mkdir(parents=True)
        markdown_path.parent.mkdir(parents=True)
        public_path.write_bytes(b"{}")
        markdown_path.write_bytes(b"unapproved")
        with self.assertRaises(module.HostedReplayPublicError):
            module.check_repository_hosted_replay(self.root)
        with self.assertRaises(scanner.CitationError):
            scanner.scan_citations(self.root)

    def test_symlinks_aliases_and_wrong_variants_do_not_write(self):
        module = public_module()
        self.source.write_text(envelope().to_json())
        target = self.root / "target.json"
        target.write_bytes(b"preserved")
        self.json_path.symlink_to(target)
        with self.assertRaises(module.HostedReplayPublicError):
            module.generate_hosted_replay_pair(self.source, self.json_path, self.markdown_path, root=ROOT)
        self.assertEqual(target.read_bytes(), b"preserved")
        self.assertFalse(self.markdown_path.exists())
        self.json_path.unlink()
        with self.assertRaises(module.HostedReplayPublicError):
            module.generate_hosted_replay_pair(self.source, self.source, self.markdown_path, root=ROOT)
        self.source.write_text(module.project_hosted_replay_public(envelope()).to_json())
        with self.assertRaises(module.HostedReplayPublicError):
            module.generate_hosted_replay_pair(self.source, self.json_path, self.markdown_path, root=ROOT)

    def test_cli_round_trip_and_drift_have_category_only_diagnostics(self):
        from tools.docs_public.__main__ import main
        import contextlib
        import io
        self.source.write_text(envelope().to_json())
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assertEqual(main(["hosted-replay-generate", "--envelope", str(self.source),
                                   "--json-output", str(self.json_path), "--markdown-output", str(self.markdown_path)]), 0)
            self.assertEqual(main(["hosted-replay-check", "--envelope", str(self.source),
                                   "--json", str(self.json_path), "--markdown", str(self.markdown_path)]), 0)
            self.markdown_path.write_bytes(self.markdown_path.read_bytes() + b"changed")
            self.assertEqual(main(["hosted-replay-check", "--envelope", str(self.source),
                                   "--json", str(self.json_path), "--markdown", str(self.markdown_path)]), 1)
        self.assertNotIn(str(self.root), output.getvalue())
        self.assertIn("hosted_replay.generated_drift", output.getvalue())

    def test_hard_linked_outputs_preserve_the_alias_and_do_not_publish(self):
        module = public_module()
        self.source.write_text(envelope().to_json(), encoding="utf-8")
        target = self.root / "preserved.json"
        target.write_bytes(b"preserved output\n")
        os.link(target, self.json_path)
        with self.assertRaises(module.HostedReplayPublicError):
            module.generate_hosted_replay_pair(self.source, self.json_path, self.markdown_path, root=ROOT)
        self.assertEqual(target.read_bytes(), b"preserved output\n")
        self.assertEqual(self.json_path.read_bytes(), b"preserved output\n")
        self.assertFalse(self.markdown_path.exists())


class HostedReplayCitationTests(unittest.TestCase):
    def test_citation_authority_exists(self):
        from tools.citation_scan import scanner
        self.assertTrue(callable(getattr(scanner, "check_hosted_replay_citations", None)),
                        "exact hosted replay citation authority is required")

    def test_safe_github_run_commit_and_repository_locators_are_accepted(self):
        from tools.citation_scan import scanner
        module = public_module()
        public = module.project_hosted_replay_public(envelope())
        scanner.check_hosted_replay_citations(ROOT, public, module.render_hosted_replay_markdown(public))

    def test_private_raw_log_job_output_and_approval_locators_are_rejected(self):
        from tools.citation_scan import scanner
        module = public_module()
        public = module.project_hosted_replay_public(envelope())
        original = module.render_hosted_replay_markdown(public)
        # Synthetic locators contain no identities, credentials or contact forms.
        for suffix in ("/logs", "?job_outputs=excluded", "/private-approval", "/calico-build/.planning"):
            changed = original + "\n[unapproved](https://github.com/mrnouiouat/calico" + suffix + ")\n"
            with self.assertRaises(scanner.CitationError):
                scanner.check_hosted_replay_citations(ROOT, public, changed)
        changed = public.to_dict()
        changed["run"]["repository"] = "fixture/unapproved"
        with self.assertRaises(scanner.CitationError):
            scanner.check_hosted_replay_citations(ROOT, changed, original)

    def test_invented_future_head_and_self_sha_evidence_are_rejected(self):
        from tools.citation_scan import scanner
        module = public_module()
        public = module.project_hosted_replay_public(envelope()).to_dict()
        invented = copy.deepcopy(public)
        invented["run"]["head_sha"] = "0" * 40
        invented["run"]["commit_url"] = "https://github.com/mrnouiouat/calico/commit/" + "0" * 40
        with self.assertRaises(scanner.CitationError):
            scanner.check_hosted_replay_citations(ROOT, invented, module.render_hosted_replay_markdown(invented))
        original_git = scanner.run_git
        def deployment_already_contains_evidence(args, root):
            if args[:2] == ["ls-tree", "--name-only"] and args[2] == public["run"]["head_sha"]:
                return (module.JSON_PATH + "\n" + module.MARKDOWN_PATH + "\n").encode()
            return original_git(args, root)
        with patch.object(scanner, "run_git", side_effect=deployment_already_contains_evidence):
            with self.assertRaises(scanner.CitationError):
                scanner.check_hosted_replay_citations(ROOT, public, module.render_hosted_replay_markdown(public))


if __name__ == "__main__":
    unittest.main()
