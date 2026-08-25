"""Tests for tools.privacy_scan.git_objects.

Every fixture is a disposable temporary Git repository containing only
reserved synthetic content. No fixture is copied from Calico-build's
private source, manifests, evidence, or history.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.privacy_scan.git_objects import (
    GitObjectError,
    ObjectSkip,
    ScannableBlob,
    classify_mode,
    iter_target_entries,
    list_reachable_commits,
    list_tree,
    load_scannable_objects,
)

SYNTHETIC_MARKER = "synthetic-marker-zzqx-9138"


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
    """Creates a disposable, isolated Git repository for one test."""

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

    def hash_object(self, content: bytes) -> str:
        result = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=self.path,
            input=content,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        return result.stdout.decode("ascii").strip()

    def add_cacheinfo(self, mode: str, oid: str, relative_path: str) -> None:
        _run(["update-index", "--add", "--cacheinfo", f"{mode},{oid},{relative_path}"], self.path)


class TestClassifyMode(unittest.TestCase):
    def test_regular_blob_is_scannable(self) -> None:
        self.assertIsNone(classify_mode("100644", "blob"))

    def test_executable_blob_is_scannable(self) -> None:
        self.assertIsNone(classify_mode("100755", "blob"))

    def test_symlink_is_skipped(self) -> None:
        self.assertEqual(classify_mode("120000", "blob"), "symlink")

    def test_gitlink_mode_is_submodule(self) -> None:
        self.assertEqual(classify_mode("160000", "commit"), "submodule")

    def test_commit_type_is_submodule_regardless_of_mode(self) -> None:
        self.assertEqual(classify_mode("100644", "commit"), "submodule")

    def test_unsupported_mode_is_skipped(self) -> None:
        self.assertEqual(classify_mode("100664", "blob"), "unsupported_mode")


class TestListTree(unittest.TestCase):
    def test_lists_regular_files(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("a.txt", b"alpha\n")
            repo.write_file("dir/b.txt", b"beta\n")
            repo.add("a.txt")
            repo.add("dir/b.txt")
            commit = repo.commit("add files")

            entries = list_tree(commit, repo.path)

        paths = sorted(entry.path for entry in entries)
        self.assertEqual(paths, ["a.txt", "dir/b.txt"])
        for entry in entries:
            self.assertEqual(entry.obj_type, "blob")
            self.assertEqual(entry.mode, "100644")

    def test_paths_with_spaces_and_non_ascii_survive_enumeration(self) -> None:
        with TempGitRepo() as repo:
            tricky_path = "dir with space/café.txt"
            repo.write_file(tricky_path, b"content\n")
            repo.add(tricky_path)
            commit = repo.commit("add tricky path")

            entries = list_tree(commit, repo.path)

        paths = [entry.path for entry in entries]
        self.assertIn(tricky_path, paths)

    def test_invalid_treeish_fails_closed(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("a.txt", b"alpha\n")
            repo.add("a.txt")
            repo.commit("add file")

            with self.assertRaises(GitObjectError) as ctx:
                list_tree("not-a-real-treeish-" + SYNTHETIC_MARKER, repo.path)
            self.assertEqual(ctx.exception.category, "invalid_treeish")
            self.assertNotIn(SYNTHETIC_MARKER, str(ctx.exception))

    def test_symlink_entry_reports_symlink_mode(self) -> None:
        with TempGitRepo() as repo:
            target_oid = repo.hash_object(b"a.txt")
            repo.add_cacheinfo("120000", target_oid, "link-to-a")
            commit = repo.commit("add symlink entry")

            entries = list_tree(commit, repo.path)

        link_entries = [entry for entry in entries if entry.path == "link-to-a"]
        self.assertEqual(len(link_entries), 1)
        self.assertEqual(link_entries[0].mode, "120000")
        self.assertEqual(classify_mode(link_entries[0].mode, link_entries[0].obj_type), "symlink")

    def test_gitlink_entry_reports_submodule(self) -> None:
        with TempGitRepo() as repo:
            fake_commit_sha = "1234567890abcdef1234567890abcdef12345678"
            repo.add_cacheinfo("160000", fake_commit_sha, "vendored-submodule")
            commit = repo.commit("add gitlink entry")

            entries = list_tree(commit, repo.path)

        link_entries = [entry for entry in entries if entry.path == "vendored-submodule"]
        self.assertEqual(len(link_entries), 1)
        self.assertEqual(classify_mode(link_entries[0].mode, link_entries[0].obj_type), "submodule")


class TestHistoryDeduplication(unittest.TestCase):
    def test_repeated_path_oid_pairs_across_commits_scanned_once(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("stable.txt", b"unchanged\n")
            repo.add("stable.txt")
            repo.commit("first commit")

            repo.write_file("stable.txt", b"unchanged\n")  # identical content -> same blob OID
            repo.write_file("changed.txt", b"version-1\n")
            repo.add("stable.txt")
            repo.add("changed.txt")
            repo.commit("second commit")

            repo.write_file("changed.txt", b"version-2\n")
            repo.add("changed.txt")
            repo.commit("third commit")

            entries = iter_target_entries(treeish=None, history_all=True, repo_dir=repo.path)

        keys = [(entry.path, entry.oid) for entry in entries]
        self.assertEqual(len(keys), len(set(keys)), "duplicate (path, oid) pairs were not deduplicated")

        stable_matches = [key for key in keys if key[0] == "stable.txt"]
        self.assertEqual(len(stable_matches), 1)

        changed_matches = [key for key in keys if key[0] == "changed.txt"]
        self.assertEqual(len(changed_matches), 2)

    def test_list_reachable_commits_enumerates_all_commits(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("a.txt", b"1\n")
            repo.add("a.txt")
            c1 = repo.commit("c1")
            repo.write_file("a.txt", b"2\n")
            repo.add("a.txt")
            c2 = repo.commit("c2")

            commits = list_reachable_commits(repo.path)

        self.assertEqual(set(commits), {c1, c2})


class TestLoadScannableObjects(unittest.TestCase):
    def test_regular_text_blob_is_scannable(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("readme.txt", b"hello world\n")
            repo.add("readme.txt")
            commit = repo.commit("add readme")
            entries = list_tree(commit, repo.path)

            results = load_scannable_objects(entries, repo.path, max_blob_bytes=1024)

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertIsInstance(result, ScannableBlob)
        self.assertEqual(result.path, "readme.txt")
        self.assertEqual(result.text, "hello world\n")

    def test_oversize_blob_is_skipped(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("big.txt", b"x" * 5000)
            repo.add("big.txt")
            commit = repo.commit("add big file")
            entries = list_tree(commit, repo.path)

            results = load_scannable_objects(entries, repo.path, max_blob_bytes=100)

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertIsInstance(result, ObjectSkip)
        self.assertEqual(result.category, "oversize_blob")

    def test_binary_content_is_skipped(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("binary.dat", b"\x00\x01\x02\x03binary")
            repo.add("binary.dat")
            commit = repo.commit("add binary file")
            entries = list_tree(commit, repo.path)

            results = load_scannable_objects(entries, repo.path, max_blob_bytes=1024)

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertIsInstance(result, ObjectSkip)
        self.assertEqual(result.category, "binary_content")

    def test_invalid_utf8_is_skipped(self) -> None:
        with TempGitRepo() as repo:
            repo.write_file("bad-encoding.txt", b"\xff\xfe not valid utf-8")
            repo.add("bad-encoding.txt")
            commit = repo.commit("add invalid utf-8 file")
            entries = list_tree(commit, repo.path)

            results = load_scannable_objects(entries, repo.path, max_blob_bytes=1024)

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertIsInstance(result, ObjectSkip)
        self.assertEqual(result.category, "invalid_utf8")

    def test_lfs_pointer_is_skipped(self) -> None:
        with TempGitRepo() as repo:
            pointer_body = (
                b"version https://git-lfs.github.com/spec/v1\n"
                b"oid sha256:" + b"0" * 64 + b"\n"
                b"size 12345\n"
            )
            repo.write_file("large-asset.bin", pointer_body)
            repo.add("large-asset.bin")
            commit = repo.commit("add lfs pointer")
            entries = list_tree(commit, repo.path)

            results = load_scannable_objects(entries, repo.path, max_blob_bytes=1024)

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertIsInstance(result, ObjectSkip)
        self.assertEqual(result.category, "lfs_pointer")

    def test_symlink_and_submodule_are_skipped_without_reading_content(self) -> None:
        with TempGitRepo() as repo:
            target_oid = repo.hash_object(b"a.txt")
            repo.add_cacheinfo("120000", target_oid, "link-to-a")
            fake_commit_sha = "1234567890abcdef1234567890abcdef12345678"
            repo.add_cacheinfo("160000", fake_commit_sha, "vendored-submodule")
            commit = repo.commit("add symlink and gitlink")
            entries = list_tree(commit, repo.path)

            results = load_scannable_objects(entries, repo.path, max_blob_bytes=1024)

        categories = {(item.path, item.category) for item in results if isinstance(item, ObjectSkip)}
        self.assertIn(("link-to-a", "symlink"), categories)
        self.assertIn(("vendored-submodule", "submodule"), categories)

    def test_errors_never_reflect_input(self) -> None:
        with TempGitRepo() as repo:
            content = ("secret value: " + SYNTHETIC_MARKER + "\n").encode("utf-8")
            repo.write_file("bad-encoding.txt", b"\xff\xfe " + content)
            repo.add("bad-encoding.txt")
            commit = repo.commit("add file")
            entries = list_tree(commit, repo.path)

            results = load_scannable_objects(entries, repo.path, max_blob_bytes=1024)

        result = results[0]
        self.assertIsInstance(result, ObjectSkip)
        self.assertNotIn(SYNTHETIC_MARKER, result.category)
        self.assertNotIn(SYNTHETIC_MARKER, repr(result))


if __name__ == "__main__":
    unittest.main()
