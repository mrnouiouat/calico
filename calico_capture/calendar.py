"""Shared stdlib calendar authority; the live CLI always observes actual UTC.

Replay adapters may supply a date to ``decide_calendar``. The live script has
no arguments or environment override for its clock. Execute this file directly
in the no-toolchain job to avoid the capture package's analytical imports.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone

SCHEDULE_CRON = "17 17 * * 3"
_EVENTS = ("schedule", "workflow_dispatch", "local")
_MODES = ("capture", "republish", "authorization_probe")


@dataclass(frozen=True)
class CalendarDecision:
    should_run: bool
    mode: str
    trigger: str
    observed_date: str

    def to_json(self) -> str:
        return json.dumps({"should_run": self.should_run, "mode": self.mode,
                           "trigger": self.trigger, "observed_date": self.observed_date},
                          sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def to_outputs(self) -> str:
        return (f"should_run={'true' if self.should_run else 'false'}\n"
                f"mode={self.mode}\ntrigger={self.trigger}\nobserved_date={self.observed_date}\n")


def is_capture_day(when: date, trigger: str) -> bool:
    """Admit first/third Wednesdays or the two closed manual triggers."""
    if type(when) is not date or trigger not in _EVENTS:
        return False
    if trigger != "schedule":
        return True
    return when.isoweekday() == 3 and (1 <= when.day <= 7 or 15 <= when.day <= 21)


def decide_calendar(*, event_name: str, dispatch_mode: str | None,
                    observed_date: date) -> CalendarDecision:
    """Validate a closed event/mode pair before producing safe output."""
    if (event_name not in _EVENTS or type(observed_date) is not date
            or (dispatch_mode is not None and dispatch_mode not in _MODES)
            or (event_name != "schedule" and dispatch_mode is None)):
        raise ValueError("calendar.invalid_input")
    mode = "capture" if event_name == "schedule" else dispatch_mode
    return CalendarDecision(is_capture_day(observed_date, event_name), mode,
                            event_name, observed_date.isoformat())


def main() -> int:
    try:
        if len(sys.argv) != 1:
            raise ValueError("calendar.invalid_input")
        decision = decide_calendar(event_name=os.environ.get("GITHUB_EVENT_NAME"),
            dispatch_mode=os.environ.get("CALICO_DISPATCH_MODE") or None,
            observed_date=datetime.now(timezone.utc).date())
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(decision.to_outputs())
    except (ValueError, KeyError, OSError):
        print("calendar.invalid_input", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
