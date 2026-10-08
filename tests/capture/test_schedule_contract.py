"""Calendar-gate and bounded retry-policy contract tests (06-02-PLAN.md
D-04/D-05; 06-RESEARCH.md Pattern 3 "Calendar Gate Plus Bounded Retry State
Machine").

Proves `calico_capture.orchestrator.is_capture_day` admits exactly the
first and third Wednesday of every UTC calendar month across a ten-year
Gregorian span for the `"schedule"` trigger, while `"workflow_dispatch"`
and `"local"` always bypass the gate -- and that the fixed `retry_delays`
policy is exactly three bounded same-day attempts at 0/90/180 minutes.
Entirely pure-function and offline: no live source, archive, or hosted
scheduler is ever contacted.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

from calico_capture.orchestrator import SCHEDULE_CRON, is_capture_day, retry_delays

#: A closed ten-Gregorian-year span (2020-01-01 inclusive to 2030-01-01
#: exclusive), spanning three leap years (2020, 2024, 2028) -- satisfies
#: 06-VALIDATION.md's "Calendar matrix: every date across at least a
#: ten-year Gregorian span" requirement.
_SPAN_START = datetime.date(2020, 1, 1)
_SPAN_END = datetime.date(2030, 1, 1)


def _iter_span() -> "list[datetime.date]":
    dates = []
    current = _SPAN_START
    while current < _SPAN_END:
        dates.append(current)
        current += datetime.timedelta(days=1)
    return dates


class CalendarGateTenYearSpanTests(unittest.TestCase):
    def test_schedule_trigger_admits_exactly_first_and_third_wednesdays(self) -> None:
        for when in _iter_span():
            admitted = is_capture_day(when, "schedule")
            expected = when.isoweekday() == 3 and (
                1 <= when.day <= 7 or 15 <= when.day <= 21
            )
            self.assertEqual(
                admitted, expected, f"is_capture_day mismatch for {when.isoformat()}"
            )

    def test_schedule_trigger_admits_every_non_wednesday_never(self) -> None:
        for when in _iter_span():
            if when.isoweekday() != 3:
                self.assertFalse(is_capture_day(when, "schedule"), when.isoformat())

    def test_schedule_trigger_admits_at_least_two_wednesdays_every_month_in_span(
        self,
    ) -> None:
        # Guards against a vacuous "always False" implementation silently
        # satisfying the exact-equality check above by never admitting
        # anything: every calendar month genuinely contains a first and a
        # third Wednesday.
        admitted_by_month: dict[tuple[int, int], int] = {}
        for when in _iter_span():
            if is_capture_day(when, "schedule"):
                key = (when.year, when.month)
                admitted_by_month[key] = admitted_by_month.get(key, 0) + 1

        expected_months = {
            (year, month) for year in range(2020, 2030) for month in range(1, 13)
        }
        self.assertEqual(set(admitted_by_month.keys()), expected_months)
        for count in admitted_by_month.values():
            self.assertEqual(count, 2)

    def test_workflow_dispatch_and_local_always_admit_on_any_date(self) -> None:
        probe_dates = (
            datetime.date(2020, 1, 1),
            datetime.date(2020, 2, 29),  # leap day
            datetime.date(2024, 2, 29),  # leap day
            datetime.date(2025, 12, 31),
            datetime.date(2026, 9, 2),  # an ordinary Wednesday, in-window
            datetime.date(2026, 9, 9),  # an ordinary Wednesday, out-of-window
        )
        for when in probe_dates:
            self.assertTrue(is_capture_day(when, "workflow_dispatch"), when.isoformat())
            self.assertTrue(is_capture_day(when, "local"), when.isoformat())


class RetryPolicyConstantsTests(unittest.TestCase):
    def test_retry_delays_is_exactly_zero_ninety_and_one_hundred_eighty_minutes(
        self,
    ) -> None:
        self.assertEqual(retry_delays, (0, 5400, 10800))
        self.assertEqual(len(retry_delays), 3)

    def test_schedule_cron_is_the_documented_weekly_wednesday_expression(self) -> None:
        # Documents the exact D-04 cron a later plan's deployed workflow
        # file must use verbatim (06-RESEARCH.md Pattern 3): POSIX
        # day-of-month and day-of-week fields are OR'd when both are
        # restricted, so `is_capture_day` above -- never a combined
        # restricted cron expression -- is what actually narrows this
        # always-weekly trigger to first/third Wednesdays.
        self.assertEqual(SCHEDULE_CRON, "17 17 * * 3")


class CalendarBoundaryDateTests(unittest.TestCase):
    def _calendar(self):
        path = Path(__file__).resolve().parents[2] / "calico_capture/calendar.py"
        self.assertTrue(path.is_file(), "shared stdlib calendar authority must exist")
        from calico_capture import calendar
        return calendar

    def test_shared_calendar_authority_exists(self):
        self._calendar()

    def test_exact_wednesday_boundaries(self):
        calendar = self._calendar()
        for day, month, expected in ((1, 7, True), (7, 10, True), (8, 7, False),
                                     (15, 7, True), (21, 10, True), (22, 7, False)):
            when = datetime.date(2026, month, day)
            self.assertEqual(when.isoweekday(), 3)
            decision = calendar.decide_calendar(event_name="schedule", dispatch_mode=None,
                                                observed_date=when)
            self.assertEqual(decision.should_run, expected)
            self.assertEqual((decision.mode, decision.trigger, decision.observed_date),
                             ("capture", "schedule", when.isoformat()))

    def test_schedule_decision_matches_ten_year_gate(self):
        calendar = self._calendar()
        for when in _iter_span():
            self.assertEqual(calendar.decide_calendar(event_name="schedule", dispatch_mode=None,
                             observed_date=when).should_run, is_capture_day(when, "schedule"))
        self.assertIs(calendar.is_capture_day, is_capture_day)

    def test_dispatch_and_local_bypass_with_closed_modes(self):
        calendar = self._calendar()
        for event in ("workflow_dispatch", "local"):
            for mode in ("capture", "republish", "authorization_probe"):
                decision = calendar.decide_calendar(event_name=event, dispatch_mode=mode,
                    observed_date=datetime.date(2026, 10, 8))
                self.assertEqual((decision.should_run, decision.mode, decision.trigger),
                                 (True, mode, event))

    def test_unknown_and_null_inputs_fail_closed_without_echo(self):
        calendar = self._calendar()
        for event, mode, when in ((None, None, datetime.date(2026, 10, 7)),
                ("unknown", "capture", datetime.date(2026, 10, 7)),
                ("workflow_dispatch", None, datetime.date(2026, 10, 7)),
                ("workflow_dispatch", "unknown", datetime.date(2026, 10, 7)),
                ("schedule", "unknown", datetime.date(2026, 10, 7)),
                ("schedule", None, None), ("schedule", None, "2026-10-07")):
            with self.subTest(event=event, mode=mode):
                with self.assertRaisesRegex(ValueError, "^calendar.invalid_input$"):
                    calendar.decide_calendar(event_name=event, dispatch_mode=mode, observed_date=when)
        self.assertFalse(calendar.is_capture_day(datetime.date(2026, 10, 7), "unknown"))

    def test_frozen_decision_and_canonical_rendering(self):
        from dataclasses import FrozenInstanceError
        decision = self._calendar().decide_calendar(event_name="schedule", dispatch_mode=None,
                                                   observed_date=datetime.date(2026, 10, 7))
        self.assertEqual(decision.to_json(), '{"mode":"capture","observed_date":"2026-10-07",'
                         '"should_run":true,"trigger":"schedule"}')
        self.assertEqual(decision.to_outputs(), "should_run=true\nmode=capture\ntrigger=schedule\n"
                         "observed_date=2026-10-07\n")
        with self.assertRaises(FrozenInstanceError):
            decision.mode = "republish"

    def test_live_cli_is_stdlib_only_and_rejects_date_override(self):
        import os
        import tempfile
        path = Path(__file__).resolve().parents[2] / "calico_capture/calendar.py"
        self._calendar()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            env = dict(os.environ, GITHUB_EVENT_NAME="workflow_dispatch", CALICO_DISPATCH_MODE="republish",
                       GITHUB_OUTPUT=str(output), CALICO_OBSERVED_DATE="1900-01-01")
            result = subprocess.run([sys.executable, "-S", str(path)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout + result.stderr, "")
            self.assertIn("observed_date=" + datetime.datetime.now(datetime.timezone.utc).date().isoformat(),
                          output.read_text())
            result = subprocess.run([sys.executable, "-S", str(path), "--observed-date", "1900-01-01"],
                                    env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "calendar.invalid_input\n")


if __name__ == "__main__":
    unittest.main()
