"""Integration tests for the positive-tree publication transaction."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from calico_publish import transaction
from calico_publish.transaction import TransactionError, publish_tree


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, shell=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return result.stdout.strip()


def _email(user: str) -> str:
    return user + "@" + "example.invalid"


class PublicationFixture:
    def __init__(self, root: Path) -> None:
        self.remote = root / "remote.git"
        self.repo = root / "repo"
        self.staging = root / "staging"
        self.remote.mkdir()
        self.repo.mkdir()
        self.staging.mkdir()
        _git(self.remote, "init", "--bare")
        _git(self.repo, "init", "--initial-branch=main")
        _git(self.repo, "config", "user.name", "Synthetic Publisher")
        _git(self.repo, "config", "user.email", _email("synthetic"))
        _git(self.repo, "remote", "add", "origin", str(self.remote))
        for path, value in {
            "authorization-probe-status.json": "probe\n",
            "capture-status.json": "capture\n",
            "legacy.txt": "must disappear\n",
        }.items():
            (self.repo / path).write_text(value, encoding="utf-8")
        _git(self.repo, "add", "authorization-probe-status.json", "capture-status.json", "legacy.txt")
        _git(self.repo, "commit", "-m", "seed")
        _git(self.repo, "push", "origin", "HEAD:refs/heads/published-data")
        self.write_staging("first\n")

    def write_staging(self, value: str) -> None:
        export = self.staging / "exports" / "public.csv"
        manifest = self.staging / "manifest" / "published-manifest-v1.json"
        export.parent.mkdir(parents=True, exist_ok=True)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        export.write_text(value, encoding="utf-8")
        manifest.write_text("{}\n", encoding="utf-8")

    @property
    def paths(self) -> tuple[str, ...]:
        return ("exports/public.csv", "manifest/published-manifest-v1.json")

    def publish(self, **kwargs):
        return publish_tree(
            repo_dir=self.repo, staging_dir=self.staging, staged_files=self.paths,
            remote="origin", target_ref="published-data", commit_subject="Synthetic publication",
            author_name="Synthetic Publisher", author_email=_email("synthetic"), **kwargs,
        )


class TestPublishTree(unittest.TestCase):
    def test_positive_tree_replaces_legacy_and_carries_status_blobs_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PublicationFixture(Path(directory))
            before = {
                path: _git(fixture.repo, "rev-parse", f"refs/remotes/origin/published-data:{path}")
                for path in transaction.CARRIED_FORWARD_PATHS
            }
            result = fixture.publish()
            _git(fixture.repo, "fetch", "origin", "published-data")
            paths = _git(fixture.repo, "ls-tree", "-r", "--name-only", "FETCH_HEAD").splitlines()
            after = {path: _git(fixture.repo, "rev-parse", f"FETCH_HEAD:{path}") for path in before}
        self.assertEqual(result.status, "published")
        self.assertEqual(paths, [*transaction.CARRIED_FORWARD_PATHS, *fixture.paths])
        self.assertEqual(before, after)

    def test_identical_tree_is_no_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PublicationFixture(Path(directory))
            fixture.publish()
            before = _git(fixture.repo, "ls-remote", "origin", "refs/heads/published-data").split()[0]
            result = fixture.publish()
            after = _git(fixture.repo, "ls-remote", "origin", "refs/heads/published-data").split()[0]
        self.assertEqual(result.status, "no_change")
        self.assertIsNone(result.commit_sha)
        self.assertEqual(before, after)

    def test_moved_tip_is_retried_and_new_commit_uses_moved_tip_as_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = PublicationFixture(root)
            mover = root / "mover"
            _git(root, "clone", "--branch", "published-data", str(fixture.remote), str(mover))
            _git(mover, "config", "user.name", "Synthetic Mover")
            _git(mover, "config", "user.email", _email("mover"))
            moved: list[str] = []

            def move_once(stage: str) -> None:
                if stage == "before_push" and not moved:
                    (mover / "race.txt").write_text("race\n", encoding="utf-8")
                    _git(mover, "add", "race.txt")
                    _git(mover, "commit", "-m", "move tip")
                    _git(mover, "push", "origin", "HEAD:published-data")
                    moved.append(_git(mover, "rev-parse", "HEAD"))

            result = fixture.publish(failure_hook=move_once)
            assert result.commit_sha is not None
            parent = _git(fixture.repo, "rev-parse", f"{result.commit_sha}^")
        self.assertEqual(parent, moved[0])

    def test_moved_tip_beyond_bound_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = PublicationFixture(root)
            mover = root / "mover"
            _git(root, "clone", "--branch", "published-data", str(fixture.remote), str(mover))
            _git(mover, "config", "user.name", "Synthetic Mover")
            _git(mover, "config", "user.email", _email("mover"))
            move_count = 0

            def move_always(stage: str) -> None:
                nonlocal move_count
                if stage == "before_push":
                    move_count += 1
                    (mover / f"race-{move_count}.txt").write_text("race\n", encoding="utf-8")
                    _git(mover, "add", f"race-{move_count}.txt")
                    _git(mover, "commit", "-m", "move tip")
                    _git(mover, "push", "origin", "HEAD:published-data")

            with self.assertRaises(TransactionError) as raised:
                fixture.publish(failure_hook=move_always)
        self.assertEqual(raised.exception.category, "transaction.push_rejected_after_retries")
        self.assertEqual(move_count, transaction.MAX_PUSH_ATTEMPTS)

    def test_interruption_points_leave_remote_unchanged(self) -> None:
        for stage in ("before_write_tree", "before_commit_tree", "after_commit_tree", "before_push"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                fixture = PublicationFixture(Path(directory))
                before = _git(fixture.repo, "ls-remote", "origin", "refs/heads/published-data").split()[0]

                def stop(name: str) -> None:
                    if name == stage:
                        raise RuntimeError("synthetic interruption")

                with self.assertRaises(RuntimeError):
                    fixture.publish(failure_hook=stop)
                after = _git(fixture.repo, "ls-remote", "origin", "refs/heads/published-data").split()[0]
            self.assertEqual(before, after)

    def test_missing_or_empty_manifest_fails_before_object_write(self) -> None:
        for missing in (True, False):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                fixture = PublicationFixture(Path(directory))
                manifest = fixture.staging / transaction.PUBLISHED_MANIFEST_PATH
                manifest.unlink() if missing else manifest.write_bytes(b"")
                with mock.patch.object(transaction, "_run", wraps=transaction._run) as run_spy:
                    with self.assertRaises(TransactionError) as raised:
                        fixture.publish()
                self.assertEqual(raised.exception.category, "transaction.staged_file_missing")
                self.assertFalse(any("hash-object" in call.args[1] for call in run_spy.call_args_list))

    def test_cached_bytes_survive_source_mutation_and_filters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PublicationFixture(Path(directory))
            original = (fixture.staging / "exports/public.csv").read_bytes()
            (fixture.repo / ".gitattributes").write_text("*.csv filter=synthetic\n", encoding="utf-8")
            _git(fixture.repo, "config", "filter.synthetic.clean", "false")

            def mutate(stage: str) -> None:
                if stage == "before_write_tree":
                    (fixture.staging / "exports/public.csv").write_text("changed\n", encoding="utf-8")

            fixture.publish(failure_hook=mutate)
            committed = subprocess.run(
                ["git", "show", "refs/remotes/origin/published-data:exports/public.csv"],
                cwd=fixture.repo, check=True, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ).stdout
        self.assertEqual(committed, original)

    def test_push_never_uses_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = PublicationFixture(Path(directory))
            with mock.patch.object(transaction, "_run", wraps=transaction._run) as run_spy:
                fixture.publish()
        push_commands = [call.args[1] for call in run_spy.call_args_list if "push" in call.args[1]]
        self.assertTrue(push_commands)
        self.assertFalse(any(any(arg.startswith("--force") or arg == "-f" for arg in command) for command in push_commands))

    def test_rejects_unsafe_paths_and_nonregular_files(self) -> None:
        unsafe = ("../escape", "/" + "absolute", "C:" + "/drive", "exports\\wrong.csv")
        for path in unsafe:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as directory:
                fixture = PublicationFixture(Path(directory))
                with self.assertRaises(TransactionError) as raised:
                    publish_tree(
                        repo_dir=fixture.repo, staging_dir=fixture.staging,
                        staged_files=(path, transaction.PUBLISHED_MANIFEST_PATH), remote="origin",
                        target_ref="published-data", commit_subject="Synthetic publication",
                        author_name="Synthetic", author_email=_email("synthetic"),
                    )
                self.assertEqual(raised.exception.category, "transaction.staged_file_missing")

        with tempfile.TemporaryDirectory() as directory:
            fixture = PublicationFixture(Path(directory))
            (fixture.staging / "exports/public.csv").unlink()
            (fixture.staging / "exports/public.csv").mkdir()
            with self.assertRaises(TransactionError) as raised:
                fixture.publish()
            self.assertEqual(raised.exception.category, "transaction.staged_file_missing")


if __name__ == "__main__":
    unittest.main()
