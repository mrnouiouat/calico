"""One production capture entry point with injected external boundaries
(06-01-PLAN.md D-01/D-02/D-06/D-07/D-13/D-14; 06-02-PLAN.md D-04/D-05/D-06;
06-RESEARCH.md "One production state machine with injected boundaries",
Pattern 3 "Calendar Gate Plus Bounded Retry State Machine").

`capture(...)` is the single state machine scheduled runs, manual
`workflow_dispatch`, and the local operator runbook all call (D-06) -- one
restore/admit/archive/build attempt loop, bounded to exactly three domain
attempts at 0/90/180 minutes (D-05), driven entirely by closed admission
outcomes rather than a generic retry-library dependency. No live network or
private archive is ever contacted here; every external boundary (archive,
candidate fetch, build, clock, sleep, restore) is injected.

`is_capture_day` is the separate, pure D-04 calendar-gate function a caller
(the hosted no-secret calendar-gate job of a later plan's workflow) applies
*before* ever invoking `capture()` -- POSIX cron's day-of-month/day-of-week
OR semantics make a combined restricted cron expression wrong for "first and
third Wednesday", so this Python filter is the actual date decision behind
the always-weekly `SCHEDULE_CRON` trigger (06-RESEARCH.md Pitfall/Pattern 3).
`workflow_dispatch` and `local` triggers always bypass this gate, matching
the mandatory manual-recovery path (D-06).

Every step follows 06-RESEARCH.md Pattern 2 ("Restore Before Capture,
Archive Before Success"): establish a fresh external store, restore
(`_restore_before_capture` now restores the single most recently archived
transaction, if any, via `calico_capture.restore.restore_latest_known_transaction`
-- the real, independently proven single-transaction restore-and-build
primitive 06-03-PLAN.md Task 2 built, finally wired into this production
entry point by the 2026-09-03 code review's CR-01 fix; see that module's
docstring for why restoring only the single latest transaction, not the
full historical catalog, is sufficient for `admit()`'s own comparison to be
correct), call the existing atomic
`calico_landing.admission.admit()` with the
closed status-vocabulary contract explicitly opted in, synchronize and
read-back-verify the resulting transaction against the archive boundary
before ever reporting acceptance, then invoke the existing real-mode
`calico_dbt.runner.build()` seam (or an injected spy) before advancing
visible last-accepted status.

Per D-06 (06-03-PLAN.md Task 1), `fetch_candidate` now defaults to the real
production source boundary -- `calico_capture.source.fetch_candidate`, a
fixed four-object bounded HTTPS download -- so schedule, `workflow_dispatch`,
and the local runbook all reach the same production fetcher without each
having to wire it up independently; only tests ever inject a fake fetcher.

Per D-05, only two conditions are retried, at the fixed `retry_delays`
schedule: a `no_new_release` outcome whose reported release date is still
the *prior* accepted date (the source has not yet republished today), and a
closed transient source-transfer failure (`fetch_candidate()` raising).
Every other outcome -- `accepted` (including a valid same-date accepted next
revision), terminal `rejected`, and a `no_new_release` outcome whose date
already matches the expected capture date (idempotent replay within the
same run) -- stops the loop immediately. Restore, archive, and build
failures are never retried: they signal an infrastructure problem, not
source unavailability, and remain single-attempt exactly as before.

Any admission, archive, restore, or build failure -- of any kind, from any
layer -- collapses to exactly one safe `operational_error` outcome plus a
closed `reason_category`. No caught exception's message, type, or chained
cause ever crosses into the returned `CaptureStatus` (D-09; mirrors
`calico_landing.candidate`/`calico_landing.store`'s non-echo discipline).
"""

from __future__ import annotations

import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Callable

from calico_capture.archive import Archive, synchronize_verified_transaction
from calico_capture.status import CaptureStatus, project_safe_status
from calico_landing.admission import admit, load_default_status_contract
from calico_landing.attempts import utc_now_iso
from calico_landing.result import AdmissionResult

#: A candidate fetcher supplies exactly what `calico_landing.admission.admit`
#: accepts as `candidate_input` -- a directory containing `candidate-set.json`
#: and its four mapped payloads. Production callers inject a real source
#: download boundary (a later plan); this plan's tracer injects a fixture
#: directory directly.
CandidateFetcher = Callable[[], "str | Path"]

#: The injected real-build boundary must match
#: `calico_dbt.runner.build(mode="real", store=<external root>) -> BuildOutcome`
#: (the plan's own `<interfaces>` block) -- any object exposing a truthy
#: `.succeeded` attribute satisfies this call site, so tests may inject a
#: lightweight spy instead of `calico_dbt.runner.build` itself.
BuildFn = Callable[[Path], object]

#: A clock returns one UTC timestamp string in `calico_landing.attempts`'s
#: closed `...Z` form. Injectable so tests can assert exact, deterministic
#: `started_at_utc`/`ended_at_utc` values without a real wall clock. Called
#: exactly once per `capture()` call (at the very top) -- the resulting
#: timestamp's date portion is also the "expected as-of date" the D-05 retry
#: decision compares every `no_new_release` result against.
Clock = Callable[[], str]

#: A sleeper accepts one non-negative delay in seconds and returns once that
#: delay has elapsed. Injectable so tests assert the exact retry cadence
#: (06-RESEARCH.md "Assert exact sleep calls and stop points") without a
#: real multi-hour wall-clock wait.
Sleeper = Callable[[int], None]

#: The single-argument boundary a caller may inject in place of the real
#: default restore-before-capture step (below) -- injectable so tests can
#: pre-populate a fresh store with an already-promoted revision before
#: `capture()`'s own retry loop runs, without needing a real archive
#: double wired through the discovery-pointer path the real default now
#: uses (CR-01 fix). Production callers never pass this -- the default
#: always wins outside tests.
RestoreFn = Callable[[Path], None]

#: Fixed D-05 bounded same-day retry policy: exactly three total domain
#: attempts, at 0, 90 x 60, and 180 x 60 seconds -- immediate, then two
#: backoff delays. This is the complete, closed, deterministic domain
#: policy every `capture()` call enforces; there is no generic retry-library
#: dependency and no fourth attempt.
retry_delays: tuple[int, int, int] = (0, 90 * 60, 180 * 60)

#: The weekly cron expression `is_capture_day` narrows to first/third
#: Wednesdays (06-RESEARCH.md Pattern 3). `17:17 UTC` is `09:17`/`10:17`
#: Pacific standard/daylight time, both after the source's documented usual
#: `08:15 Pacific` publication time and away from the top of the hour (D-04).
#: A later plan's deployed workflow file must use this exact literal value.
SCHEDULE_CRON = "17 17 * * 3"


class CaptureError(Exception):
    """Raised internally to unify every capture-layer failure into one
    fixed safe `category` matching `calico_capture.status`'s closed reason
    vocabulary. Never escapes `capture()` -- always caught and projected
    into a safe `CaptureStatus`.
    """

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _default_clock() -> str:
    return utc_now_iso()


def _default_sleeper(delay_seconds: int) -> None:
    if delay_seconds > 0:
        time.sleep(delay_seconds)


def _default_build(store_root: Path) -> object:
    from calico_dbt.runner import build as dbt_build

    return dbt_build(mode="real", store=store_root)


def _default_fetch_candidate() -> "str | Path":
    from calico_capture.source import fetch_candidate as _fetch_candidate

    return _fetch_candidate()


def is_capture_day(when: date, trigger: str) -> bool:
    """The D-04 calendar gate.

    `trigger="schedule"` is admitted only on the first or third Wednesday
    of `when`'s calendar month: ISO weekday `3` (Wednesday) and a
    day-of-month in `1..7` or `15..21`. `"workflow_dispatch"` and `"local"`
    always return `True` -- both bypass the calendar entirely, matching the
    mandatory manual-recovery path (D-06). Every other `trigger` value is
    also treated as an unconditional bypass rather than raising, since this
    is a pure scheduling filter, not a `capture()`-style closed-vocabulary
    boundary; `capture()` itself is what enforces the closed trigger
    vocabulary on its own `trigger` parameter via `CaptureStatus`.

    POSIX cron's day-of-month/day-of-week fields are evaluated with OR
    semantics, so a single combined restricted cron expression cannot
    express "first and third Wednesday" -- `SCHEDULE_CRON` fires weekly and
    this function is the actual date decision a caller (a later plan's
    no-secret calendar-gate job) applies before ever invoking `capture()`.
    """

    if trigger != "schedule":
        return True
    if when.isoweekday() != 3:
        return False
    return 1 <= when.day <= 7 or 15 <= when.day <= 21


class _SkipBuildOutcome:
    """A fixed, always-succeeded `BuildFn` result for the restore-before-
    capture step (mirrors `calico_capture.cli._SkipBuildOutcome` exactly).

    `restore_latest_known_transaction`/`restore_verified_transaction`
    unconditionally invoke their own `build` boundary once per restored
    transaction; running the real, expensive `calico_dbt` build again here
    would be redundant work with no observable benefit -- this function's
    caller (`capture()`, below) already invokes the one real build this
    admission attempt needs, and only for a genuinely `accepted` outcome.
    """

    succeeded = True


def _skip_build(_store_root: Path) -> object:
    return _SkipBuildOutcome()


def _restore_before_capture(archive: Archive, destination_root: Path) -> None:
    """Restore the single most recently archived transaction, if any, into
    `destination_root` before this call's own admission attempt (Pattern 2,
    D-13; CR-01 fix, 2026-09-03 code review).

    `calico_landing.admission.admit()`'s own `no_new_release`/next-
    revision-number decision (`calico_landing.store.commit_revision`) only
    ever inspects the current attempt's expected `as_of_date` and the
    promotion pointer -- both of which the single latest archived
    transaction's own restored `promoted-releases.json` already carries --
    so restoring only that one transaction (via
    `calico_capture.restore.restore_latest_known_transaction`, the real,
    independently proven primitive 06-03-PLAN.md Task 2 built) is
    sufficient for this comparison to be correct; looping the full
    historical catalog is unnecessary here and remains a separate,
    explicitly-invoked operator command (`calico_capture.cli`'s
    `restore-build`). For the very first capture into a never-before-
    archived history, `restore_latest_known_transaction` returns `None` and
    only establishes the empty, freshly laid out store layout this function
    has always established for that case.

    The real `calico_dbt` build is deliberately never re-run during this
    restore step (`build=_skip_build`) -- `capture()`'s own build step,
    below, is the one real build this admission attempt needs, and only
    for a genuinely `accepted` outcome.
    """

    from calico_capture.restore import restore_latest_known_transaction

    restore_latest_known_transaction(archive, destination_root, build=_skip_build)


def _reason_category_for(result: AdmissionResult) -> str:
    if result.status == "accepted":
        return "none"
    if result.status == "no_new_release":
        return "source_not_advanced"
    if result.status == "rejected":
        return "structural_rejection"
    return "none"  # operational_error decided inside admit() itself


def _expected_as_of_date(started_at_utc: str) -> str:
    """The ISO calendar date this capture attempt expects to observe,
    derived once from `started_at_utc`'s date portion -- never re-read from
    a fresh clock call mid-attempt, so the D-05 prior/current-date
    comparison stays fixed for the whole bounded retry window regardless of
    how much wall-clock time the injected `sleeper` actually consumes.
    """

    return started_at_utc[:10]


def _is_prior_date_no_new_release(result: AdmissionResult, expected_as_of_date: str) -> bool:
    """True only for a `no_new_release` result whose reported release date
    is still the prior accepted date -- the sole D-05 no_new_release retry
    condition. A `no_new_release` result already matching the expected
    capture date is idempotent within this run (retrying cannot change it)
    and must stop immediately instead.
    """

    return result.status == "no_new_release" and result.as_of_date != expected_as_of_date


def capture(
    *,
    trigger: str,
    archive: Archive,
    fetch_candidate: CandidateFetcher | None = None,
    build: BuildFn | None = None,
    clock: Clock = _default_clock,
    sleeper: Sleeper = _default_sleeper,
    restore: RestoreFn | None = None,
) -> CaptureStatus:
    """Run one bounded restore/admit/archive/build capture attempt sequence
    and return its closed, non-echo `CaptureStatus`.

    `trigger` is one of the closed `"schedule"`/`"workflow_dispatch"`/
    `"local"` values `calico_capture.status` accepts. `archive` is always
    injected -- there is no default archive boundary, since this module
    never contacts a live private archive itself. `fetch_candidate`
    defaults to the real bounded HTTPS source boundary
    (`calico_capture.source.fetch_candidate`, D-06); tests inject a fake
    fetcher instead. `build` defaults to the real `calico_dbt.runner.build(
    mode="real", ...)` seam; tests inject a spy instead. `clock` defaults to
    the real UTC clock; tests inject a deterministic one. `sleeper` defaults
    to a real `time.sleep`-backed sleeper; tests inject a recording no-op.
    `restore` defaults to the internal `_restore_before_capture` boundary,
    which restores the single most recently archived transaction (if any)
    for `archive` (CR-01 fix; see that function's docstring); production
    callers never pass it, but tests may inject a boundary that
    pre-populates the fresh store differently before the retry loop runs.

    Per D-05, this call attempts `fetch_candidate()` + `admit()` up to
    `len(retry_delays)` times against the *same* restored store, sleeping
    `retry_delays[attempt_index]` seconds before each attempt (including
    the first, a no-op `0`-second sleep). Only two outcomes retry: a
    `no_new_release` result whose date is still the prior accepted date,
    and a `fetch_candidate()` exception (a closed transient source-transfer
    failure). Every other admission outcome -- `accepted`, terminal
    `rejected`, or a `no_new_release` result matching the expected capture
    date -- stops the loop on that same attempt. Exhausting every attempt
    without a stop condition ends the loop on its last decided outcome.

    Archive synchronization and the real-mode build run once, after the
    loop, against whichever `result` the loop stopped on -- never per
    attempt, and never for a `rejected` or `operational_error` outcome.

    Never raises: every admission/archive/restore/build failure, and any
    other unexpected exception, collapses to one `operational_error`
    outcome plus a closed `reason_category` (D-09).
    """

    started_at_utc = clock()
    expected_as_of_date = _expected_as_of_date(started_at_utc)

    try:
        with tempfile.TemporaryDirectory(prefix="calico-capture-") as temp_name:
            destination_root = Path(temp_name).resolve()

            restore_fn = (
                restore
                if restore is not None
                else (lambda root: _restore_before_capture(archive, root))
            )
            try:
                restore_fn(destination_root)
            except CaptureError:
                raise
            except Exception as exc:
                raise CaptureError("restore_error") from exc

            fetch_candidate_fn = (
                fetch_candidate if fetch_candidate is not None else _default_fetch_candidate
            )

            result: AdmissionResult | None = None
            last_attempt_index = len(retry_delays) - 1
            for attempt_index, delay_seconds in enumerate(retry_delays):
                sleeper(delay_seconds)
                is_last_attempt = attempt_index == last_attempt_index

                try:
                    candidate_input = fetch_candidate_fn()
                except Exception as exc:
                    if is_last_attempt:
                        raise CaptureError("source_transfer_error") from exc
                    continue

                result = admit(
                    candidate_input,
                    destination_root,
                    status_contract=load_default_status_contract(),
                )

                if _is_prior_date_no_new_release(result, expected_as_of_date) and not is_last_attempt:
                    continue

                break

            if result is None:
                # Unreachable given the loop above (every path either sets
                # `result` before its final `break`/fall-through or raises
                # on the last attempt's fetch failure) -- kept as a
                # defensive fail-closed guard rather than an assert.
                raise CaptureError("source_transfer_error")

            if result.status in ("accepted", "no_new_release"):
                try:
                    synchronize_verified_transaction(archive, destination_root, result)
                except Exception as exc:
                    raise CaptureError("archive_error") from exc

            if result.status == "accepted":
                build_fn = build if build is not None else _default_build
                try:
                    build_outcome = build_fn(destination_root)
                except Exception as exc:
                    raise CaptureError("warehouse_build_error") from exc
                if not getattr(build_outcome, "succeeded", False):
                    raise CaptureError("warehouse_build_error")

            ended_at_utc = clock()
            has_release_identity = result.status in ("accepted", "no_new_release")
            return project_safe_status(
                trigger=trigger,
                outcome=result.status,
                reason_category=_reason_category_for(result),
                started_at_utc=started_at_utc,
                ended_at_utc=ended_at_utc,
                last_accepted_as_of_date=result.as_of_date if has_release_identity else None,
                last_accepted_release_revision=(
                    result.release_revision if has_release_identity else None
                ),
            )
    except CaptureError as exc:
        ended_at_utc = clock()
        return project_safe_status(
            trigger=trigger,
            outcome="operational_error",
            reason_category=exc.category,
            started_at_utc=started_at_utc,
            ended_at_utc=ended_at_utc,
        )
    except Exception:
        # Anything unanticipated (e.g. `status.StatusError` from a caller
        # somehow supplying an unknown `trigger`) still fails closed rather
        # than propagating a raw exception to a scheduled/manual caller.
        ended_at_utc = clock()
        return project_safe_status(
            trigger=trigger if trigger in ("schedule", "workflow_dispatch", "local") else "local",
            outcome="operational_error",
            reason_category="none",
            started_at_utc=started_at_utc,
            ended_at_utc=ended_at_utc,
        )


__all__ = [
    "BuildFn",
    "CandidateFetcher",
    "CaptureError",
    "Clock",
    "RestoreFn",
    "SCHEDULE_CRON",
    "Sleeper",
    "capture",
    "is_capture_day",
    "retry_delays",
]
