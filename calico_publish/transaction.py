"""Atomic positive-tree publication through bounded non-force Git plumbing."""

from __future__ import annotations

import os
import hashlib
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence

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
        "transaction.hash_object_mismatch",
        "transaction.index_mismatch",
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


def _git(
    repo_dir: Path,
    args: Sequence[str],
    *,
    category: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return _run(repo_dir, args, env=env)
    except OSError as exc:
        raise TransactionError(category) from exc


def _safe_staged_paths(staged_files: Sequence[str]) -> tuple[str, ...]:
    if (
        not staged_files
        or not all(isinstance(path, str) for path in staged_files)
        or len(staged_files) != len(set(staged_files))
    ):
        raise TransactionError("transaction.staged_file_missing")
    normalized: list[str] = []
    for raw in staged_files:
        if not isinstance(raw, str) or not raw:
            raise TransactionError("transaction.staged_file_missing")
        path = PurePosixPath(raw)
        if (
            "\\" in raw
            or re.match(r"^[A-Za-z]:", raw)
            or path.is_absolute()
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


def _regular_staged_file(staging: Path, relative_path: str) -> Path:
    candidate = staging.joinpath(*PurePosixPath(relative_path).parts)
    current = staging
    try:
        if current.is_symlink() or not stat.S_ISDIR(os.lstat(current).st_mode):
            raise TransactionError("transaction.staged_file_missing")
        for part in PurePosixPath(relative_path).parts[:-1]:
            current = current / part
            mode = os.lstat(current).st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise TransactionError("transaction.staged_file_missing")
        mode = os.lstat(candidate).st_mode
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        raise TransactionError("transaction.staged_file_missing") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise TransactionError("transaction.staged_file_missing")
    return candidate


def _cache_staged_files(
    staging: Path,
    paths: tuple[str, ...],
    cache_root: Path,
    expected_sha256: Mapping[str, str] | None,
) -> dict[str, str]:
    """Copy caller bytes once and return each cache file's exact Git OID.

    All validation and copying completes before the first object-writing Git
    command. The private cache is then the sole source for every retry, so a
    caller-side mutation cannot change bytes between gate/scan and commit.
    """

    expected = dict(expected_sha256 or {})
    if set(expected) - set(paths):
        raise TransactionError("transaction.staged_file_missing")
    object_ids: dict[str, str] = {}
    for relative_path in paths:
        source = _regular_staged_file(staging, relative_path)
        destination = cache_root.joinpath(*PurePosixPath(relative_path).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as reader, destination.open("xb") as writer:
                while chunk := reader.read(1024 * 1024):
                    writer.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        except OSError as exc:
            raise TransactionError("transaction.staged_file_missing") from exc
        if relative_path == PUBLISHED_MANIFEST_PATH and size == 0:
            raise TransactionError("transaction.staged_file_missing")
        if relative_path in expected and digest.hexdigest() != expected[relative_path]:
            raise TransactionError("transaction.hash_object_mismatch")

        object_digest = hashlib.sha1(f"blob {size}\0".encode("ascii"))
        try:
            with destination.open("rb") as reader:
                while chunk := reader.read(1024 * 1024):
                    object_digest.update(chunk)
        except OSError as exc:
            raise TransactionError("transaction.staged_file_missing") from exc
        object_ids[relative_path] = object_digest.hexdigest()
    return object_ids


def _tip(repo_dir: Path, remote: str, target_ref: str) -> str:
    fetched = _git(repo_dir, ["fetch", remote, target_ref], category="transaction.fetch_failed")
    if fetched.returncode != 0:
        raise TransactionError("transaction.fetch_failed")
    resolved = _git(repo_dir, ["rev-parse", "FETCH_HEAD"], category="transaction.parent_not_found")
    if resolved.returncode != 0 or not resolved.stdout.strip():
        raise TransactionError("transaction.parent_not_found")
    return resolved.stdout.strip()


def _carried_blobs(repo_dir: Path, parent: str) -> dict[str, str]:
    blobs: dict[str, str] = {}
    for path in CARRIED_FORWARD_PATHS:
        result = _git(repo_dir, ["ls-tree", parent, "--", path], category="transaction.parent_not_found")
        parts = result.stdout.strip().split()
        if result.returncode != 0 or len(parts) < 3 or parts[0] != "100644" or parts[1] != "blob":
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
    expected_sha256: Mapping[str, str] | None = None,
    failure_hook: Callable[[str], None] = lambda _stage: None,
) -> TransactionResult:
    """Build and non-force push exactly one positively enumerated tree."""

    repo = Path(repo_dir)
    staging = Path(staging_dir)
    paths = _safe_staged_paths(staged_files)
    if not isinstance(remote, str) or not remote or remote.startswith("-"):
        raise TransactionError("transaction.fetch_failed")
    if target_ref not in {"published-data", "refs/heads/published-data"}:
        raise TransactionError("transaction.parent_not_found")
    push_ref = "refs/heads/published-data"

    with tempfile.TemporaryDirectory(prefix="calico-publish-cache-") as cache_dir:
        cache_root = Path(cache_dir)
        cached_oids = _cache_staged_files(staging, paths, cache_root, expected_sha256)
        for attempt in range(MAX_PUSH_ATTEMPTS):
            parent = _tip(repo, remote, push_ref)
            carried = _carried_blobs(repo, parent)
            environment = os.environ.copy()
            environment["GIT_INDEX_FILE"] = str(cache_root / f"index-{attempt}")
            blobs: dict[str, str] = {}
            for relative_path in paths:
                hashed = _git(
                    repo,
                    ["hash-object", "-w", "--no-filters", "--", str(cache_root / relative_path)],
                    category="transaction.hash_object_failed",
                    env=environment,
                )
                if hashed.returncode != 0 or not hashed.stdout.strip():
                    raise TransactionError("transaction.hash_object_failed")
                blob = hashed.stdout.strip()
                if blob != cached_oids[relative_path]:
                    raise TransactionError("transaction.hash_object_mismatch")
                blobs[relative_path] = blob

            for relative_path in sorted({**blobs, **carried}):
                blob = blobs.get(relative_path, carried.get(relative_path))
                updated = _git(
                    repo,
                    ["update-index", "--add", "--cacheinfo", f"100644,{blob},{relative_path}"],
                    category="transaction.write_tree_failed",
                    env=environment,
                )
                if updated.returncode != 0:
                    raise TransactionError("transaction.write_tree_failed")
                indexed = _git(
                    repo,
                    ["ls-files", "--stage", "--", relative_path],
                    category="transaction.index_mismatch",
                    env=environment,
                )
                fields = indexed.stdout.strip().split()
                if indexed.returncode != 0 or len(fields) < 4 or fields[0] != "100644" or fields[1] != blob:
                    raise TransactionError("transaction.index_mismatch")

            failure_hook("before_write_tree")
            written = _git(repo, ["write-tree"], category="transaction.write_tree_failed", env=environment)
            if written.returncode != 0 or not written.stdout.strip():
                raise TransactionError("transaction.write_tree_failed")
            tree_sha = written.stdout.strip()
            parent_tree = _git(repo, ["rev-parse", f"{parent}^{{tree}}"], category="transaction.parent_not_found")
            if parent_tree.returncode != 0:
                raise TransactionError("transaction.parent_not_found")
            if tree_sha == parent_tree.stdout.strip():
                return TransactionResult(status="no_change", commit_sha=None, tree_sha=tree_sha)

            failure_hook("before_commit_tree")
            committed = _git(
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
                category="transaction.commit_tree_failed",
                env=environment,
            )
            if committed.returncode != 0 or not committed.stdout.strip():
                raise TransactionError("transaction.commit_tree_failed")
            commit_sha = committed.stdout.strip()
            failure_hook("after_commit_tree")
            failure_hook("before_push")
            pushed = _git(repo, ["push", remote, f"{commit_sha}:{push_ref}"], category="transaction.push_failed")
            if pushed.returncode == 0:
                failure_hook("after_push")
                return TransactionResult(status="published", commit_sha=commit_sha, tree_sha=tree_sha)

            try:
                new_tip = _tip(repo, remote, push_ref)
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
