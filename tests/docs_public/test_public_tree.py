"""Composition of the existing publication, documentation and privacy gates."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from tools.privacy_scan.policy import PolicyError, load_policy
from tools.privacy_scan.scanner import ScanPathError, check_path_rules, scan, scan_paths
from tests.tools.privacy_scan.test_non_echo import TempGitRepo


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "policies/publishable-tree.json"
WORKFLOWS = ("privacy-gate.yml", "dbt-fixture.yml")
V3_SHA256 = "e0c7a9e4507a771ccf2d4c14434a5fcbdc8a52f81819ebc991dd55acc2795fc5"


def workflow(name):
    return (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")


def scripts(text):
    """Read the existing workflows' plain, literal run blocks."""
    lines = text.splitlines()
    result = []
    for index, line in enumerate(lines):
        if not line.startswith("        run: "):
            continue
        command = line.removeprefix("        run: ")
        if command == "|":
            body = []
            for following in lines[index + 1:]:
                if not following.startswith("          "):
                    break
                body.append(following[10:])
            command = "\n".join(body)
        result.append(command)
    return result


class PublicBoundaryContracts(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy(POLICY_PATH)

    def test_planning_prefix_denied_without_adjacent_collision(self):
        for name in (".planning/STATE.md", ".planning/nested/evidence.json"):
            findings = check_path_rules(name, self.policy)
            self.assertEqual([f.category for f in findings], ["forbidden_path"])
        for name in (".planning-note", "docs/provenance/index-v1.json", "docs/decisions/register.md"):
            self.assertEqual(check_path_rules(name, self.policy), [])

    def test_ignore_prevents_planning_only(self):
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--stdin"], cwd=ROOT,
            input=".planning/STATE.md\n.planning-note\ndocs/provenance/index-v1.json\n",
            text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines(), [".planning/STATE.md"])

    def test_policy_schema_and_v3_field_authority_stay_closed(self):
        document = json.loads(POLICY_PATH.read_bytes())
        self.assertEqual(set(document), {"policy_version", "max_blob_bytes", "forbidden_paths"})
        self.assertEqual(hashlib.sha256((ROOT / "contracts/publication-exports-v3.json").read_bytes()).hexdigest(), V3_SHA256)
        for unknown in ("content_classes", "allowlist", "allowed_paths"):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "policy.json"
                path.write_text(json.dumps({**document, unknown: []}), encoding="utf-8")
                with self.assertRaises(PolicyError) as failure:
                    load_policy(path)
                self.assertEqual(str(failure.exception), "invalid_policy_schema")

    def test_invalid_candidate_sets_fail_and_one_entry_scans(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.md").write_text("Synthetic Observatory Fund\n", encoding="utf-8")
            (root / "b.md").write_text("Public evidence\n", encoding="utf-8")
            for paths in (None, [], ["b.md", "a.md"], ["a.md", "a.md"], ["../a.md"], ["./a.md"]):
                with self.assertRaises(ScanPathError) as failure:
                    scan_paths(root, paths, self.policy)
                self.assertEqual(str(failure.exception), "privacy_scan.invalid_path_list")
            self.assertEqual(scan_paths(root, ["a.md"], self.policy), [])

    def test_symlink_directory_and_missing_candidates_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "directory").mkdir()
            (root / "regular.md").write_text("Public evidence\n", encoding="utf-8")
            (root / "linked.md").symlink_to(root / "regular.md")
            for name in ("directory", "linked.md", "missing.md"):
                with self.assertRaises(ScanPathError) as failure:
                    scan_paths(root, [name], self.policy)
                self.assertEqual(str(failure.exception), "privacy_scan.non_regular_file")

    def test_named_organization_registration_and_verification_remain_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            name = "docs/organization.md"
            (root / "docs").mkdir()
            (root / name).write_text(
                "Organization: Synthetic Observatory Fund\n"
                "State Charity Reg#: 0012345\nState Charity Reg#: CT0000123\n"
                "State Charity Reg#: EX0000123\nCity: Sacramento\nState: CA\n"
                "https://rct.doj.ca.gov/verification/Web/Search.aspx\n", encoding="utf-8")
            self.assertEqual(scan_paths(root, [name], self.policy), [])

    def test_excluded_content_is_rejected_without_reflecting_values(self):
        # Split reserved test values so committed test source is itself safe.
        cases = (
            ("FEIN: " + "94-" + "7654321", "fein"),
            ("123 " + "Synthetic Street", "street_address"),
            ("synthetic" + "@example.invalid", "contact_info"),
            ("555-" + "123-4567", "contact_info"),
            ("/" + "Users/synthetic/private-note", "absolute_local_path"),
            ("https://example.invalid/join?" + "ein=" + "synthetic", "unapproved_join_field"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for content, category in cases:
                (root / "notes.md").write_text(content + "\n", encoding="utf-8")
                findings = scan_paths(root, ["notes.md"], self.policy)
                self.assertTrue(any(f.category == category for f in findings), category)
                self.assertTrue(all(content not in f.render() for f in findings))

    def test_forbidden_artifact_paths_remain_denied(self):
        for name in ("data/raw/source.csv", "data/generated-diffs/diff.csv", "source.pdf", "private.duckdb"):
            self.assertTrue(check_path_rules(name, self.policy))

    def test_staged_candidate_and_removed_planning_history_are_denied(self):
        with TempGitRepo() as repo:
            repo.write_file("public.md", b"Public evidence\n")
            repo.add("public.md")
            repo.commit("Add public evidence")
            repo.write_file(".planning/notes.md", b"Withheld planning\n")
            repo.add(".planning/notes.md")
            candidate = subprocess.run(["git", "write-tree"], cwd=repo.path,
                                       text=True, capture_output=True, check=True).stdout.strip()
            findings = scan(treeish=candidate, history_all=True, repo_dir=repo.path, policy=self.policy)
            self.assertTrue(any(f.category == "forbidden_path" for f in findings))
            repo.commit("Add synthetic planning")
            (repo.path / ".planning/notes.md").unlink()
            repo.add(".planning/notes.md")
            repo.commit("Remove synthetic planning")
            self.assertEqual(scan(treeish="HEAD", history_all=False, repo_dir=repo.path, policy=self.policy), [])
            findings = scan(treeish="HEAD", history_all=True, repo_dir=repo.path, policy=self.policy)
            self.assertTrue(any(f.category == "forbidden_path" for f in findings))

    def test_current_documentation_inventories_and_generators_compose(self):
        from tools.citation_scan.scanner import check_repository
        from tools.docs_public.readme import check_readme
        from tools.docs_public.lineage import validate_projection
        check_readme(ROOT)
        counts = check_repository(ROOT)
        self.assertGreater(counts["occurrences"], 0)
        graph = json.loads((ROOT / "docs/evidence/dbt-lineage-v1.json").read_bytes())
        validate_projection(graph, project_dir=ROOT / "dbt")
        self.assertGreater(len(graph["nodes"]), 0)


class ReadOnlyWorkflowContracts(unittest.TestCase):
    def test_workflows_use_pinned_read_only_complete_checkouts(self):
        for name in WORKFLOWS:
            text = workflow(name)
            self.assertIn("permissions:\n  contents: read\n", text)
            self.assertIn("defaults:\n  run:\n    shell: bash\n", text)
            self.assertIn("fetch-depth: 0", text)
            self.assertIn("persist-credentials: false", text)
            pins = re.findall(r"uses: ([^\s]+)", text)
            self.assertGreater(len(pins), 0)
            self.assertTrue(all(re.fullmatch(r"actions/(?:checkout|setup-python)@[0-9a-f]{40}", p) for p in pins))
            for prohibited in ("continue-on-error", "|| true", "set +e", "secrets.", "contents: write",
                               "git push", "upload-artifact", "--mode real", "if: always()", "tools.citation_scan --write"):
                self.assertNotIn(prohibited, text)

    def test_privacy_workflow_requires_all_phase_gates_before_history(self):
        text = workflow("privacy-gate.yml")
        gates = (
            "python -m tools.docs_public check",
            "python -m tools.citation_scan --check",
            "python -m unittest discover -s tests/docs_public -t . -v",
            "python -m unittest tests.tools.privacy_scan.test_non_echo -v",
            "python -m tools.privacy_scan --tree HEAD --history-all",
        )
        self.assertTrue(all(g in text for g in gates))
        self.assertEqual([text.index(g) for g in gates], sorted(text.index(g) for g in gates))
        self.assertIn("python -m pip install --requirement requirements-dbt.txt", text)

    def test_fixture_docs_produces_lineage_before_drift_checks(self):
        text = workflow("dbt-fixture.yml")
        docs = "python -m calico_dbt docs --mode fixture"
        self.assertIn("python -m tools.docs_public check", text)
        self.assertIn("git diff --exit-code -- docs/evidence/dbt-lineage-v1.json", text)
        self.assertLess(text.index(docs), text.index("python -m tools.docs_public check"))
        self.assertLess(text.index(docs), text.index("git diff --exit-code -- docs/evidence/dbt-lineage-v1.json"))

    def test_gate_failure_or_interruption_cannot_produce_success(self):
        commands = [script for name in WORKFLOWS for script in scripts(workflow(name))
                    if any(gate in script for gate in ("tools.docs_public", "tools.citation_scan", "tools.privacy_scan"))]
        self.assertGreater(len(commands), 0)
        for script in commands:
            for failure in ("return 7", 'kill -TERM "$$"'):
                result = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c",
                                         "python() { " + failure + "; };\n" + script], capture_output=True)
                self.assertNotEqual(result.returncode, 0)

    def test_concurrent_verification_results_are_independent(self):
        script = next(s for s in scripts(workflow("privacy-gate.yml")) if "tools.docs_public" in s)
        def run(code):
            return subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c",
                                   "python() { return " + str(code) + "; };\n" + script], capture_output=True).returncode
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, (0, 7)))
        self.assertEqual(results, [0, 7])


if __name__ == "__main__":
    unittest.main()
