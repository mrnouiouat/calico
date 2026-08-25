"""Git object database access for the publishable-tree privacy scanner.

Reads the candidate tree and reachable target history through Git plumbing
only -- never by parsing `.git/objects` directly (D-08). All subprocess
invocations use argument arrays with `shell=False`; no treeish, path, or OID
is ever interpolated into a shell command (D-08, T-03).

Every failure crosses this module's boundary as a fixed safe `category`
string via `GitObjectError`. No stdout, stderr, exception `repr`, matched
value, or blob content is ever included (D-10, T-01).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

#: Tree entry modes this scanner treats as ordinary scannable blobs.
_SUPPORTED_BLOB_MODES = frozenset({"100644", "100755"})

#: Git's symlink tree-entry mode.
_SYMLINK_MODE = "120000"

#: Git's gitlink (submodule) tree-entry mode.
_GITLINK_MODE = "160000"

#: Git LFS pointer files begin with this exact line.
_LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec"


class GitObjectError(Exception):
    """Raised when Git object access fails or returns unscannable data.

    Carries only a fixed safe `category`; never stdout/stderr/exception repr.
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    obj_type: str
    oid: str
    path: str


@dataclass(frozen=True)
class ScannableBlob:
    path: str
    oid: str
    text: str


@dataclass(frozen=True)
class ObjectSkip:
    path: str
    oid: str
    category: str


def classify_mode(mode: str, obj_type: str) -> str | None:
    """Return a fixed safe skip category for a tree entry, or None if scannable."""

    if mode == _SYMLINK_MODE:
        return "symlink"
    if mode == _GITLINK_MODE or obj_type == "commit":
        return "submodule"
    if mode not in _SUPPORTED_BLOB_MODES or obj_type != "blob":
        return "unsupported_mode"
    return None


def run_git(args: list[str], repo_dir: str | Path) -> bytes:
    """Invoke Git with an argument array (`shell=False`) and return stdout bytes.

    Raises `GitObjectError("git_error")` on any nonzero exit or invocation
    failure; stderr is never surfaced to the caller.
    """

    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        raise GitObjectError("git_error") from exc
    return completed.stdout


def _parse_ls_tree(raw: bytes) -> list[TreeEntry]:
    entries: list[TreeEntry] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        meta, _, path_bytes = record.partition(b"\t")
        try:
            path = path_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GitObjectError("invalid_path_encoding") from exc
        try:
            meta_text = meta.decode("ascii")
            mode, obj_type, oid = meta_text.split(" ")
        except (UnicodeDecodeError, ValueError) as exc:
            raise GitObjectError("invalid_tree_entry") from exc
        entries.append(TreeEntry(mode=mode, obj_type=obj_type, oid=oid, path=path))
    return entries


def list_tree(treeish: str, repo_dir: str | Path) -> list[TreeEntry]:
    """List all entries reachable from `treeish`, recursively, NUL-delimited.

    Raises `GitObjectError("invalid_treeish")` if the treeish cannot be
    resolved or listed.
    """

    try:
        raw = run_git(["ls-tree", "-rz", "--full-tree", treeish], repo_dir)
    except GitObjectError as exc:
        if exc.category == "git_error":
            raise GitObjectError("invalid_treeish") from exc
        raise
    return _parse_ls_tree(raw)


def list_reachable_commits(repo_dir: str | Path) -> list[str]:
    """Return every commit reachable from any ref (`git rev-list --all`)."""

    raw = run_git(["rev-list", "--all"], repo_dir)
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise GitObjectError("git_error") from exc
    return [line for line in text.splitlines() if line]


def iter_target_entries(
    *,
    treeish: str | None,
    history_all: bool,
    repo_dir: str | Path,
) -> list[TreeEntry]:
    """Enumerate unique (path, blob OID) tree entries for the candidate tree
    and/or all reachable target history, deduplicated once globally.
    """

    seen: set[tuple[str, str]] = set()
    result: list[TreeEntry] = []

    def _add_all(entries: list[TreeEntry]) -> None:
        for entry in entries:
            key = (entry.path, entry.oid)
            if key in seen:
                continue
            seen.add(key)
            result.append(entry)

    if treeish is not None:
        _add_all(list_tree(treeish, repo_dir))

    if history_all:
        for commit in list_reachable_commits(repo_dir):
            _add_all(list_tree(commit, repo_dir))

    return result


class _BatchBlobReader:
    """Wraps a single long-lived `git cat-file --batch` process."""

    def __init__(self, repo_dir: str | Path) -> None:
        self._repo_dir = repo_dir
        self._proc: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> "_BatchBlobReader":
        try:
            self._proc = subprocess.Popen(
                ["git", "-C", str(self._repo_dir), "cat-file", "--batch"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        except OSError as exc:
            raise GitObjectError("git_error") from exc
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if self._proc is not None:
            try:
                if self._proc.stdin is not None:
                    self._proc.stdin.close()
            except OSError:
                pass
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=10)
            finally:
                for stream in (self._proc.stdout, self._proc.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass
                self._proc = None
        return False

    def read(self, oid: str, max_blob_bytes: int) -> tuple[int, bytes, bool]:
        """Read one object's size and up-to-`max_blob_bytes+1` content bytes.

        Returns (size, content_prefix, oversize). Content beyond the cap is
        drained from the stream (to preserve batch protocol framing) but
        never retained, bounding memory use regardless of object size.
        """

        assert self._proc is not None
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None

        try:
            self._proc.stdin.write(oid.encode("ascii") + b"\n")
            self._proc.stdin.flush()
            header = self._proc.stdout.readline()
            if not header:
                raise GitObjectError("git_error")
            header_text = header.decode("ascii").strip()
            if header_text.endswith("missing"):
                raise GitObjectError("git_error")
            parts = header_text.split(" ")
            if len(parts) != 3:
                raise GitObjectError("git_error")
            _resolved_oid, _obj_type, size_text = parts
            size = int(size_text)

            cap = max(max_blob_bytes, 0)
            read_len = min(size, cap + 1)
            content = self._proc.stdout.read(read_len)
            if content is None or len(content) != read_len:
                raise GitObjectError("git_error")

            remaining = size - len(content)
            while remaining > 0:
                chunk = self._proc.stdout.read(min(remaining, 65536))
                if not chunk:
                    raise GitObjectError("git_error")
                remaining -= len(chunk)

            trailer = self._proc.stdout.read(1)
            if trailer != b"\n":
                raise GitObjectError("git_error")

            return size, content, size > cap
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise GitObjectError("git_error") from exc


def load_scannable_objects(
    entries: list[TreeEntry],
    repo_dir: str | Path,
    max_blob_bytes: int,
) -> list[ScannableBlob | ObjectSkip]:
    """Classify and read each tree entry into a ScannableBlob or ObjectSkip.

    Symlinks, submodules, and unsupported modes are skipped without any
    content read. Oversize blobs are skipped with content read bounded to
    `max_blob_bytes + 1` bytes regardless of true object size (T-04). LFS
    pointers, binary content (any NUL byte), and invalid UTF-8 are skipped
    with a fixed safe category.
    """

    results: list[ScannableBlob | ObjectSkip] = []
    with _BatchBlobReader(repo_dir) as reader:
        for entry in entries:
            skip_category = classify_mode(entry.mode, entry.obj_type)
            if skip_category is not None:
                results.append(ObjectSkip(path=entry.path, oid=entry.oid, category=skip_category))
                continue

            size, content, oversize = reader.read(entry.oid, max_blob_bytes)
            if oversize:
                results.append(ObjectSkip(path=entry.path, oid=entry.oid, category="oversize_blob"))
                continue
            if content.startswith(_LFS_POINTER_PREFIX):
                results.append(ObjectSkip(path=entry.path, oid=entry.oid, category="lfs_pointer"))
                continue
            if b"\x00" in content:
                results.append(ObjectSkip(path=entry.path, oid=entry.oid, category="binary_content"))
                continue
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                results.append(ObjectSkip(path=entry.path, oid=entry.oid, category="invalid_utf8"))
                continue

            results.append(ScannableBlob(path=entry.path, oid=entry.oid, text=text))

    return results
