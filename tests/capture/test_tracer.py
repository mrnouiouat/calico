"""Offline end-to-end tracer proof (06-01-PLAN.md Task 1).

Proves the production `calico_capture.orchestrator.capture()` skeleton
carries one identity-free candidate through the real
`calico_landing.admission.admit()` boundary, the real
`calico_capture.archive.synchronize_verified_transaction()` boundary, and
an injected real-build spy, to a closed `accepted` status projection --
entirely offline, using only a local `FakeArchive` and a fixture directory
already committed under `tests/fixtures/landing/valid/`
(`tests.fixtures.landing.fixture_builder.mutated_candidate`). No live
network or private archive is ever contacted.

A second test proves that an archive failure -- including one whose
underlying provider exception carries a supplied sensitive detail -- always
collapses to the closed `operational_error` outcome with a fixed
`reason_category`, and that the supplied detail never appears anywhere in
the returned status's JSON projection (D-09 non-echo discipline).
"""

from __future__ import annotations

import contextlib
import json
import unittest
from pathlib import Path
from typing import Iterator

from calico_capture.orchestrator import capture
from calico_capture.status import CaptureStatus
from tests.capture.fakes import FakeArchive
from tests.fixtures.landing.fixture_builder import MANIFEST_FILENAME, MutatedCandidate, mutated_candidate

#: The committed baseline candidate's shared As-of Date
#: (`tests/fixtures/landing/valid/`), reused across this repository's own
#: landing test suite.
_BASELINE_AS_OF_DATE = "2020-01-15"

#: A clock reporting exactly the baseline candidate's own As-of Date --
#: makes a `no_new_release` result against the baseline candidate a
#: *current*-date (idempotent, single-attempt, no real sleep) result rather
#: than a *prior*-date result the D-05 retry loop would otherwise treat as
#: "the source has not yet republished today" and retry against a real
#: `time.sleep`-backed default sleeper (mirrors
#: `tests.capture.test_orchestrator._CURRENT_DATE_CLOCK_TIMESTAMP` exactly).
_CURRENT_DATE_CLOCK_TIMESTAMP = f"{_BASELINE_AS_OF_DATE}T17:17:00.000Z"


def _recompute_content_length(candidate_root: Path) -> None:
    """Resynchronize a mutated candidate's manifest `content_length`
    fields with the mutated CSVs' actual on-disk byte counts (mirrors
    `tests.landing.test_admission._recompute_content_length`)."""

    manifest_path = candidate_root / MANIFEST_FILENAME
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in document["objects"].values():
        csv_path = candidate_root / entry["relative_path"]
        entry["content_length"] = csv_path.stat().st_size
    manifest_path.write_text(json.dumps(document), encoding="utf-8")


@contextlib.contextmanager
def _status_contract_compliant_candidate() -> "Iterator[MutatedCandidate]":
    """One identity-free candidate that is both structurally valid *and*
    compliant with the closed 33-value `Registry Status` vocabulary
    `capture()` always opts into via `load_default_status_contract()`
    (04-01-PLAN.md D-02/D-22).

    The committed baseline fixture predates that closed vocabulary and
    carries non-compliant placeholder statuses ("Active", "Reporting
    Incomplete") -- see `calico_landing.admission.admit`'s own docstring
    and `tests.landing.test_admission.StatusVocabularyEnforcementTests`.
    This mirrors that test module's own
    `test_admit_with_status_contract_accepts_a_fully_compliant_candidate`
    fixture exactly, rather than mutating the shared baseline.
    """

    with mutated_candidate() as candidate:
        candidate.replace_field("charities-may-operate", 0, 0, "Current")
        candidate.replace_field("charities-may-operate", 1, 0, "Current")
        candidate.replace_field("charities-may-operate", 2, 0, "Current")
        candidate.replace_field("charities-undetermined-status", 0, 0, "Not Registered")
        candidate.replace_field("charities-undetermined-status", 1, 0, "Not Registered")
        _recompute_content_length(candidate.root)
        yield candidate


class _BuildSpy:
    """A minimal injected real-build boundary spy matching
    `calico_dbt.runner.build(mode="real", store=...) -> BuildOutcome`'s
    call site exactly (`<interfaces>` block): callable with the store
    root, exposing `.succeeded`. Never invokes real dbt.
    """

    def __init__(self, *, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.calls: list[object] = []

    def __call__(self, store_root: object) -> "_BuildSpy":
        self.calls.append(store_root)
        return self

    @property
    def succeeded(self) -> bool:
        return self.succeeds


class TracerAcceptedPathTests(unittest.TestCase):
    def test_one_identity_free_candidate_is_traced_to_accepted_status(self) -> None:
        with _status_contract_compliant_candidate() as candidate:
            archive = FakeArchive()
            build_spy = _BuildSpy(succeeds=True)

            status = capture(
                trigger="local",
                archive=archive,
                fetch_candidate=lambda: candidate.root,
                build=build_spy,
            )

        self.assertIsInstance(status, CaptureStatus)
        self.assertEqual(status.outcome, "accepted")
        self.assertEqual(status.reason_category, "none")
        self.assertEqual(status.trigger, "local")
        self.assertEqual(status.last_accepted_as_of_date, _BASELINE_AS_OF_DATE)
        self.assertEqual(status.last_accepted_release_revision, 1)
        self.assertLessEqual(status.started_at_utc, status.ended_at_utc)

        # The injected real-build boundary was actually invoked exactly
        # once, with the fresh external store `capture()` built -- never
        # skipped, never invoked more than once.
        self.assertEqual(len(build_spy.calls), 1)

        # The archive actually received a complete, restorable transaction:
        # at minimum the revision manifest content object and the
        # transaction manifest itself (uploaded last).
        keys = archive.all_keys()
        self.assertTrue(any(key.endswith("archive-transaction.json") for key in keys))
        self.assertTrue(any("releases/" in key and key.endswith("manifest.json") for key in keys))

        # Closed positive projection: exactly the fixed key set, and no
        # path-like content anywhere in the serialized document.
        document = status.to_dict()
        self.assertEqual(
            set(document.keys()),
            {
                "schema_version",
                "trigger",
                "outcome",
                "reason_category",
                "started_at_utc",
                "ended_at_utc",
                "last_accepted_as_of_date",
                "last_accepted_release_revision",
            },
        )
        serialized = status.to_json()
        self.assertNotIn("\\", serialized)
        self.assertNotIn("Temp", serialized)

    def test_replaying_the_same_accepted_candidate_is_idempotent(self) -> None:
        """A second `capture()` call with the exact same identity-free
        candidate correctly re-admits as `no_new_release` -- proving the
        real default restore-before-capture boundary
        (`calico_capture.orchestrator._restore_before_capture` /
        `calico_capture.restore.restore_latest_known_transaction`, CR-01
        fix) actually restores the first call's already-archived
        transaction into the second call's own fresh store before
        admission runs, rather than always presenting `admit()` with an
        empty store (which would silently re-admit as a spurious
        `accepted` revision 1 every time, exactly the bug CR-01 fixed).
        Neither call injects a `restore=` override. Its own archive
        synchronization is itself byte-verified idempotent against the
        first call's already-written transaction when replayed against the
        same archive instance.
        """

        with _status_contract_compliant_candidate() as candidate:
            archive = FakeArchive()

            first = capture(
                trigger="local",
                archive=archive,
                fetch_candidate=lambda: candidate.root,
                build=_BuildSpy(succeeds=True),
                clock=lambda: _CURRENT_DATE_CLOCK_TIMESTAMP,
            )
            self.assertEqual(first.outcome, "accepted")
            self.assertEqual(first.last_accepted_release_revision, 1)
            keys_after_first = archive.all_keys()

            second_build_spy = _BuildSpy(succeeds=True)
            second = capture(
                trigger="local",
                archive=archive,
                fetch_candidate=lambda: candidate.root,
                build=second_build_spy,
                clock=lambda: _CURRENT_DATE_CLOCK_TIMESTAMP,
            )

        # The second call's default restore discovers and restores the
        # first call's already-archived transaction, so admission correctly
        # observes the same content already promoted for the same date and
        # reports no_new_release -- not a second, spuriously-numbered
        # revision 1. Its own synchronize_verified_transaction resolves to
        # the exact same archive keys as the first call and is a
        # byte-verified no-op against them -- no new or different keys ever
        # appear, and the real build boundary is never invoked (build only
        # ever runs for a genuinely accepted outcome).
        self.assertEqual(second.outcome, "no_new_release")
        self.assertEqual(second.reason_category, "source_not_advanced")
        self.assertEqual(second.last_accepted_as_of_date, _BASELINE_AS_OF_DATE)
        self.assertEqual(second.last_accepted_release_revision, 1)
        self.assertEqual(len(second_build_spy.calls), 0)
        self.assertEqual(archive.all_keys(), keys_after_first)
        for key in keys_after_first:
            self.assertEqual(archive.version_count(key), 1)


class TracerClosedFailurePathTests(unittest.TestCase):
    def test_archive_failure_collapses_to_operational_error_without_echoing_detail(self) -> None:
        # Split via runtime concatenation so the committed source text never
        # contains a contiguous absolute-path-shaped literal (the privacy
        # scanner's raw blob-text matching false-positives on exactly this
        # class of synthetic sentinel -- mirrors the established fix in
        # `tests/fixtures/landing/fixture_builder.py` and Phase 1/2's own
        # test literals) while the runtime value stays byte-identical.
        sensitive_detail = "leaked-secret-path " + "C:" + "\\" + "owner" + "\\" + "private" + "\\" + "RegistryData-key-fragment"

        with _status_contract_compliant_candidate() as candidate:
            archive = FakeArchive()
            archive.fail_all_writes(detail=sensitive_detail)
            build_spy = _BuildSpy(succeeds=True)

            status = capture(
                trigger="local",
                archive=archive,
                fetch_candidate=lambda: candidate.root,
                build=build_spy,
            )

        self.assertEqual(status.outcome, "operational_error")
        self.assertEqual(status.reason_category, "archive_error")

        # Admission committed locally (archive failure happens after), but
        # the real-build boundary must never run once the archive
        # transaction could not be verified -- the visible last-accepted
        # release must not advance on an unverified archive (Pattern 2).
        self.assertEqual(len(build_spy.calls), 0)

        serialized = status.to_json()
        self.assertNotIn(sensitive_detail, serialized)
        self.assertNotIn("owner", serialized)
        self.assertNotIn("RegistryData", serialized)
        self.assertEqual(
            set(status.to_dict().keys()),
            {
                "schema_version",
                "trigger",
                "outcome",
                "reason_category",
                "started_at_utc",
                "ended_at_utc",
                "last_accepted_as_of_date",
                "last_accepted_release_revision",
            },
        )
        self.assertIsNone(status.last_accepted_as_of_date)
        self.assertIsNone(status.last_accepted_release_revision)

    def test_structural_rejection_never_reaches_archive_or_build(self) -> None:
        from tests.fixtures.landing.fixture_builder import wrong_header

        with wrong_header() as candidate:
            archive = FakeArchive()
            build_spy = _BuildSpy(succeeds=True)

            status = capture(
                trigger="local",
                archive=archive,
                fetch_candidate=lambda: candidate.root,
                build=build_spy,
            )

        self.assertEqual(status.outcome, "rejected")
        self.assertEqual(status.reason_category, "structural_rejection")
        self.assertEqual(archive.all_keys(), ())
        self.assertEqual(len(build_spy.calls), 0)
        self.assertIsNone(status.last_accepted_as_of_date)
        self.assertIsNone(status.last_accepted_release_revision)


if __name__ == "__main__":
    unittest.main()
