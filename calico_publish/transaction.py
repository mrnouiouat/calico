"""Atomic positive-tree publication through bounded non-force Git plumbing."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Sequence

PUBLISHED_EXPORT_DIR = "exports"
PUBLISHED_MANIFEST_PATH = "manifest/published-manifest-v1.json"
CARRIED_FORWARD_PATHS = ("authorization-probe-status.json", "capture-status.json")
MAX_PUSH_ATTEMPTS = 3
TRANSACTION_ERROR_CATEGORIES = frozenset(
    {
        "transaction.fetch_failed",
        "transaction.parent_not_found",
        "transaction.staged_file_missing",
        "transaction.hash_object_failed",
        "transaction.write_tree_failed",
        "transaction.commit_tree_failed",
        "transaction.push_failed",
        "transaction.push_rejected_after_retries",
    }
)


class TransactionError(Exception):
    """A value-free failure in the publication transaction."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class TransactionResult:
    status: str
    commit_sha: str | None
    tree_sha: str


def _run(
    repo_dir: Path,
    args: Sequence[str],
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )


def _safe_staged_paths(staged_files: Sequence[str]) -> tuple[str, ...]:
    if (
        not staged_files
        or not all(isinstance(path, str) for path in staged_files)
        or len(staged_files) != len(set(staged_files))
    ):
        raise TransactionError("transaction.staged_file_missing")
    normalized: list[str] = []
    for raw in staged_files:
        path = PurePosixPath(raw)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != raw
            or (
                raw != PUBLISHED_MANIFEST_PATH
                and re.fullmatch(r"exports/[a-z][a-z0-9_]*\.csv", raw) is None
            )
        ):
            raise TransactionError("transaction.staged_file_missing")
        normalized.append(raw)
    if PUBLISHED_MANIFEST_PATH not in normalized:
        raise TransactionError("transaction.staged_file_missing")
    return tuple(sorted(normalized))


def _tip(repo_dir: Path, remote: str, target_ref: str) -> str:
    fetched = _run(repo_dir, ["fetch", remote, target_ref])
    if fetched.returncode != 0:
        raise TransactionError("transaction.fetch_failed")
    resolved = _run(repo_dir, ["rev-parse", "FETCH_HEAD"])
    if resolved.returncode != 0 or not resolved.stdout.strip():
        raise TransactionError("transaction.parent_not_found")
    return resolved.stdout.strip()


def _carried_blobs(repo_dir: Path, parent: str) -> dict[str, str]:
    blobs: dict[str, str] = {}
    for path in CARRIED_FORWARD_PATHS:
        result = _run(repo_dir, ["ls-tree", parent, "--", path])
        parts = result.stdout.strip().split()
        if result.returncode != 0 or len(parts) < 3 or parts[1] != "blob":
            raise TransactionError("transaction.parent_not_found")
        blobs[path] = parts[2]
    return blobs


def publish_tree(
    *,
    repo_dir: str | Path,
    staging_dir: str | Path,
    staged_files: Sequence[str],
    remote: str,
    target_ref: str,
    commit_subject: str,
    author_name: str,
    author_email: str,
    failure_hook: Callable[[str], None] = lambda _stage: None,
) -> TransactionResult:
    """Build and non-force push exactly one positively enumerated tree."""

    repo = Path(repo_dir)
    staging = Path(staging_dir).resolve()
    paths = _safe_staged_paths(staged_files)
    for relative_path in paths:
        candidate = staging / relative_path
        if not candidate.is_file() or not candidate.resolve().is_relative_to(staging):
            raise TransactionError("transaction.staged_file_missing")

    for attempt in range(MAX_PUSH_ATTEMPTS):
        parent = _tip(repo, remote, target_ref)
        carried = _carried_blobs(repo, parent)
        with tempfile.TemporaryDirectory(prefix="calico-publish-index-") as temp_dir:
            environment = os.environ.copy()
            environment["GIT_INDEX_FILE"] = str(Path(temp_dir) / "index")
            blobs: dict[str, str] = {}
            for relative_path in paths:
                hashed = _run(
                    repo,
                    ["hash-object", "-w", "--", str(staging / relative_path)],
                    env=environment,
                )
                if hashed.returncode != 0 or not hashed.stdout.strip():
                    raise TransactionError("transaction.hash_object_failed")
                blobs[relative_path] = hashed.stdout.strip()

            for relative_path in sorted({**blobs, **carried}):
                blob = blobs.get(relative_path, carried.get(relative_path))
                updated = _run(
                    repo,
                    ["update-index", "--add", "--cacheinfo", f"100644,{blob},{relative_path}"],
                    env=environment,
                )
                if updated.returncode != 0:
                    raise TransactionError("transaction.write_tree_failed")

            failure_hook("before_write_tree")
            written = _run(repo, ["write-tree"], env=environment)
            if written.returncode != 0 or not written.stdout.strip():
                raise TransactionError("transaction.write_tree_failed")
            tree_sha = written.stdout.strip()
            parent_tree = _run(repo, ["rev-parse", f"{parent}^{{tree}}"])
            if parent_tree.returncode != 0:
                raise TransactionError("transaction.parent_not_found")
            if tree_sha == parent_tree.stdout.strip():
                return TransactionResult(status="no_change", commit_sha=None, tree_sha=tree_sha)

            failure_hook("before_commit_tree")
            committed = _run(
                repo,
                [
                    "-c",
                    f"user.name={author_name}",
                    "-c",
                    f"user.email={author_email}",
                    "commit-tree",
                    tree_sha,
                    "-p",
                    parent,
                    "-m",
                    commit_subject,
                ],
                env=environment,
            )
            if committed.returncode != 0 or not committed.stdout.strip():
                raise TransactionError("transaction.commit_tree_failed")
            commit_sha = committed.stdout.strip()
            failure_hook("after_commit_tree")
            failure_hook("before_push")
            pushed = _run(repo, ["push", remote, f"{commit_sha}:{target_ref}"])
            if pushed.returncode == 0:
                failure_hook("after_push")
                return TransactionResult(status="published", commit_sha=commit_sha, tree_sha=tree_sha)

            try:
                new_tip = _tip(repo, remote, target_ref)
            except TransactionError as exc:
                raise TransactionError("transaction.push_failed") from exc
            if new_tip == parent:
                raise TransactionError("transaction.push_failed")
            if attempt == MAX_PUSH_ATTEMPTS - 1:
                raise TransactionError("transaction.push_rejected_after_retries")

    raise TransactionError("transaction.push_rejected_after_retries")


__all__ = [
    "CARRIED_FORWARD_PATHS",
    "MAX_PUSH_ATTEMPTS",
    "PUBLISHED_EXPORT_DIR",
    "PUBLISHED_MANIFEST_PATH",
    "TRANSACTION_ERROR_CATEGORIES",
    "TransactionError",
    "TransactionResult",
    "publish_tree",
]
