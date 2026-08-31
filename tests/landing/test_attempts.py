"""Regression suite for `calico_landing.attempts` and its wiring into
`calico_landing.admission.admit()` / `calico_landing.store.commit_revision`
(04-04-PLAN.md D-02/D-12/D-13).

Covers the three closed attempt-document shapes (admission-level v1,
store-level v1, additive v2), the durable v2 writer's atomic-write
discipline, and the end-to-end proof that one `admit()` call writes exactly
one v2 logical attempt with true UTC bounds while `commit_revision`'s own
direct legacy v1 writer stays completely unaffected for a caller that does
not opt out. No real organization identity or excluded value is used --
only invented synthetic values, mirroring every other Phase 2/3/4 suite.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from calico_landing.admission import admit
from calico_landing.attempts import (
    AdmissionV1Attempt,
    AttemptError,
    StoreV1Attempt,
    V2Attempt,
    load_attempt_file,
    parse_attempt_document,
    utc_now_iso,
    write_v2_attempt,
)
from calico_landing.store import commit_revision, ensure_store_layout

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_CANDIDATE_ROOT = REPO_ROOT / "tests" / "fixtures" / "landing" / "valid"

_FINGERPRINT = "a" * 64
_AS_OF_DATE = "2026-08-05"


def _admission_v1_document(**overrides: object) -> dict:
    document = {
        "schema_version": 1,
        "attempt_id": "admission-v1-attempt",
        "status": "rejected",
        "as_of_date": _AS_OF_DATE,
        "reason_count": 2,
    }
    document.update(overrides)
    return document


def _store_v1_document(**overrides: object) -> dict:
    document = {
        "schema_version": 1,
        "attempt_id": "store-v1-attempt",
        "as_of_date": _AS_OF_DATE,
        "revision_fingerprint": _FINGERPRINT,
        "status": "accepted",
        "release_revision": 1,
        "recovered": False,
    }
    document.update(overrides)
    return document


def _v2_document(**overrides: object) -> dict:
    document = {
        "schema_version": 2,
        "attempt_id": "v2-attempt",
        "started_at_utc": "2026-08-05T12:00:00.000Z",
        "ended_at_utc": "2026-08-05T12:00:01.000Z",
        "status": "accepted",
        "as_of_date": _AS_OF_DATE,
        "release_revision": 1,
        "revision_fingerprint": _FINGERPRINT,
        "reason_count": None,
    }
    document.update(overrides)
    return document


class ParseAdmissionV1Tests(unittest.TestCase):
    def test_valid_document_parses(self) -> None:
        parsed = parse_attempt_document(_admission_v1_document())
        self.assertIsInstance(parsed, AdmissionV1Attempt)
        self.assertEqual(parsed.status, "rejected")
        self.assertEqual(parsed.as_of_date, _AS_OF_DATE)
        self.assertEqual(parsed.reason_count, 2)

    def test_null_as_of_date_permitted(self) -> None:
        parsed = parse_attempt_document(_admission_v1_document(as_of_date=None))
        self.assertIsNone(parsed.as_of_date)

    def test_unknown_status_rejected(self) -> None:
        with self.assertRaises(AttemptError) as ctx:
            parse_attempt_document(_admission_v1_document(status="accepted"))
        self.assertEqual(ctx.exception.category, "attempt.invalid_document_schema")


class ParseStoreV1Tests(unittest.TestCase):
    def test_valid_accepted_document_parses(self) -> None:
        parsed = parse_attempt_document(_store_v1_document())
        self.assertIsInstance(parsed, StoreV1Attempt)
        self.assertEqual(parsed.status, "accepted")
        self.assertFalse(parsed.recovered)

    def test_valid_no_new_release_document_parses(self) -> None:
        parsed = parse_attempt_document(_store_v1_document(status="no_new_release"))
        self.assertEqual(parsed.status, "no_new_release")

    def test_recovered_true_parses(self) -> None:
        parsed = parse_attempt_document(_store_v1_document(recovered=True))
        self.assertTrue(parsed.recovered)

    def test_unknown_status_rejected(self) -> None:
        with self.assertRaises(AttemptError) as ctx:
            parse_attempt_document(_store_v1_document(status="rejected"))
        self.assertEqual(ctx.exception.category, "attempt.invalid_document_schema")

    def test_null_as_of_date_rejected(self) -> None:
        # Store-level v1's as_of_date is never null in any real writer call.
        with self.assertRaises(AttemptError):
            parse_attempt_document(_store_v1_document(as_of_date=None))

    def test_malformed_fingerprint_rejected(self) -> None:
        with self.assertRaises(AttemptError):
            parse_attempt_document(_store_v1_document(revision_fingerprint="not-hex"))


class ParseV2Tests(unittest.TestCase):
    def test_valid_accepted_document_parses(self) -> None:
        parsed = parse_attempt_document(_v2_document())
        self.assertIsInstance(parsed, V2Attempt)
        self.assertEqual(parsed.status, "accepted")
        self.assertEqual(parsed.started_at_utc, "2026-08-05T12:00:00.000Z")
        self.assertEqual(parsed.ended_at_utc, "2026-08-05T12:00:01.000Z")

    def test_all_four_closed_statuses_parse(self) -> None:
        for status in ("accepted", "no_new_release", "rejected", "recovered"):
            with self.subTest(status=status):
                parsed = parse_attempt_document(
                    _v2_document(
                        status=status,
                        as_of_date=None if status == "rejected" else _AS_OF_DATE,
                        release_revision=None if status == "rejected" else 1,
                        revision_fingerprint=None if status == "rejected" else _FINGERPRINT,
                        reason_count=3 if status == "rejected" else None,
                    )
                )
                self.assertEqual(parsed.status, status)

    def test_nullable_fields_accept_null(self) -> None:
        parsed = parse_attempt_document(
            _v2_document(
                status="rejected",
                as_of_date=None,
                release_revision=None,
                revision_fingerprint=None,
                reason_count=5,
            )
        )
        self.assertIsNone(parsed.as_of_date)
        self.assertIsNone(parsed.release_revision)
        self.assertIsNone(parsed.revision_fingerprint)
        self.assertEqual(parsed.reason_count, 5)

    def test_unknown_status_rejected(self) -> None:
        with self.assertRaises(AttemptError) as ctx:
            parse_attempt_document(_v2_document(status="unknown"))
        self.assertEqual(ctx.exception.category, "attempt.invalid_document_schema")

    def test_malformed_started_at_utc_missing_z_rejected(self) -> None:
        with self.assertRaises(AttemptError):
            parse_attempt_document(_v2_document(started_at_utc="2026-08-05T12:00:00"))

    def test_malformed_started_at_utc_wrong_shape_rejected(self) -> None:
        with self.assertRaises(AttemptError):
            parse_attempt_document(_v2_document(started_at_utc="not-a-timestamp"))

    def test_fractional_seconds_permitted(self) -> None:
        parsed = parse_attempt_document(
            _v2_document(started_at_utc="2026-08-05T12:00:00.123456Z")
        )
        self.assertEqual(parsed.started_at_utc, "2026-08-05T12:00:00.123456Z")

    def test_whole_second_timestamp_permitted(self) -> None:
        parsed = parse_attempt_document(_v2_document(started_at_utc="2026-08-05T12:00:00Z"))
        self.assertEqual(parsed.started_at_utc, "2026-08-05T12:00:00Z")


class SharedShapeDispatchTests(unittest.TestCase):
    def test_missing_key_rejected(self) -> None:
        document = _v2_document()
        del document["reason_count"]
        with self.assertRaises(AttemptError) as ctx:
            parse_attempt_document(document)
        self.assertEqual(ctx.exception.category, "attempt.invalid_document_schema")

    def test_extra_key_rejected(self) -> None:
        document = _v2_document()
        document["unexpected_field"] = "value"
        with self.assertRaises(AttemptError) as ctx:
            parse_attempt_document(document)
        self.assertEqual(ctx.exception.category, "attempt.invalid_document_schema")

    def test_schema_version_1_with_mixed_keys_rejected(self) -> None:
        # Neither exact v1 key set -- must not silently coerce into either.
        mixed = _admission_v1_document()
        mixed["recovered"] = False
        with self.assertRaises(AttemptError) as ctx:
            parse_attempt_document(mixed)
        self.assertEqual(ctx.exception.category, "attempt.invalid_document_schema")

    def test_unknown_schema_version_rejected(self) -> None:
        with self.assertRaises(AttemptError) as ctx:
            parse_attempt_document(_v2_document(schema_version=3))
        self.assertEqual(ctx.exception.category, "attempt.unsupported_schema_version")

    def test_non_dict_document_rejected(self) -> None:
        with self.assertRaises(AttemptError) as ctx:
            parse_attempt_document(["not", "a", "dict"])
        self.assertEqual(ctx.exception.category, "attempt.invalid_document_schema")

    def test_schema_version_wrong_type_rejected(self) -> None:
        with self.assertRaises(AttemptError):
            parse_attempt_document(_v2_document(schema_version="2"))


class LoadAttemptFileTests(unittest.TestCase):
    def test_round_trips_a_v2_document_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "attempt.json"
            path.write_text(json.dumps(_v2_document()), encoding="utf-8")
            parsed = load_attempt_file(path)
            self.assertIsInstance(parsed, V2Attempt)

    def test_malformed_json_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "attempt.json"
            path.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(AttemptError) as ctx:
                load_attempt_file(path)
            self.assertEqual(ctx.exception.category, "attempt.invalid_document_schema")

    @unittest.skipUnless(sys.platform != "win32", "symlink creation needs elevated privilege on Windows")
    def test_symlink_alias_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real_path = Path(tmp) / "real.json"
            real_path.write_text(json.dumps(_v2_document()), encoding="utf-8")
            link_path = Path(tmp) / "alias.json"
            link_path.symlink_to(real_path)

            with self.assertRaises(AttemptError) as ctx:
                load_attempt_file(link_path)
            self.assertEqual(ctx.exception.category, "attempt.link_rejected")


class WriteV2AttemptTests(unittest.TestCase):
    def test_writes_exact_closed_fields_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp)
            (store_root / "attempts").mkdir()

            write_v2_attempt(
                store_root,
                attempt_id="write-test-attempt",
                started_at_utc="2026-08-05T12:00:00.000Z",
                ended_at_utc="2026-08-05T12:00:01.000Z",
                status="accepted",
                as_of_date=_AS_OF_DATE,
                release_revision=1,
                revision_fingerprint=_FINGERPRINT,
                reason_count=None,
            )

            attempt_files = list((store_root / "attempts").glob("*.json"))
            self.assertEqual(len(attempt_files), 1)
            self.assertEqual(attempt_files[0].name, "write-test-attempt.json")

            document = json.loads(attempt_files[0].read_text(encoding="utf-8"))
            self.assertEqual(
                set(document),
                {
                    "schema_version",
                    "attempt_id",
                    "started_at_utc",
                    "ended_at_utc",
                    "status",
                    "as_of_date",
                    "release_revision",
                    "revision_fingerprint",
                    "reason_count",
                },
            )
            self.assertEqual(document["schema_version"], 2)
            self.assertEqual(document["status"], "accepted")

            # No leftover sibling temp file survives a clean write.
            leftovers = [
                path for path in (store_root / "attempts").iterdir() if path.name.startswith(".attempt.")
            ]
            self.assertEqual(leftovers, [])

    def test_invalid_status_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp)
            (store_root / "attempts").mkdir()
            with self.assertRaises(AttemptError) as ctx:
                write_v2_attempt(
                    store_root,
                    attempt_id="bad-status",
                    started_at_utc="2026-08-05T12:00:00.000Z",
                    ended_at_utc="2026-08-05T12:00:01.000Z",
                    status="not_a_real_status",
                    as_of_date=None,
                    release_revision=None,
                    revision_fingerprint=None,
                    reason_count=None,
                )
            self.assertEqual(ctx.exception.category, "attempt.invalid_status")

    def test_missing_attempts_directory_is_best_effort_and_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp)
            # Deliberately never create `attempts/` -- mirrors the legacy
            # writers' best-effort discipline: a failure here never masks
            # the already-decided admission outcome.
            write_v2_attempt(
                store_root,
                attempt_id="no-directory",
                started_at_utc="2026-08-05T12:00:00.000Z",
                ended_at_utc="2026-08-05T12:00:01.000Z",
                status="accepted",
                as_of_date=_AS_OF_DATE,
                release_revision=1,
                revision_fingerprint=_FINGERPRINT,
                reason_count=None,
            )
            self.assertFalse((store_root / "attempts").exists())


class UtcNowIsoTests(unittest.TestCase):
    def test_matches_the_closed_v2_pattern(self) -> None:
        import re

        timestamp = utc_now_iso()
        self.assertRegex(timestamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z$")

    def test_two_calls_are_non_decreasing(self) -> None:
        first = utc_now_iso()
        second = utc_now_iso()
        self.assertLessEqual(first, second)


class AdmitWritesExactlyOneV2AttemptTests(unittest.TestCase):
    """Proves D-13's central compatibility invariant: every new `admit()`
    call writes exactly one durable attempt, and it is always v2 with true
    UTC bounds -- never the legacy v1 shape, and never two records for one
    logical call.
    """

    def _attempt_documents(self, store_root: Path) -> list[dict]:
        attempts_dir = store_root / "attempts"
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(attempts_dir.glob("*.json"))
        ]

    def test_accepted_admission_writes_one_v2_attempt_with_ordered_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as store_dir:
            store_root = Path(store_dir)
            result = admit(BASELINE_CANDIDATE_ROOT, store_root)
            self.assertEqual(result.status, "accepted")

            documents = self._attempt_documents(store_root)
            self.assertEqual(len(documents), 1)
            document = documents[0]
            self.assertEqual(document["schema_version"], 2)
            self.assertEqual(document["status"], "accepted")
            self.assertEqual(document["release_revision"], result.release_revision)
            self.assertEqual(document["revision_fingerprint"], result.revision_fingerprint)
            self.assertLessEqual(document["started_at_utc"], document["ended_at_utc"])

    def test_no_new_release_admission_writes_one_v2_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as store_dir:
            store_root = Path(store_dir)
            admit(BASELINE_CANDIDATE_ROOT, store_root)
            second = admit(BASELINE_CANDIDATE_ROOT, store_root)
            self.assertEqual(second.status, "no_new_release")

            documents = self._attempt_documents(store_root)
            self.assertEqual(len(documents), 2)
            # Attempt filenames are random UUIDs, not write-ordered, so
            # compare the status multiset rather than a fixed position.
            statuses = sorted(document["status"] for document in documents)
            self.assertEqual(statuses, ["accepted", "no_new_release"])

    def test_rejected_admission_writes_one_v2_attempt_with_null_release_identity(self) -> None:
        with tempfile.TemporaryDirectory() as store_dir:
            store_root = Path(store_dir)
            missing_dir = Path(store_dir).parent / "does-not-exist-candidate"
            result = admit(missing_dir, store_root)
            self.assertEqual(result.status, "rejected")

            documents = self._attempt_documents(store_root)
            self.assertEqual(len(documents), 1)
            document = documents[0]
            self.assertEqual(document["status"], "rejected")
            self.assertIsNone(document["release_revision"])
            self.assertIsNone(document["revision_fingerprint"])
            self.assertGreater(document["reason_count"], 0)

    def test_operational_error_writes_no_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as fake_repo:
            (Path(fake_repo) / ".git").mkdir()
            fake_store = Path(fake_repo) / "store"
            fake_store.mkdir()

            result = admit(BASELINE_CANDIDATE_ROOT, fake_store)
            self.assertEqual(result.status, "operational_error")
            self.assertEqual(list(fake_store.iterdir()), [])


class CommitRevisionDirectWriteAttemptOptOutTests(unittest.TestCase):
    """Proves `commit_revision`'s additive `write_attempt` keyword: the
    default preserves its exact historical store-level v1 write for any
    direct caller, and the explicit opt-out (what `admit()` now passes)
    writes nothing at the store layer at all.
    """

    def test_default_still_writes_the_legacy_store_level_v1_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            staged_dir = Path(tempfile.mkdtemp(dir=str(layout.staging_root)))
            (staged_dir / "raw").mkdir()
            (staged_dir / "canonical").mkdir()
            (staged_dir / "raw" / "sentinel.bin").write_bytes(b"synthetic")

            commit_revision(layout.store_root, staged_dir, _AS_OF_DATE, _FINGERPRINT, {})

            attempt_files = list((layout.store_root / "attempts").glob("*.json"))
            self.assertEqual(len(attempt_files), 1)
            document = json.loads(attempt_files[0].read_text(encoding="utf-8"))
            self.assertEqual(document["schema_version"], 1)
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

    def test_write_attempt_false_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_root = Path(tmp) / "store"
            store_root.mkdir()
            layout = ensure_store_layout(store_root)
            staged_dir = Path(tempfile.mkdtemp(dir=str(layout.staging_root)))
            (staged_dir / "raw").mkdir()
            (staged_dir / "canonical").mkdir()
            (staged_dir / "raw" / "sentinel.bin").write_bytes(b"synthetic")

            commit_revision(
                layout.store_root,
                staged_dir,
                _AS_OF_DATE,
                _FINGERPRINT,
                {},
                write_attempt=False,
            )

            attempt_files = list((layout.store_root / "attempts").glob("*.json"))
            self.assertEqual(attempt_files, [])


if __name__ == "__main__":
    unittest.main()
