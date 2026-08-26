"""Regression and proof suite for `calico_landing.store` (D-07/D-08/D-09).

Every scenario runs against a real temporary filesystem -- never a mocked
`Path` or patched `os.replace` -- per this plan's explicit real-proof
requirement. The two mandatory two-phase-commit crash boundaries and the
concurrent-writer proof additionally use real Windows subprocesses that
call `os._exit()` mid-commit. This module doubles as its own subprocess
worker (`python -m tests.landing.test_store --worker ...`, see
`_worker_main` and the `__main__` guard at the bottom) so the crash proof
needs no separate non-test file: `python -m unittest` never collects a
`--worker` invocation as a test run. Recovery is observed from genuine
crashed-process filesystem state, not simulated in process. No real
organization identity or excluded value is used -- only reserved synthetic
sentinels, per D-10.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from calico_landing.store import (
    RevisionCommit,
    StoreError,
    StoreLayout,
    commit_revision,
    ensure_store_layout,
    read_promoted_releases,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Reserved synthetic sentinel, never a real registration/FEIN value. Split
#: via runtime concatenation per D-10 (mirrors Phase 2 Plan 01/02's
#: precedent for the same false-positive privacy-scanner class).
_SENTINEL_MARKER = "synthetic-store-sentinel-" + "zzqx-4471"

_DATE_A = "2026-08-05"
_DATE_B = "2026-08-19"

#: Fixed exit code a `--worker` subprocess uses to signal a deliberately
#: injected crash (`os._exit`), distinct from 0 (clean) and Python's own
#: uncaught-exception code 1.
_WORKER_CRASH_EXIT_CODE = 17


def _fingerprint(seed: str) -> str:
    """A deterministic, valid-looking 64-char lowercase hex fingerprint."""

    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _build_staged_dir(layout: StoreLayout, *, content: bytes = b"synthetic-raw-bytes") -> Path:
    staged_dir = Path(tempfile.mkdtemp(dir=str(layout.staging_root)))
    (staged_dir / "raw").mkdir()
    (staged_dir / "canonical").mkdir()
    (staged_dir / "raw" / "sentinel.bin").write_bytes(content)
    return staged_dir


def _worker_main(argv: list[str]) -> int:
    """Subprocess worker entry point (invoked via `--worker`, never
    collected by `unittest discover`). Builds one staged revision directory
    with synthetic content, then calls `commit_revision`, calling
    `os._exit()` at `crash_stage` if given -- a real, unrecoverable process
    kill with no Python-level unwinding -- so the parent test observes
    genuine crashed-process filesystem state. Prints one JSON result line
    to stdout on clean completion; never a path, row, or exception object.
    """

    store_root, as_of_date, fingerprint, crash_stage, metadata_json = argv[:5]
    manifest_metadata = json.loads(metadata_json)

    layout = ensure_store_layout(store_root)
    staged_dir = _build_staged_dir(layout)

    def _hook(stage: str) -> None:
        if stage == crash_stage:
            os._exit(_WORKER_CRASH_EXIT_CODE)

    result = commit_revision(
        store_root=str(layout.store_root),
        staged_revision_dir=staged_dir,
        as_of_date=as_of_date,
        revision_fingerprint=fingerprint,
        manifest_metadata=manifest_metadata,
        failure_hook=_hook,
    )
    print(
        json.dumps(
            {
                "status": result.status,
                "release_revision": result.release_revision,
                "revision_fingerprint": result.revision_fingerprint,
                "recovered": result.recovered,
            }
        )
    )
    return 0


def _worker_args(
    store_root: Path, as_of_date: str, fingerprint: str, crash_stage: str, note: str
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "tests.landing.test_store",
        "--worker",
        str(store_root),
        as_of_date,
        fingerprint,
        crash_stage,
        json.dumps({"note": note}),
    ]


def _run_worker(
    store_root: Path, as_of_date: str, fingerprint: str, crash_stage: str
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        _worker_args(store_root, as_of_date, fingerprint, crash_stage, "synthetic-worker-metadata"),
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )


class StoreLayoutTests(unittest.TestCase):
    def test_ensure_store_layout_creates_fixed_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()

            layout = ensure_store_layout(store_root)

            self.assertTrue((layout.store_root / ".staging").is_dir())
            self.assertTrue((layout.store_root / "attempts").is_dir())
            self.assertTrue((layout.store_root / "releases").is_dir())
            self.assertEqual(layout.staging_root, layout.store_root / ".staging")

    def test_ensure_store_layout_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()

            first = ensure_store_layout(store_root)
            second = ensure_store_layout(store_root)

            self.assertEqual(first.store_root, second.store_root)
            self.assertEqual(first.staging_root, second.staging_root)

    def test_missing_store_root_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist"
            with self.assertRaises(StoreError) as ctx:
                ensure_store_layout(missing)
            self.assertEqual(ctx.exception.category, "store.invalid_store_root")


class CommitRevisionBasicTests(unittest.TestCase):
    def test_first_commit_creates_revision_one_and_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            fingerprint = _fingerprint("release-one")
            staged_dir = _build_staged_dir(layout)

            result = commit_revision(
                layout.store_root, staged_dir, _DATE_A, fingerprint, {"row_counts": [1, 2]}
            )

            self.assertIsInstance(result, RevisionCommit)
            self.assertEqual(result.status, "accepted")
            self.assertEqual(result.release_revision, 1)
            self.assertEqual(result.revision_fingerprint, fingerprint)
            self.assertFalse(result.recovered)
            self.assertFalse(staged_dir.exists(), "staged dir must be moved, not copied")

            promotions = read_promoted_releases(layout.store_root)
            self.assertEqual(set(promotions), {_DATE_A})
            self.assertEqual(promotions[_DATE_A].release_revision, 1)
            self.assertEqual(promotions[_DATE_A].revision_fingerprint, fingerprint)

            revision_dir = layout.store_root / promotions[_DATE_A].revision_dir
            self.assertTrue(revision_dir.is_dir())
            self.assertTrue((revision_dir / "manifest.json").is_file())
            self.assertTrue((revision_dir / "raw" / "sentinel.bin").is_file())

    def test_identical_retry_returns_no_new_release_without_new_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            fingerprint = _fingerprint("release-retry")

            first = commit_revision(
                layout.store_root, _build_staged_dir(layout), _DATE_A, fingerprint, {}
            )
            self.assertEqual(first.status, "accepted")

            pointer_path = layout.store_root / "promoted-releases.json"
            before_bytes = pointer_path.read_bytes()
            releases_before = sorted((layout.store_root / "releases" / _DATE_A).iterdir())

            second = commit_revision(
                layout.store_root, _build_staged_dir(layout), _DATE_A, fingerprint, {}
            )

            self.assertEqual(second.status, "no_new_release")
            self.assertEqual(second.release_revision, 1)
            self.assertFalse(second.recovered)
            self.assertEqual(pointer_path.read_bytes(), before_bytes)
            releases_after = sorted((layout.store_root / "releases" / _DATE_A).iterdir())
            self.assertEqual(releases_before, releases_after)

    def test_same_date_different_fingerprint_creates_revision_two_with_one_date_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            fingerprint_one = _fingerprint("release-rev1")
            fingerprint_two = _fingerprint("release-rev2")

            commit_revision(
                layout.store_root, _build_staged_dir(layout), _DATE_A, fingerprint_one, {}
            )
            second = commit_revision(
                layout.store_root, _build_staged_dir(layout), _DATE_A, fingerprint_two, {}
            )

            self.assertEqual(second.status, "accepted")
            self.assertEqual(second.release_revision, 2)

            promotions = read_promoted_releases(layout.store_root)
            self.assertEqual(set(promotions), {_DATE_A})
            self.assertEqual(promotions[_DATE_A].release_revision, 2)
            self.assertEqual(promotions[_DATE_A].revision_fingerprint, fingerprint_two)

            revision_dirs = sorted((layout.store_root / "releases" / _DATE_A).iterdir())
            self.assertEqual(len(revision_dirs), 2)

    def test_prior_revision_remains_queryable_after_same_date_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            fingerprint_one = _fingerprint("prior-rev1")
            fingerprint_two = _fingerprint("prior-rev2")

            first = commit_revision(
                layout.store_root, _build_staged_dir(layout), _DATE_A, fingerprint_one, {}
            )
            commit_revision(
                layout.store_root, _build_staged_dir(layout), _DATE_A, fingerprint_two, {}
            )

            revision_dirs = sorted((layout.store_root / "releases" / _DATE_A).iterdir())
            rev_one_dir = next(d for d in revision_dirs if d.name.startswith("rev-0001-"))
            self.assertTrue(rev_one_dir.is_dir())
            manifest = json.loads((rev_one_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["revision_fingerprint"], fingerprint_one)
            self.assertEqual(manifest["release_revision"], 1)
            self.assertEqual(first.release_revision, 1)

    def test_different_dates_each_get_their_own_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)

            commit_revision(
                layout.store_root, _build_staged_dir(layout), _DATE_A, _fingerprint("date-a"), {}
            )
            commit_revision(
                layout.store_root, _build_staged_dir(layout), _DATE_B, _fingerprint("date-b"), {}
            )

            promotions = read_promoted_releases(layout.store_root)
            self.assertEqual(set(promotions), {_DATE_A, _DATE_B})
            self.assertEqual(promotions[_DATE_A].release_revision, 1)
            self.assertEqual(promotions[_DATE_B].release_revision, 1)


class ContainmentAndValidationTests(unittest.TestCase):
    def test_rejects_staged_dir_outside_staging_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            outside_dir = Path(tmp) / "outside-staging"
            outside_dir.mkdir()

            with self.assertRaises(StoreError) as ctx:
                commit_revision(
                    layout.store_root, outside_dir, _DATE_A, _fingerprint("outside"), {}
                )
            self.assertEqual(ctx.exception.category, "store.invalid_staging_root")

    def test_rejects_nested_staged_dir_two_levels_below_staging_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            nested = Path(tempfile.mkdtemp(dir=str(layout.staging_root))) / "nested"
            nested.mkdir()

            with self.assertRaises(StoreError) as ctx:
                commit_revision(layout.store_root, nested, _DATE_A, _fingerprint("nested"), {})
            self.assertEqual(ctx.exception.category, "store.invalid_staging_root")

    def test_rejects_invalid_as_of_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            staged_dir = _build_staged_dir(layout)

            with self.assertRaises(StoreError) as ctx:
                commit_revision(
                    layout.store_root, staged_dir, "not-a-date", _fingerprint("bad-date"), {}
                )
            self.assertEqual(ctx.exception.category, "store.invalid_as_of_date")

    def test_rejects_path_traversal_disguised_as_as_of_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            staged_dir = _build_staged_dir(layout)

            with self.assertRaises(StoreError) as ctx:
                commit_revision(
                    layout.store_root,
                    staged_dir,
                    "../../" + _SENTINEL_MARKER,
                    _fingerprint("traversal"),
                    {},
                )
            self.assertEqual(ctx.exception.category, "store.invalid_as_of_date")

    def test_rejects_invalid_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            staged_dir = _build_staged_dir(layout)

            with self.assertRaises(StoreError) as ctx:
                commit_revision(layout.store_root, staged_dir, _DATE_A, "not-hex", {})
            self.assertEqual(ctx.exception.category, "store.invalid_fingerprint")

    def test_rejects_non_dict_manifest_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            staged_dir = _build_staged_dir(layout)

            with self.assertRaises(StoreError) as ctx:
                commit_revision(
                    layout.store_root,
                    staged_dir,
                    _DATE_A,
                    _fingerprint("bad-metadata"),
                    ["not", "a", "dict"],  # type: ignore[arg-type]
                )
            self.assertEqual(ctx.exception.category, "store.invalid_manifest_metadata")

    def test_rejects_symlinked_staged_revision_dir(self) -> None:
        if sys.platform == "win32":
            self.skipTest("creating a directory symlink on Windows needs elevated privileges")
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            real_target = Path(tmp) / "real-target"
            real_target.mkdir()
            link_path = layout.staging_root / "link-child"
            link_path.symlink_to(real_target, target_is_directory=True)

            with self.assertRaises(StoreError) as ctx:
                commit_revision(layout.store_root, link_path, _DATE_A, _fingerprint("link"), {})
            self.assertEqual(ctx.exception.category, "store.link_rejected")

    def test_cross_filesystem_staging_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            staged_dir = _build_staged_dir(layout)

            real_stat = os.stat

            def _fake_stat(path, *args, **kwargs):
                result = real_stat(path, *args, **kwargs)
                if str(path) == str(staged_dir):
                    # os.stat_result is a structseq with no public field-wise
                    # copy constructor; rebuild from the first 10 required
                    # fields (mode/ino/dev/nlink/uid/gid/size/atime/mtime/ctime)
                    # with only st_dev perturbed, so pathlib's own mode-based
                    # checks (is_symlink/is_dir) keep working unmodified.
                    fields = list(tuple(result)[:10])
                    fields[2] = result.st_dev + 1
                    return os.stat_result(fields)
                return result

            with mock.patch("calico_landing.store.os.stat", side_effect=_fake_stat):
                with self.assertRaises(StoreError) as ctx:
                    commit_revision(
                        layout.store_root, staged_dir, _DATE_A, _fingerprint("cross-fs"), {}
                    )
            self.assertEqual(ctx.exception.category, "store.cross_filesystem")


class SchemaTests(unittest.TestCase):
    def test_manifest_json_has_closed_schema_matching_the_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            fingerprint = _fingerprint("manifest-schema")

            commit_revision(
                layout.store_root,
                _build_staged_dir(layout),
                _DATE_A,
                fingerprint,
                {"row_counts": [1, 2, 3]},
            )

            promotions = read_promoted_releases(layout.store_root)
            revision_dir = layout.store_root / promotions[_DATE_A].revision_dir
            manifest = json.loads((revision_dir / "manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(
                set(manifest),
                {
                    "schema_version",
                    "as_of_date",
                    "release_revision",
                    "revision_fingerprint",
                    "fingerprint_algorithm",
                    "metadata",
                },
            )
            self.assertEqual(manifest["as_of_date"], _DATE_A)
            self.assertEqual(manifest["revision_fingerprint"], fingerprint)
            self.assertEqual(manifest["fingerprint_algorithm"], "ordered-source-sha256-json-v1")
            self.assertEqual(manifest["metadata"], {"row_counts": [1, 2, 3]})

    def test_pointer_json_has_closed_schema_exact_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)

            commit_revision(
                layout.store_root, _build_staged_dir(layout), _DATE_A, _fingerprint("pointer"), {}
            )

            pointer_path = layout.store_root / "promoted-releases.json"
            document = json.loads(pointer_path.read_text(encoding="utf-8"))

            self.assertEqual(set(document), {"schema_version", "promotions"})
            self.assertEqual(document["schema_version"], 1)
            self.assertEqual(set(document["promotions"]), {_DATE_A})
            entry = document["promotions"][_DATE_A]
            self.assertEqual(
                set(entry), {"release_revision", "revision_fingerprint", "revision_dir"}
            )

    def test_malformed_pointer_document_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            (layout.store_root / "promoted-releases.json").write_text(
                '{"unexpected_key": true}', encoding="utf-8"
            )

            with self.assertRaises(StoreError) as ctx:
                read_promoted_releases(layout.store_root)
            self.assertEqual(ctx.exception.category, "store.malformed_pointer")


class InProcessFailureInjectionTests(unittest.TestCase):
    def test_failure_before_rename_leaves_no_revision_dir_and_pointer_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)

            # Give the store a real, pre-existing pointer for a different
            # date so "unchanged" is a genuine byte comparison, not an
            # absence check.
            commit_revision(
                layout.store_root, _build_staged_dir(layout), _DATE_B, _fingerprint("baseline"), {}
            )
            pointer_path = layout.store_root / "promoted-releases.json"
            before_bytes = pointer_path.read_bytes()

            def _hook(stage: str) -> None:
                if stage == "before_rename":
                    raise RuntimeError("injected-test-failure")

            with self.assertRaises(RuntimeError):
                commit_revision(
                    layout.store_root,
                    _build_staged_dir(layout),
                    _DATE_A,
                    _fingerprint("never-rendered"),
                    {},
                    failure_hook=_hook,
                )

            # An empty date container directory may exist (it is created
            # before the rename boundary so a destination path is available
            # to rename into), but no revision was ever published into it.
            date_dir = layout.store_root / "releases" / _DATE_A
            revision_dirs = list(date_dir.iterdir()) if date_dir.exists() else []
            self.assertEqual(revision_dirs, [])
            self.assertEqual(pointer_path.read_bytes(), before_bytes)

    def test_failure_after_rename_leaves_directory_but_pointer_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            commit_revision(
                layout.store_root, _build_staged_dir(layout), _DATE_B, _fingerprint("baseline2"), {}
            )
            pointer_path = layout.store_root / "promoted-releases.json"
            before_bytes = pointer_path.read_bytes()

            def _hook(stage: str) -> None:
                if stage == "after_rename":
                    raise RuntimeError("injected-test-failure")

            fingerprint = _fingerprint("after-rename-crash")
            with self.assertRaises(RuntimeError):
                commit_revision(
                    layout.store_root,
                    _build_staged_dir(layout),
                    _DATE_A,
                    fingerprint,
                    {},
                    failure_hook=_hook,
                )

            # The revision directory was renamed into place (complete, on
            # the same filesystem) but the pointer was never touched.
            revision_dirs = list((layout.store_root / "releases" / _DATE_A).iterdir())
            self.assertEqual(len(revision_dirs), 1)
            self.assertEqual(pointer_path.read_bytes(), before_bytes)

            # A clean retry with the same fingerprint recovers, rather than
            # allocating revision 2.
            recovered = commit_revision(
                layout.store_root, _build_staged_dir(layout), _DATE_A, fingerprint, {}
            )
            self.assertEqual(recovered.status, "accepted")
            self.assertTrue(recovered.recovered)
            self.assertEqual(recovered.release_revision, 1)

    def test_hook_stage_sequence_for_fresh_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            seen: list[str] = []

            commit_revision(
                layout.store_root,
                _build_staged_dir(layout),
                _DATE_A,
                _fingerprint("sequence"),
                {},
                failure_hook=seen.append,
            )

            self.assertEqual(seen, ["before_rename", "after_rename", "after_replace"])

    def test_hook_stage_sequence_for_recovery_is_replace_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            fingerprint = _fingerprint("recovery-sequence")

            def _crash_after_rename(stage: str) -> None:
                if stage == "after_rename":
                    raise RuntimeError("injected-test-failure")

            with self.assertRaises(RuntimeError):
                commit_revision(
                    layout.store_root,
                    _build_staged_dir(layout),
                    _DATE_A,
                    fingerprint,
                    {},
                    failure_hook=_crash_after_rename,
                )

            seen: list[str] = []
            commit_revision(
                layout.store_root,
                _build_staged_dir(layout),
                _DATE_A,
                fingerprint,
                {},
                failure_hook=seen.append,
            )
            self.assertEqual(seen, ["after_replace"])


class AttemptRecordTests(unittest.TestCase):
    def test_attempt_record_written_with_closed_safe_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            fingerprint = _fingerprint("attempt-record")

            commit_revision(
                layout.store_root, _build_staged_dir(layout), _DATE_A, fingerprint, {}
            )

            attempt_files = list((layout.store_root / "attempts").glob("*.json"))
            self.assertEqual(len(attempt_files), 1)
            document = json.loads(attempt_files[0].read_text(encoding="utf-8"))
            self.assertEqual(
                set(document),
                {
                    "schema_version",
                    "attempt_id",
                    "as_of_date",
                    "revision_fingerprint",
                    "status",
                    "release_revision",
                    "recovered",
                },
            )
            self.assertEqual(document["status"], "accepted")
            self.assertEqual(document["revision_fingerprint"], fingerprint)


class NonEchoTests(unittest.TestCase):
    def test_store_error_never_echoes_sentinel_path_or_value(self) -> None:
        sentinel_path = Path(tempfile.gettempdir()) / (_SENTINEL_MARKER + "-does-not-exist")
        with self.assertRaises(StoreError) as ctx:
            ensure_store_layout(sentinel_path)

        self.assertEqual(ctx.exception.category, "store.invalid_store_root")
        self.assertNotIn(_SENTINEL_MARKER, str(ctx.exception))
        self.assertNotIn(_SENTINEL_MARKER, repr(ctx.exception))


@unittest.skipUnless(sys.platform == "win32", "mandatory Windows subprocess proof for Phase 2")
class SubprocessCrashRecoveryTests(unittest.TestCase):
    """Real-process crash-recovery proof (D-08/D-09 recovery invariants).

    Each worker subprocess calls `os._exit()` at the named boundary --
    a genuine, unrecoverable process kill -- so recovery is proven against
    real crashed-process filesystem state.
    """

    def test_crash_after_rename_recovers_same_revision_on_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            fingerprint = _fingerprint("subprocess-after-rename")

            crashed = _run_worker(store_root, _DATE_A, fingerprint, "after_rename")
            self.assertEqual(crashed.returncode, 17, msg=crashed.stderr)

            revision_dirs = list((store_root / "releases" / _DATE_A).iterdir())
            self.assertEqual(len(revision_dirs), 1)
            self.assertFalse((store_root / "promoted-releases.json").exists())

            recovered = _run_worker(store_root, _DATE_A, fingerprint, "none")
            self.assertEqual(recovered.returncode, 0, msg=recovered.stderr)
            payload = json.loads(recovered.stdout.decode("utf-8").strip())
            self.assertEqual(payload["status"], "accepted")
            self.assertTrue(payload["recovered"])
            self.assertEqual(payload["release_revision"], 1)

            revision_dirs_after = list((store_root / "releases" / _DATE_A).iterdir())
            self.assertEqual(len(revision_dirs_after), 1, "no revision 3 was allocated")

    def test_crash_after_replace_retry_returns_no_new_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            fingerprint = _fingerprint("subprocess-after-replace")

            crashed = _run_worker(store_root, _DATE_A, fingerprint, "after_replace")
            self.assertEqual(crashed.returncode, 17, msg=crashed.stderr)

            pointer_path = store_root / "promoted-releases.json"
            self.assertTrue(pointer_path.exists())
            before_bytes = pointer_path.read_bytes()

            retried = _run_worker(store_root, _DATE_A, fingerprint, "none")
            self.assertEqual(retried.returncode, 0, msg=retried.stderr)
            payload = json.loads(retried.stdout.decode("utf-8").strip())
            self.assertEqual(payload["status"], "no_new_release")
            self.assertEqual(payload["release_revision"], 1)

            self.assertEqual(pointer_path.read_bytes(), before_bytes)
            revision_dirs = list((store_root / "releases" / _DATE_A).iterdir())
            self.assertEqual(len(revision_dirs), 1)


@unittest.skipUnless(sys.platform == "win32", "mandatory Windows subprocess proof for Phase 2")
class SubprocessConcurrencyTests(unittest.TestCase):
    def test_two_concurrent_writers_get_distinct_revisions_without_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            fingerprint_one = _fingerprint("concurrent-writer-one")
            fingerprint_two = _fingerprint("concurrent-writer-two")

            args_one = _worker_args(store_root, _DATE_A, fingerprint_one, "none", "writer-one")
            args_two = _worker_args(store_root, _DATE_A, fingerprint_two, "none", "writer-two")

            proc_one = subprocess.Popen(
                args_one, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            proc_two = subprocess.Popen(
                args_two, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            out_one, err_one = proc_one.communicate(timeout=30)
            out_two, err_two = proc_two.communicate(timeout=30)

            self.assertEqual(proc_one.returncode, 0, msg=err_one)
            self.assertEqual(proc_two.returncode, 0, msg=err_two)

            payload_one = json.loads(out_one.decode("utf-8").strip())
            payload_two = json.loads(out_two.decode("utf-8").strip())

            self.assertEqual(payload_one["status"], "accepted")
            self.assertEqual(payload_two["status"], "accepted")

            revisions = {payload_one["release_revision"], payload_two["release_revision"]}
            self.assertEqual(revisions, {1, 2}, "concurrent writers must not collide or skip")

            revision_dirs = list((store_root / "releases" / _DATE_A).iterdir())
            self.assertEqual(len(revision_dirs), 2)

            promotions = read_promoted_releases(store_root)
            self.assertEqual(set(promotions), {_DATE_A})
            self.assertEqual(promotions[_DATE_A].release_revision, 2)

            winner_payload = (
                payload_one if payload_one["release_revision"] == 2 else payload_two
            )
            self.assertEqual(
                promotions[_DATE_A].revision_fingerprint, winner_payload["revision_fingerprint"]
            )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        raise SystemExit(_worker_main(sys.argv[2:]))
    unittest.main()
