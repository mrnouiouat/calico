"""Identity-free, in-memory `Archive` test double (06-01-PLAN.md Task 2).

Models the exact-key version-listing, absent/identical/colliding-object,
duplicate-version, and injected list/read/write-failure behavior
`calico_capture.archive` depends on -- entirely in memory, contacting
neither the live source nor any private archive (D-14; COVERAGE.md
"Coverage invariants": "every live adapter call is reachable through a
provider-neutral archive protocol also implemented by the identity-free
fake").

Every failure this fake raises is the same fixed-category `ArchiveError`
production code raises, optionally chained from a caller-supplied "leak"
detail string -- so a test can assert that detail never appears anywhere
in a `CaptureStatus`/exception boundary crossing (D-05/D-09 non-echo
discipline), exactly mirroring how a real provider's raw exception text
must never leak.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from calico_capture.archive import Archive, ArchiveError, ArchiveObjectVersion


@dataclass
class _StoredVersion:
    version_id: str
    data: bytes
    action: str = "upload"


class FakeArchive(Archive):
    """In-memory `Archive` double. Every method is safe to call from
    multiple tests without cross-contamination -- construct one instance
    per test.
    """

    def __init__(self) -> None:
        self._versions: dict[str, list[_StoredVersion]] = {}
        self._next_version_id: int = 1
        self._list_failure_keys: set[str] = set()
        self._write_failure_keys: set[str] = set()
        self._read_failure_keys: set[str] = set()
        self._fail_all_writes: bool = False
        self._fail_all_lists: bool = False
        self._fail_all_reads: bool = False
        self._failure_detail: str | None = None
        self._read_overrides: dict[str, bytes] = {}

    # -- Archive protocol -------------------------------------------------

    def list_versions(self, key: str) -> tuple[ArchiveObjectVersion, ...]:
        if self._fail_all_lists or key in self._list_failure_keys:
            self._raise("archive.list_failed")
        stored = self._versions.get(key, [])
        return tuple(
            ArchiveObjectVersion(
                version_id=version.version_id,
                sha256=hashlib.sha256(version.data).hexdigest(),
                content_length=len(version.data),
                action=version.action,
            )
            for version in stored
        )

    def put_object(self, key: str, data: bytes) -> None:
        if self._fail_all_writes or key in self._write_failure_keys:
            self._raise("archive.write_failed")
        version_id = f"v{self._next_version_id}"
        self._next_version_id += 1
        self._versions.setdefault(key, []).append(
            _StoredVersion(version_id=version_id, data=data)
        )

    def get_object(self, key: str, *, version_id: str | None = None) -> bytes:
        if self._fail_all_reads or key in self._read_failure_keys:
            self._raise("archive.read_failed")
        if key in self._read_overrides:
            # Models a provider returning bytes that disagree with what it
            # just listed/wrote -- a transport-corruption read-back
            # mismatch, regardless of which stored version is requested.
            return self._read_overrides[key]
        stored = self._versions.get(key, [])
        if not stored:
            raise ArchiveError("archive.object_not_found")
        if version_id is None:
            return stored[-1].data
        for version in stored:
            if version.version_id == version_id:
                return version.data
        raise ArchiveError("archive.object_not_found")

    # -- Test-only injection helpers ---------------------------------------

    def all_keys(self) -> tuple[str, ...]:
        """Every key with at least one stored version, sorted -- a
        read-only introspection helper for assertions, never used by
        production code.
        """

        return tuple(sorted(key for key, versions in self._versions.items() if versions))

    def version_count(self, key: str) -> int:
        return len(self._versions.get(key, []))

    def inject_colliding_version(self, key: str, data: bytes, *, action: str = "upload") -> None:
        """Force an additional, byte-different existing version at `key`
        (collision/duplicate-version/hide-marker/unfinished-upload test).
        """

        version_id = f"v{self._next_version_id}"
        self._next_version_id += 1
        self._versions.setdefault(key, []).append(
            _StoredVersion(version_id=version_id, data=data, action=action)
        )

    def fail_list(self, key: str) -> None:
        self._list_failure_keys.add(key)

    def fail_write(self, key: str) -> None:
        self._write_failure_keys.add(key)

    def fail_read(self, key: str) -> None:
        self._read_failure_keys.add(key)

    def clear_write_failure(self, key: str) -> None:
        """Remove a targeted `fail_write` injection -- models an operator
        clearing a transient provider fault before a retried synchronize
        call (recovery/interruption test)."""

        self._write_failure_keys.discard(key)

    def set_read_override(self, key: str, data: bytes) -> None:
        """Force every subsequent `get_object(key, ...)` call to return
        `data` regardless of what was actually written -- models a
        transport read-back mismatch (content or manifest corruption in
        transit)."""

        self._read_overrides[key] = data

    def fail_all_writes(self, detail: str | None = None) -> None:
        """Fail every subsequent `put_object` call -- models exhausted
        bounded transport retries at the provider-adapter layer. `detail`,
        if given, is embedded only in an internally chained exception
        never surfaced by production code; a test asserts it stays absent
        from every observable output.
        """

        self._fail_all_writes = True
        if detail is not None:
            self._failure_detail = detail

    def fail_all_lists(self, detail: str | None = None) -> None:
        self._fail_all_lists = True
        if detail is not None:
            self._failure_detail = detail

    def fail_all_reads(self, detail: str | None = None) -> None:
        self._fail_all_reads = True
        if detail is not None:
            self._failure_detail = detail

    def _raise(self, category: str) -> None:
        if self._failure_detail is not None:
            try:
                raise RuntimeError(self._failure_detail)
            except RuntimeError as exc:
                raise ArchiveError(category) from exc
        raise ArchiveError(category)


__all__ = ["FakeArchive"]
