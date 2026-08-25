"""Contract tests for the clean-root admission manifest (`seed-manifest.json`)
and the resulting target root commit.

Three concerns, three test classes:

  * ``SeedManifestSchemaTests`` -- static shape/closure of the manifest
    itself (schema version, exact key set, sorted/unique/closed path list,
    exclusion of pre-smoke candidate paths and every forbidden prefix).
  * ``TemporaryStagingEquivalenceTests`` -- proves NUL-safe set equality
    between the manifest's ``seed_paths`` and an actually-staged Git index,
    using a disposable temporary repository (never the real target, and
    never Calico-build).
  * ``TargetRootCommitTests`` -- proves the real target repository's HEAD
    tree equals the manifest allowlist exactly, that exactly one
    independently created root commit exists, and that no unlisted/private/
    evolution path or forbidden prefix is reachable from it.

Never echoes matched content, file bytes, or private path values -- only
membership/equality/count assertions over paths already known to be public
(D-10).

Run:
    py -V:3.13 -m unittest tests.tools.privacy_scan.test_seed_manifest -v
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "seed-manifest.json"

#: Forbidden path prefixes that must never appear in the admitted seed set,
#: per the plan's own exclusion list (private planning, raw/data rows,
#: research scripts, production dbt models, local envs, dbt artifacts).
_FORBIDDEN_PREFIXES = (
    ".planning/",
    "data/",
    "scripts/research/",
    "models/",
    ".venv/",
    "venv/",
    "target/",
    "dbt_packages/",
)

#: Excluded from admission until the runtime smoke proof succeeds
#: (Task 2's responsibility, not Task 1's).
_PRE_SMOKE_EXCLUDED_PATHS = frozenset(
    {
        ".python-version",
        "requirements-dbt.txt",
        "tests/fixtures/dbt_adapter_smoke/dbt_project.yml",
        "tests/fixtures/dbt_adapter_smoke/profiles.yml",
        "tests/fixtures/dbt_adapter_smoke/models/adapter_smoke.sql",
        "docs/evidence/dbt-toolchain-smoke.json",
    }
)

_REQUIRED_SEED_MEMBERS = (
    "tools/privacy_scan/__init__.py",
    "tools/privacy_scan/policy.py",
    "tools/privacy_scan/git_objects.py",
    "tools/privacy_scan/scanner.py",
    "tools/privacy_scan/__main__.py",
    "policies/publishable-tree.json",
    "tests/test_repository_contract.py",
    "tests/test_redaction_contract.py",
    "docs/ag-registry-migration-2026-08.md",
    "docs/redactions/ag-registry-migration-2026-08.json",
    ".gitignore",
    "CLAUDE.md",
    "seed-manifest.json",
)


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )


class SeedManifestSchemaTests(unittest.TestCase):
    """Static shape/closure of `seed-manifest.json`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = _load_manifest()

    def test_manifest_exists(self) -> None:
        self.assertTrue(MANIFEST_PATH.is_file())

    def test_manifest_is_json_object(self) -> None:
        self.assertIsInstance(self.manifest, dict)

    def test_manifest_key_set_is_exact_and_closed(self) -> None:
        self.assertEqual(
            set(self.manifest.keys()), {"schema_version", "target_repository", "seed_paths"}
        )

    def test_schema_version_is_one(self) -> None:
        self.assertEqual(self.manifest["schema_version"], 1)

    def test_target_repository_is_exact(self) -> None:
        self.assertEqual(self.manifest["target_repository"], "mrnouiouat/calico")

    def test_seed_paths_is_sorted_unique_and_nonempty(self) -> None:
        paths = self.manifest["seed_paths"]
        self.assertIsInstance(paths, list)
        self.assertGreater(len(paths), 0)
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(len(paths), len(set(paths)))

    def test_seed_paths_are_posix_relative_no_traversal(self) -> None:
        for path in self.manifest["seed_paths"]:
            self.assertNotIn("\\", path)
            self.assertFalse(path.startswith("/"))
            self.assertNotIn("\x00", path)
            self.assertNotIn("..", path.split("/"))

    def test_manifest_contains_itself(self) -> None:
        self.assertIn("seed-manifest.json", self.manifest["seed_paths"])

    def test_manifest_excludes_pre_smoke_candidate_paths(self) -> None:
        paths = set(self.manifest["seed_paths"])
        self.assertTrue(paths.isdisjoint(_PRE_SMOKE_EXCLUDED_PATHS))

    def test_manifest_excludes_forbidden_prefixes(self) -> None:
        for path in self.manifest["seed_paths"]:
            for forbidden in _FORBIDDEN_PREFIXES:
                self.assertFalse(
                    path.startswith(forbidden),
                    f"seed path unexpectedly under forbidden prefix {forbidden!r}",
                )

    def test_manifest_includes_required_seed_members(self) -> None:
        paths = set(self.manifest["seed_paths"])
        for required in _REQUIRED_SEED_MEMBERS:
            self.assertIn(required, paths)

    def test_every_manifest_path_exists_on_disk(self) -> None:
        for path in self.manifest["seed_paths"]:
            self.assertTrue((REPO_ROOT / path).is_file(), f"missing on disk: {path}")


class TemporaryStagingEquivalenceTests(unittest.TestCase):
    """Proves NUL-safe staged-index equality against the manifest, using a
    disposable temporary repository -- never the real target."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = _load_manifest()
        cls.seed_paths: list[str] = cls.manifest["seed_paths"]

    def _new_repo(self) -> Path:
        tmp = Path(tempfile.mkdtemp())
        _run_git(["init", "--initial-branch=main"], tmp)
        _run_git(["config", "user.email", "synthetic" + "@example.invalid"], tmp)
        _run_git(["config", "user.name", "Synthetic Test User"], tmp)
        return tmp

    def _copy_seed_paths(self, dest: Path, paths: list[str]) -> None:
        for rel in paths:
            source = REPO_ROOT / rel
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())

    def _staged_paths(self, repo: Path) -> list[str]:
        result = _run_git(["diff", "--cached", "--name-only", "-z"], repo)
        raw = result.stdout.split(b"\x00")
        return sorted(p.decode("utf-8") for p in raw if p)

    def test_staging_exact_manifest_paths_matches_manifest(self) -> None:
        repo = self._new_repo()
        try:
            self._copy_seed_paths(repo, self.seed_paths)
            for rel in self.seed_paths:
                _run_git(["add", "--", rel], repo)
            self.assertEqual(self._staged_paths(repo), sorted(self.seed_paths))
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_staging_an_unlisted_extra_path_breaks_equality(self) -> None:
        repo = self._new_repo()
        try:
            self._copy_seed_paths(repo, self.seed_paths)
            for rel in self.seed_paths:
                _run_git(["add", "--", rel], repo)
            extra = repo / "unlisted-evolution-file.txt"
            extra.write_text("synthetic unlisted content\n", encoding="utf-8")
            _run_git(["add", "--", "unlisted-evolution-file.txt"], repo)
            self.assertNotEqual(self._staged_paths(repo), sorted(self.seed_paths))
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_omitting_a_manifest_path_breaks_equality(self) -> None:
        repo = self._new_repo()
        try:
            reduced = self.seed_paths[1:]
            self._copy_seed_paths(repo, reduced)
            for rel in reduced:
                _run_git(["add", "--", rel], repo)
            self.assertNotEqual(self._staged_paths(repo), sorted(self.seed_paths))
        finally:
            shutil.rmtree(repo, ignore_errors=True)


class TargetRootCommitTests(unittest.TestCase):
    """Proves the real target repository's committed HEAD matches the
    manifest exactly, with exactly one independently created root commit and
    no reachable Calico-build object or unlisted/forbidden path."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = _load_manifest()

    def _head_paths(self) -> list[str]:
        result = _run_git(["ls-tree", "-rz", "--name-only", "--full-tree", "HEAD"], REPO_ROOT)
        raw = result.stdout.split(b"\x00")
        return sorted(p.decode("utf-8") for p in raw if p)

    def test_exactly_one_root_commit(self) -> None:
        result = _run_git(["rev-list", "--max-parents=0", "--all", "--count"], REPO_ROOT)
        self.assertEqual(result.stdout.decode("ascii").strip(), "1")

    def test_head_tree_matches_manifest_seed_paths_exactly(self) -> None:
        self.assertEqual(self._head_paths(), sorted(self.manifest["seed_paths"]))

    def test_no_remote_configured(self) -> None:
        result = _run_git(["remote"], REPO_ROOT)
        self.assertEqual(result.stdout.strip(), b"")

    def test_no_alternates_object_database(self) -> None:
        alternates = REPO_ROOT / ".git" / "objects" / "info" / "alternates"
        self.assertFalse(alternates.exists())

    def test_committed_tree_carries_no_forbidden_prefix(self) -> None:
        for path in self._head_paths():
            for forbidden in _FORBIDDEN_PREFIXES:
                self.assertFalse(path.startswith(forbidden))

    def test_committed_tree_carries_no_pre_smoke_excluded_path(self) -> None:
        head = set(self._head_paths())
        self.assertTrue(head.isdisjoint(_PRE_SMOKE_EXCLUDED_PATHS))


if __name__ == "__main__":
    unittest.main()
