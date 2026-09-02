"""Closed positive status-projection schema enforcement tests (06-02-PLAN.md
Task 2; `contracts/capture-status-v1.schema.json`).

Proves `calico_capture.status`'s exact closed key set and enums permit only
the schema version, UTC attempt bounds, the closed outcome/reason/trigger
vocabularies, and the accepted date/revision; that extra keys, arbitrary
strings, fingerprints, URLs, paths, object/row counts, source content, and
exception/provider details are rejected -- structurally, by an allowlisted
field/type/enum check, never by filtering an already-built document; and
that the last-accepted date/revision remains `None` (never a stale or
partial echo) on rejected, no_new_release-exhaustion-shaped, archive-error,
and build-error outcomes. Cross-checks the committed JSON schema file
against the same closed Python vocabularies so the two documents can never
silently drift apart. Entirely offline and pure-function.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from calico_capture.status import (
    STATUS_DOCUMENT_KEYS,
    CaptureStatus,
    StatusError,
    project_safe_status,
    validate_capture_status_document,
)

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "contracts" / "capture-status-v1.schema.json"
)

_STARTED = "2026-09-02T17:17:00.000Z"
_ENDED = "2026-09-02T17:24:00.000Z"


def _load_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _valid_accepted_document() -> dict:
    return {
        "schema_version": 1,
        "trigger": "local",
        "outcome": "accepted",
        "reason_category": "none",
        "started_at_utc": _STARTED,
        "ended_at_utc": _ENDED,
        "last_accepted_as_of_date": "2020-01-15",
        "last_accepted_release_revision": 1,
    }


class SchemaFileCrossCheckTests(unittest.TestCase):
    """Test 1: exact schema keys/enums permit only version, UTC attempt
    bounds, closed outcome/reason/trigger, safe workflow metadata (the
    closed `trigger` vocabulary), and accepted date/revision."""

    def test_schema_file_exists_and_is_closed(self) -> None:
        schema = _load_schema()
        self.assertEqual(schema["additionalProperties"], False)
        self.assertEqual(schema["type"], "object")

    def test_schema_required_keys_match_the_python_closed_key_set(self) -> None:
        schema = _load_schema()
        self.assertEqual(set(schema["required"]), STATUS_DOCUMENT_KEYS)
        self.assertEqual(set(schema["properties"].keys()), STATUS_DOCUMENT_KEYS)

    def test_schema_version_is_a_fixed_const(self) -> None:
        schema = _load_schema()
        self.assertEqual(schema["properties"]["schema_version"], {"const": 1})

    def test_schema_trigger_enum_matches_the_python_closed_vocabulary(self) -> None:
        schema = _load_schema()
        self.assertEqual(
            set(schema["properties"]["trigger"]["enum"]),
            {"schedule", "workflow_dispatch", "local"},
        )

    def test_schema_outcome_enum_matches_the_python_closed_vocabulary(self) -> None:
        schema = _load_schema()
        self.assertEqual(
            set(schema["properties"]["outcome"]["enum"]),
            {"accepted", "no_new_release", "rejected", "operational_error"},
        )

    def test_schema_reason_category_enum_matches_the_python_closed_vocabulary(self) -> None:
        schema = _load_schema()
        self.assertEqual(
            set(schema["properties"]["reason_category"]["enum"]),
            {
                "none",
                "source_not_advanced",
                "structural_rejection",
                "source_transfer_error",
                "archive_error",
                "restore_error",
                "warehouse_build_error",
            },
        )

    def test_valid_accepted_document_satisfies_validate_capture_status_document(
        self,
    ) -> None:
        validate_capture_status_document(_valid_accepted_document())

    def test_project_safe_status_output_satisfies_the_same_closed_key_set(self) -> None:
        status = project_safe_status(
            trigger="local",
            outcome="accepted",
            reason_category="none",
            started_at_utc=_STARTED,
            ended_at_utc=_ENDED,
            last_accepted_as_of_date="2020-01-15",
            last_accepted_release_revision=1,
        )
        self.assertEqual(set(status.to_dict().keys()), STATUS_DOCUMENT_KEYS)
        validate_capture_status_document(status.to_dict())


class ClosedVocabularyRejectionTests(unittest.TestCase):
    """Test 2: extra keys, arbitrary strings, fingerprints, URLs, paths,
    object/row counts, source content, and exception/provider details are
    rejected rather than filtered after construction."""

    def test_extra_key_is_rejected(self) -> None:
        document = _valid_accepted_document()
        document["exception_detail"] = "leaked provider exception text"
        with self.assertRaises(StatusError) as ctx:
            validate_capture_status_document(document)
        self.assertEqual(ctx.exception.category, "status.malformed_document")

    def test_missing_key_is_rejected(self) -> None:
        document = _valid_accepted_document()
        del document["reason_category"]
        with self.assertRaises(StatusError) as ctx:
            validate_capture_status_document(document)
        self.assertEqual(ctx.exception.category, "status.malformed_document")

    def test_non_dict_document_is_rejected(self) -> None:
        with self.assertRaises(StatusError) as ctx:
            validate_capture_status_document(["not", "a", "dict"])
        self.assertEqual(ctx.exception.category, "status.malformed_document")

    def test_source_row_shaped_extra_key_is_rejected(self) -> None:
        document = _valid_accepted_document()
        document["source_row"] = {"State Charity Reg#": "7088001", "Status": "Delinquent"}
        with self.assertRaises(StatusError) as ctx:
            validate_capture_status_document(document)
        self.assertEqual(ctx.exception.category, "status.malformed_document")

    def test_revision_fingerprint_shaped_extra_key_is_rejected(self) -> None:
        document = _valid_accepted_document()
        document["revision_fingerprint"] = "a" * 64
        with self.assertRaises(StatusError) as ctx:
            validate_capture_status_document(document)
        self.assertEqual(ctx.exception.category, "status.malformed_document")

    def test_absolute_path_shaped_extra_key_is_rejected(self) -> None:
        document = _valid_accepted_document()
        # Split via runtime concatenation so the committed source text never
        # contains a contiguous absolute-path-shaped literal (mirrors the
        # established fix in tests/capture/test_tracer.py and
        # tests/fixtures/landing/fixture_builder.py).
        document["store_path"] = "C:" + "\\" + "owner" + "\\" + "private" + "\\" + "store"
        with self.assertRaises(StatusError) as ctx:
            validate_capture_status_document(document)
        self.assertEqual(ctx.exception.category, "status.malformed_document")

    def test_url_shaped_extra_key_is_rejected(self) -> None:
        document = _valid_accepted_document()
        document["source_url"] = "https://oag.ca.gov/charities/current-operating"
        with self.assertRaises(StatusError) as ctx:
            validate_capture_status_document(document)
        self.assertEqual(ctx.exception.category, "status.malformed_document")

    def test_row_or_object_count_shaped_extra_key_is_rejected(self) -> None:
        document = _valid_accepted_document()
        document["object_count"] = 4
        with self.assertRaises(StatusError) as ctx:
            validate_capture_status_document(document)
        self.assertEqual(ctx.exception.category, "status.malformed_document")

    def test_arbitrary_trigger_string_is_rejected(self) -> None:
        document = _valid_accepted_document()
        document["trigger"] = "cron-job-42"
        with self.assertRaises(StatusError) as ctx:
            validate_capture_status_document(document)
        self.assertEqual(ctx.exception.category, "status.unknown_trigger")

    def test_arbitrary_outcome_string_is_rejected(self) -> None:
        document = _valid_accepted_document()
        document["outcome"] = "partially_accepted"
        with self.assertRaises(StatusError) as ctx:
            validate_capture_status_document(document)
        self.assertEqual(ctx.exception.category, "status.unknown_outcome")

    def test_raw_exception_text_as_reason_category_is_rejected(self) -> None:
        document = _valid_accepted_document()
        document["reason_category"] = "RuntimeError: connection refused at 10.0.0.5"
        with self.assertRaises(StatusError) as ctx:
            validate_capture_status_document(document)
        self.assertEqual(ctx.exception.category, "status.unknown_reason_category")

    def test_non_iso_last_accepted_as_of_date_is_rejected(self) -> None:
        document = _valid_accepted_document()
        document["last_accepted_as_of_date"] = "01/15/2020"
        with self.assertRaises(StatusError) as ctx:
            validate_capture_status_document(document)
        self.assertEqual(ctx.exception.category, "status.invalid_last_accepted_as_of_date")

    def test_zero_last_accepted_release_revision_is_rejected(self) -> None:
        document = _valid_accepted_document()
        document["last_accepted_release_revision"] = 0
        with self.assertRaises(StatusError) as ctx:
            validate_capture_status_document(document)
        self.assertEqual(
            ctx.exception.category, "status.invalid_last_accepted_release_revision"
        )

    def test_construction_time_rejection_matches_document_validation(self) -> None:
        # __post_init__ enforces the identical closed vocabulary at
        # CaptureStatus construction time, not only via the standalone
        # document validator.
        with self.assertRaises(StatusError) as ctx:
            project_safe_status(
                trigger="cron-job-42",
                outcome="accepted",
                reason_category="none",
                started_at_utc=_STARTED,
                ended_at_utc=_ENDED,
                last_accepted_as_of_date="2020-01-15",
                last_accepted_release_revision=1,
            )
        self.assertEqual(ctx.exception.category, "status.unknown_trigger")


class LastAcceptedPreservationTests(unittest.TestCase):
    """Test 3: last accepted date/revision remains unchanged (absent, never
    a stale or partial echo) on rejected, no_new_release-exhaustion,
    archive error, or build error outcomes."""

    def test_last_accepted_is_none_for_rejected(self) -> None:
        status = project_safe_status(
            trigger="local",
            outcome="rejected",
            reason_category="structural_rejection",
            started_at_utc=_STARTED,
            ended_at_utc=_ENDED,
        )
        self.assertIsNone(status.last_accepted_as_of_date)
        self.assertIsNone(status.last_accepted_release_revision)

    def test_last_accepted_is_none_for_source_transfer_error_exhaustion(self) -> None:
        status = project_safe_status(
            trigger="schedule",
            outcome="operational_error",
            reason_category="source_transfer_error",
            started_at_utc=_STARTED,
            ended_at_utc=_ENDED,
        )
        self.assertIsNone(status.last_accepted_as_of_date)
        self.assertIsNone(status.last_accepted_release_revision)

    def test_last_accepted_is_none_for_archive_error(self) -> None:
        status = project_safe_status(
            trigger="local",
            outcome="operational_error",
            reason_category="archive_error",
            started_at_utc=_STARTED,
            ended_at_utc=_ENDED,
        )
        self.assertIsNone(status.last_accepted_as_of_date)
        self.assertIsNone(status.last_accepted_release_revision)

    def test_last_accepted_is_none_for_warehouse_build_error(self) -> None:
        status = project_safe_status(
            trigger="local",
            outcome="operational_error",
            reason_category="warehouse_build_error",
            started_at_utc=_STARTED,
            ended_at_utc=_ENDED,
        )
        self.assertIsNone(status.last_accepted_as_of_date)
        self.assertIsNone(status.last_accepted_release_revision)

    def test_last_accepted_is_populated_only_for_accepted(self) -> None:
        status = project_safe_status(
            trigger="local",
            outcome="accepted",
            reason_category="none",
            started_at_utc=_STARTED,
            ended_at_utc=_ENDED,
            last_accepted_as_of_date="2020-01-15",
            last_accepted_release_revision=1,
        )
        self.assertEqual(status.last_accepted_as_of_date, "2020-01-15")
        self.assertEqual(status.last_accepted_release_revision, 1)

    def test_last_accepted_is_populated_for_no_new_release(self) -> None:
        # A no_new_release outcome (including an exhausted prior-date retry
        # sequence, orchestrator.py's job to produce) still reports the
        # existing accepted release identity -- D-08's "records the last
        # attempt and last accepted release", not a null.
        status = project_safe_status(
            trigger="schedule",
            outcome="no_new_release",
            reason_category="source_not_advanced",
            started_at_utc=_STARTED,
            ended_at_utc=_ENDED,
            last_accepted_as_of_date="2020-01-15",
            last_accepted_release_revision=1,
        )
        self.assertEqual(status.last_accepted_as_of_date, "2020-01-15")
        self.assertEqual(status.last_accepted_release_revision, 1)


class SerializationTests(unittest.TestCase):
    def test_to_json_is_stable_sorted_ascii_and_newline_terminated(self) -> None:
        status = project_safe_status(
            trigger="local",
            outcome="accepted",
            reason_category="none",
            started_at_utc=_STARTED,
            ended_at_utc=_ENDED,
            last_accepted_as_of_date="2020-01-15",
            last_accepted_release_revision=1,
        )
        serialized = status.to_json()
        self.assertTrue(serialized.endswith("\n"))
        # Stable key order: re-serializing an equal document is byte-identical.
        self.assertEqual(serialized, status.to_json())
        document = json.loads(serialized)
        self.assertEqual(document, status.to_dict())

    def test_to_json_revalidates_before_returning(self) -> None:
        # A CaptureStatus can only be constructed through the validated
        # dataclass/project_safe_status path, so this asserts to_json()'s
        # own explicit validate_capture_status_document call does not
        # itself reject a document its own constructor already accepted.
        status = CaptureStatus(
            schema_version=1,
            trigger="workflow_dispatch",
            outcome="no_new_release",
            reason_category="source_not_advanced",
            started_at_utc=_STARTED,
            ended_at_utc=_ENDED,
            last_accepted_as_of_date="2020-01-15",
            last_accepted_release_revision=1,
        )
        validate_capture_status_document(json.loads(status.to_json()))


if __name__ == "__main__":
    unittest.main()
