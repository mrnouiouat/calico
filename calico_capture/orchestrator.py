"""One production capture entry point with injected external boundaries
(06-01-PLAN.md D-01/D-02/D-06/D-07/D-13/D-14; 06-RESEARCH.md "One production
state machine with injected boundaries").

`capture(...)` is the single state machine scheduled runs, manual
`workflow_dispatch`, and the local operator runbook will all eventually
call (D-06) -- this plan's offline tracer test is the first caller and
proves the skeleton end-to-end with a local fake archive, an injected
candidate fetcher, and an injected real-build spy; no live network or
private archive is ever contacted here.

Every step follows 06-RESEARCH.md Pattern 2 ("Restore Before Capture,
Archive Before Success"): establish a fresh external store, restore
(currently a fresh-layout establishment; full reconstruction from prior
archived transactions is `calico_capture.restore`'s job in a later plan),
call the existing atomic `calico_landing.admission.admit()` with the
closed status-vocabulary contract explicitly opted in, synchronize and
read-back-verify the resulting transaction against the archive boundary
before ever reporting acceptance, then invoke the existing real-mode
`calico_dbt.runner.build()` seam (or an injected spy) before advancing
visible last-accepted status.

Any admission, archive, restore, or build failure -- of any kind, from any
layer -- collapses to exactly one safe `operational_error` outcome plus a
closed `reason_category`. No caught exception's message, type, or chained
cause ever crosses into the returned `CaptureStatus` (D-09; mirrors
`calico_landing.candidate`/`calico_landing.store`'s non-echo discipline).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable

from calico_capture.archive import Archive, synchronize_verified_transaction
from calico_capture.status import CaptureStatus, project_safe_status
from calico_landing.admission import admit, load_default_status_contract
from calico_landing.attempts import utc_now_iso
from calico_landing.result import AdmissionResult
from calico_landing.store import ensure_store_layout

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
#: `started_at_utc`/`ended_at_utc` values without a real wall clock.
Clock = Callable[[], str]


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


def _default_build(store_root: Path) -> object:
    from calico_dbt.runner import build as dbt_build

    return dbt_build(mode="real", store=store_root)


def _restore_before_capture(destination_root: Path) -> None:
    """Establish a fresh, verified store layout before this call's own
    admission attempt (Pattern 2, D-13).

    Full reconstruction of `destination_root` from every prior archived
    transaction is `calico_capture.restore`'s responsibility (a later
    plan's wave). This call still performs the mandated restore-before-
    capture ordering so the production entry point never skips the step;
    for the very first capture into a never-before-archived history the
    correct restored state genuinely is the empty, freshly laid out store
    this establishes. A later plan extends this function's body -- without
    changing its call site or signature -- to actually repopulate
    `destination_root` from every existing archived transaction.
    """

    ensure_store_layout(destination_root)


def _reason_category_for(result: AdmissionResult) -> str:
    if result.status == "accepted":
        return "none"
    if result.status == "no_new_release":
        return "source_not_advanced"
    if result.status == "rejected":
        return "structural_rejection"
    return "none"  # operational_error decided inside admit() itself


def capture(
    *,
    trigger: str,
    archive: Archive,
    fetch_candidate: CandidateFetcher,
    build: BuildFn | None = None,
    clock: Clock = _default_clock,
) -> CaptureStatus:
    """Run one complete restore/admit/archive/build capture attempt and
    return its closed, non-echo `CaptureStatus`.

    `trigger` is one of the closed `"schedule"`/`"workflow_dispatch"`/
    `"local"` values `calico_capture.status` accepts. `archive` and
    `fetch_candidate` are always injected -- there is no default source or
    archive boundary, since this module never contacts a live provider
    itself. `build` defaults to the real `calico_dbt.runner.build(mode=
    "real", ...)` seam; tests inject a spy instead. `clock` defaults to the
    real UTC clock; tests inject a deterministic one.

    Never raises: every admission/archive/restore/build failure, and any
    other unexpected exception, collapses to one `operational_error`
    outcome plus a closed `reason_category` (D-09).
    """

    started_at_utc = clock()

    try:
        with tempfile.TemporaryDirectory(prefix="calico-capture-") as temp_name:
            destination_root = Path(temp_name).resolve()

            try:
                _restore_before_capture(destination_root)
            except CaptureError:
                raise
            except Exception as exc:
                raise CaptureError("restore_error") from exc

            try:
                candidate_input = fetch_candidate()
            except Exception as exc:
                raise CaptureError("source_transfer_error") from exc

            result = admit(
                candidate_input,
                destination_root,
                status_contract=load_default_status_contract(),
            )

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


__all__ = ["BuildFn", "CandidateFetcher", "CaptureError", "Clock", "capture"]
