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
import unittest

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


if __name__ == "__main__":
    unittest.main()
