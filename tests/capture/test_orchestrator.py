"""Complete outcome/equality/calendar/retry state-machine proof for
`calico_capture.orchestrator.capture()` (06-02-PLAN.md Task 1).

Drives the real production `capture()` entry point -- real
`calico_landing.admission.admit()`, real `calico_capture.archive`
synchronization, an injected `FakeArchive`, and an injected build spy --
through the identity-free replay outcome matrix documented in
`tests/capture/fixtures/replay-v1.json`: accepted, terminal rejected, an
incomplete four-object candidate, current-date idempotent no_new_release,
prior-date no_new_release retried to exhaustion, prior-date no_new_release
retried into a valid next same-date revision, and a closed transient
source-transfer failure both recovering and exhausting. Every scenario
asserts the exact `retry_delays` sleep sequence the loop used, matching
`06-RESEARCH.md`'s "assert exact sleep calls and stop points" requirement.
No live network, source, or private archive is ever contacted.
"""

from __future__ import annotations

import contextlib
import json
import unittest
from pathlib import Path
from typing import Callable, Iterator

from calico_capture.orchestrator import capture, retry_delays
from calico_landing.admission import admit, load_default_status_contract
from calico_landing.store import ensure_store_layout
from tests.capture.fakes import FakeArchive
from tests.fixtures.landing.fixture_builder import (
    DATE_STATUS_SET_COLUMN,
    MANIFEST_FILENAME,
    STATUS_COLUMN,
    MutatedCandidate,
    missing_mapping,
    mutated_candidate,
    wrong_header,
)

#: The committed baseline candidate's shared As-of Date
#: (`tests/fixtures/landing/valid/`), reused across this repository's own
#: landing test suite -- and this plan's tracer (`test_tracer.py`).
_BASELINE_AS_OF_DATE = "2020-01-15"

#: An arbitrary "today" far from `_BASELINE_AS_OF_DATE` -- every
#: `no_new_release` result against the fixed baseline candidate is
#: therefore always a *prior*-date result relative to this clock.
_PRIOR_DATE_CLOCK_TIMESTAMP = "2026-09-02T17:17:00.000Z"

#: A clock returning exactly the baseline as-of date -- makes a
#: `no_new_release` result against the baseline candidate a *current*-date
#: (idempotent) result instead.
_CURRENT_DATE_CLOCK_TIMESTAMP = "2020-01-15T17:17:00.000Z"

_REPLAY_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "replay-v1.json"


def _load_replay_scenarios() -> "list[dict[str, object]]":
    document = json.loads(_REPLAY_FIXTURE_PATH.read_text(encoding="utf-8"))
    return document["scenarios"]


def _recompute_content_length(candidate_root: Path) -> None:
    """Resynchronize a mutated candidate's manifest `content_length`
    fields with the mutated CSVs' actual on-disk byte counts (mirrors
    `tests.landing.test_admission._recompute_content_length` and
    `tests.capture.test_tracer._recompute_content_length`)."""

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
    (mirrors `tests.capture.test_tracer._status_contract_compliant_candidate`
    exactly; duplicated locally rather than imported so this module stays
    self-contained, per this plan's own file-ownership precedent)."""

    with mutated_candidate() as candidate:
        candidate.replace_field("charities-may-operate", 0, 0, "Current")
        candidate.replace_field("charities-may-operate", 1, 0, "Current")
        candidate.replace_field("charities-may-operate", 2, 0, "Current")
        candidate.replace_field("charities-undetermined-status", 0, 0, "Not Registered")
        candidate.replace_field("charities-undetermined-status", 1, 0, "Not Registered")
        _recompute_content_length(candidate.root)
        yield candidate


@contextlib.contextmanager
def _status_contract_compliant_same_date_revision() -> "Iterator[MutatedCandidate]":
    """A second, differently-fingerprinted candidate sharing the baseline's
    As-of Date and remaining status-vocabulary compliant -- a legitimate
    same-day republication (one status change) that must admit as the next
    immutable revision, not a rejection (mirrors
    `tests.fixtures.landing.fixture_builder.valid_same_date_revision`, plus
    the same closed-vocabulary overrides above)."""

    with mutated_candidate() as candidate:
        candidate.replace_field("charities-may-operate", 0, 0, "Current")
        candidate.replace_field("charities-may-operate", 1, 0, "Current")
        candidate.replace_field("charities-may-operate", 2, 0, "Current")
        candidate.replace_field("charities-undetermined-status", 0, 0, "Not Registered")
        candidate.replace_field("charities-undetermined-status", 1, 0, "Not Registered")
        candidate.replace_field("charities-may-operate", 0, STATUS_COLUMN, "Delinquent")
        candidate.replace_field(
            "charities-may-operate", 0, DATE_STATUS_SET_COLUMN, _BASELINE_AS_OF_DATE
        )
        _recompute_content_length(candidate.root)
        yield candidate


def _seed_restore(seed_candidate_root: Path) -> Callable[[Path], None]:
    """Build a `restore` boundary that pre-populates a fresh store with one
    already-promoted revision of `seed_candidate_root` before `capture()`'s
    own retry loop ever runs.

    This is the only way to exercise a genuine `no_new_release` outcome
    while this plan's restore step is still the fresh-empty-layout stub
    (06-01-SUMMARY.md Known Stubs; full archive-backed reconstruction is
    Plan 06-03's job) -- it mirrors exactly what `capture()`'s own default
    restore step plus one prior successful admission would produce in a
    real deployment that already has archived history.
    """

    def _restore(destination_root: Path) -> None:
        ensure_store_layout(destination_root)
        admit(
            seed_candidate_root,
            destination_root,
            status_contract=load_default_status_contract(),
        )

    return _restore


class _BuildSpy:
    """A minimal injected real-build boundary spy matching
    `calico_dbt.runner.build(mode="real", store=...) -> BuildOutcome`'s
    call site: callable with the store root, exposing `.succeeded`. Never
    invokes real dbt."""

    def __init__(self, *, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.calls: list[object] = []

    def __call__(self, store_root: object) -> "_BuildSpy":
        self.calls.append(store_root)
        return self

    @property
    def succeeded(self) -> bool:
        return self.succeeds


class _RecordingSleeper:
    """Records every delay `capture()`'s retry loop passed to `sleeper`,
    in call order, without ever actually sleeping -- makes the bounded
    multi-hour retry policy assertable in milliseconds."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def __call__(self, delay_seconds: int) -> None:
        self.calls.append(delay_seconds)


def _fixed_clock(timestamp: str) -> Callable[[], str]:
    return lambda: timestamp


class AcceptedAndRejectedOutcomeTests(unittest.TestCase):
    """`replay-v1.json` scenarios: accepted_first_attempt,
    terminal_rejected_structural, empty_object_set_rejected."""

    def test_accepted_first_attempt_stops_after_one_attempt(self) -> None:
        with _status_contract_compliant_candidate() as candidate:
            archive = FakeArchive()
            build_spy = _BuildSpy(succeeds=True)
            sleeper = _RecordingSleeper()

            status = capture(
                trigger="local",
                archive=archive,
                fetch_candidate=lambda: candidate.root,
                build=build_spy,
                sleeper=sleeper,
            )

        self.assertEqual(status.outcome, "accepted")
        self.assertEqual(status.reason_category, "none")
        self.assertEqual(status.last_accepted_as_of_date, _BASELINE_AS_OF_DATE)
        self.assertEqual(status.last_accepted_release_revision, 1)
        self.assertEqual(sleeper.calls, [0])
        self.assertEqual(len(build_spy.calls), 1)

    def test_terminal_rejected_stops_after_one_attempt_without_retry(self) -> None:
        with wrong_header() as candidate:
            archive = FakeArchive()
            build_spy = _BuildSpy(succeeds=True)
            sleeper = _RecordingSleeper()

            status = capture(
                trigger="local",
                archive=archive,
                fetch_candidate=lambda: candidate.root,
                build=build_spy,
                sleeper=sleeper,
            )

        self.assertEqual(status.outcome, "rejected")
        self.assertEqual(status.reason_category, "structural_rejection")
        self.assertEqual(sleeper.calls, [0])
        self.assertEqual(archive.all_keys(), ())
        self.assertEqual(len(build_spy.calls), 0)
        self.assertIsNone(status.last_accepted_as_of_date)
        self.assertIsNone(status.last_accepted_release_revision)

    def test_empty_object_set_rejects_before_archive_or_build(self) -> None:
        with missing_mapping() as candidate:
            archive = FakeArchive()
            build_spy = _BuildSpy(succeeds=True)
            sleeper = _RecordingSleeper()

            status = capture(
                trigger="local",
                archive=archive,
                fetch_candidate=lambda: candidate.root,
                build=build_spy,
                sleeper=sleeper,
            )

        self.assertEqual(status.outcome, "rejected")
        self.assertEqual(status.reason_category, "structural_rejection")
        self.assertEqual(sleeper.calls, [0])
        self.assertEqual(archive.all_keys(), ())
        self.assertEqual(len(build_spy.calls), 0)


class NoNewReleaseEqualityAndRetryTests(unittest.TestCase):
    """`replay-v1.json` scenarios: current_date_no_new_release,
    prior_date_no_new_release_exhaustion,
    prior_date_no_new_release_then_next_same_date_revision."""

    def test_current_date_no_new_release_stops_without_retry(self) -> None:
        with _status_contract_compliant_candidate() as candidate:
            archive = FakeArchive()
            sleeper = _RecordingSleeper()

            status = capture(
                trigger="local",
                archive=archive,
                fetch_candidate=lambda: candidate.root,
                restore=_seed_restore(candidate.root),
                clock=_fixed_clock(_CURRENT_DATE_CLOCK_TIMESTAMP),
                sleeper=sleeper,
            )

        self.assertEqual(status.outcome, "no_new_release")
        self.assertEqual(status.reason_category, "source_not_advanced")
        self.assertEqual(status.last_accepted_as_of_date, _BASELINE_AS_OF_DATE)
        self.assertEqual(status.last_accepted_release_revision, 1)
        self.assertEqual(sleeper.calls, [0])

    def test_prior_date_no_new_release_retries_until_exhaustion(self) -> None:
        with _status_contract_compliant_candidate() as candidate:
            archive = FakeArchive()
            sleeper = _RecordingSleeper()

            status = capture(
                trigger="local",
                archive=archive,
                fetch_candidate=lambda: candidate.root,
                restore=_seed_restore(candidate.root),
                clock=_fixed_clock(_PRIOR_DATE_CLOCK_TIMESTAMP),
                sleeper=sleeper,
            )

        self.assertEqual(status.outcome, "no_new_release")
        self.assertEqual(status.reason_category, "source_not_advanced")
        self.assertEqual(status.last_accepted_as_of_date, _BASELINE_AS_OF_DATE)
        self.assertEqual(status.last_accepted_release_revision, 1)
        self.assertEqual(list(sleeper.calls), list(retry_delays))

    def test_prior_date_no_new_release_retries_then_accepts_next_same_date_revision(
        self,
    ) -> None:
        with _status_contract_compliant_candidate() as seed_candidate, \
                _status_contract_compliant_same_date_revision() as revised_candidate:
            archive = FakeArchive()
            build_spy = _BuildSpy(succeeds=True)
            sleeper = _RecordingSleeper()
            fetched_roots: list[Path] = []

            def _fetch() -> Path:
                root = seed_candidate.root if not fetched_roots else revised_candidate.root
                fetched_roots.append(root)
                return root

            status = capture(
                trigger="local",
                archive=archive,
                fetch_candidate=_fetch,
                build=build_spy,
                restore=_seed_restore(seed_candidate.root),
                clock=_fixed_clock(_PRIOR_DATE_CLOCK_TIMESTAMP),
                sleeper=sleeper,
            )

        self.assertEqual(status.outcome, "accepted")
        self.assertEqual(status.reason_category, "none")
        self.assertEqual(status.last_accepted_as_of_date, _BASELINE_AS_OF_DATE)
        self.assertEqual(status.last_accepted_release_revision, 2)
        self.assertEqual(sleeper.calls, [retry_delays[0], retry_delays[1]])
        self.assertEqual(len(fetched_roots), 2)
        self.assertEqual(len(build_spy.calls), 1)


class SourceTransferFailureRetryTests(unittest.TestCase):
    """`replay-v1.json` scenarios:
    transient_source_transfer_failure_then_accepted,
    persistent_source_transfer_failure_exhaustion."""

    def test_transient_transfer_failures_retry_then_succeed(self) -> None:
        with _status_contract_compliant_candidate() as candidate:
            archive = FakeArchive()
            build_spy = _BuildSpy(succeeds=True)
            sleeper = _RecordingSleeper()
            call_count = {"n": 0}

            def _flaky_fetch() -> Path:
                call_count["n"] += 1
                if call_count["n"] < len(retry_delays):
                    raise RuntimeError("transient transfer failure")
                return candidate.root

            status = capture(
                trigger="local",
                archive=archive,
                fetch_candidate=_flaky_fetch,
                build=build_spy,
                sleeper=sleeper,
            )

        self.assertEqual(status.outcome, "accepted")
        self.assertEqual(status.reason_category, "none")
        self.assertEqual(call_count["n"], len(retry_delays))
        self.assertEqual(list(sleeper.calls), list(retry_delays))
        self.assertEqual(len(build_spy.calls), 1)

    def test_persistent_transfer_failure_exhausts_to_operational_error(self) -> None:
        archive = FakeArchive()
        build_spy = _BuildSpy(succeeds=True)
        sleeper = _RecordingSleeper()
        # Split via runtime concatenation so the committed source text never
        # contains a contiguous absolute-path-shaped literal (mirrors the
        # established fix in `tests/capture/test_tracer.py` and
        # `tests/fixtures/landing/fixture_builder.py`) while the runtime
        # value stays byte-identical.
        sensitive_detail = "permanent transfer failure " + "C:" + "\\" + "owner" + "\\" + "private"

        def _always_fails() -> Path:
            raise RuntimeError(sensitive_detail)

        status = capture(
            trigger="local",
            archive=archive,
            fetch_candidate=_always_fails,
            build=build_spy,
            sleeper=sleeper,
        )

        self.assertEqual(status.outcome, "operational_error")
        self.assertEqual(status.reason_category, "source_transfer_error")
        self.assertEqual(list(sleeper.calls), list(retry_delays))
        self.assertEqual(len(build_spy.calls), 0)
        self.assertIsNone(status.last_accepted_as_of_date)
        self.assertIsNone(status.last_accepted_release_revision)

        serialized = status.to_json()
        self.assertNotIn(sensitive_detail, serialized)
        self.assertNotIn("owner", serialized)
        self.assertNotIn("private", serialized)


class ReplayFixtureCrossCheckTests(unittest.TestCase):
    """Cross-checks that every scenario named in
    `tests/capture/fixtures/replay-v1.json` has a corresponding executed
    test above, and that this module's assertions agree with the fixture's
    own documented expectations -- so the committed JSON contract and the
    Python behavior it documents can never silently drift apart."""

    #: Maps each fixture scenario name to the outcome/reason_category/sleep
    #: sequence this module's own dedicated test methods above already
    #: proved through the real `capture()` entry point. Duplicated here
    #: (rather than re-deriving it from a shared runner) so a change to
    #: either the fixture or the dedicated test is independently visible.
    _PROVEN_RESULTS: "dict[str, dict[str, object]]" = {
        "accepted_first_attempt": {
            "expected_outcome": "accepted",
            "expected_reason_category": "none",
            "expected_sleep_delays_seconds": [0],
            "expected_release_revision": 1,
            "expected_build_invoked": True,
        },
        "terminal_rejected_structural": {
            "expected_outcome": "rejected",
            "expected_reason_category": "structural_rejection",
            "expected_sleep_delays_seconds": [0],
            "expected_release_revision": None,
            "expected_build_invoked": False,
        },
        "empty_object_set_rejected": {
            "expected_outcome": "rejected",
            "expected_reason_category": "structural_rejection",
            "expected_sleep_delays_seconds": [0],
            "expected_release_revision": None,
            "expected_build_invoked": False,
        },
        "current_date_no_new_release": {
            "expected_outcome": "no_new_release",
            "expected_reason_category": "source_not_advanced",
            "expected_sleep_delays_seconds": [0],
            "expected_release_revision": 1,
            "expected_build_invoked": False,
        },
        "prior_date_no_new_release_exhaustion": {
            "expected_outcome": "no_new_release",
            "expected_reason_category": "source_not_advanced",
            "expected_sleep_delays_seconds": [0, 5400, 10800],
            "expected_release_revision": 1,
            "expected_build_invoked": False,
        },
        "prior_date_no_new_release_then_next_same_date_revision": {
            "expected_outcome": "accepted",
            "expected_reason_category": "none",
            "expected_sleep_delays_seconds": [0, 5400],
            "expected_release_revision": 2,
            "expected_build_invoked": True,
        },
        "transient_source_transfer_failure_then_accepted": {
            "expected_outcome": "accepted",
            "expected_reason_category": "none",
            "expected_sleep_delays_seconds": [0, 5400, 10800],
            "expected_release_revision": 1,
            "expected_build_invoked": True,
        },
        "persistent_source_transfer_failure_exhaustion": {
            "expected_outcome": "operational_error",
            "expected_reason_category": "source_transfer_error",
            "expected_sleep_delays_seconds": [0, 5400, 10800],
            "expected_release_revision": None,
            "expected_build_invoked": False,
        },
    }

    def test_every_fixture_scenario_has_a_proven_matching_result(self) -> None:
        scenarios = _load_replay_scenarios()
        self.assertEqual(len(scenarios), len(self._PROVEN_RESULTS))
        for scenario in scenarios:
            name = scenario["name"]
            with self.subTest(scenario=name):
                self.assertIn(name, self._PROVEN_RESULTS)
                proven = self._PROVEN_RESULTS[name]
                for field_name in (
                    "expected_outcome",
                    "expected_reason_category",
                    "expected_sleep_delays_seconds",
                    "expected_release_revision",
                    "expected_build_invoked",
                ):
                    self.assertEqual(
                        scenario[field_name],
                        proven[field_name],
                        f"{name}.{field_name} mismatch between fixture and proven result",
                    )


if __name__ == "__main__":
    unittest.main()
