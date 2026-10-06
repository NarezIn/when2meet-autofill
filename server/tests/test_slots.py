"""Tests for slot computation (screenshot2meet.slots)."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from screenshot2meet.app import app
from screenshot2meet.models import FillPlanRequest, PlanSettings, ReviewedEvent
from screenshot2meet.slots import compute_fill_plan, decode_days_of_week_slot, is_days_of_week

NY = ZoneInfo("America/New_York")
DOW_SUNDAY_0800 = 279705600  # Sun 1978-11-12 08:00 "UTC" = 08:00 event wall clock


def ny_ts(date: dt.date, hhmm: str) -> int:
    """
    Unix timestamp of a New York wall-clock time.

    Args:
        date (datetime.date): The local date.
        hhmm (str): The local time "HH:MM".

    Returns:
        int: Seconds since the epoch.
    """
    h, m = map(int, hhmm.split(":"))
    return int(dt.datetime.combine(date, dt.time(h, m), tzinfo=NY).timestamp())


def day_slots(date: dt.date, start: str = "08:00", end: str = "12:00") -> list[int]:
    """
    Specific-dates slots every 15 minutes for one New York day.

    Args:
        date (datetime.date): The local date.
        start (str): First slot start "HH:MM".
        end (str): End of the last slot "HH:MM".

    Returns:
        list[int]: Slot timestamps.
    """
    first, last = ny_ts(date, start), ny_ts(date, end)
    return list(range(first, last, 900))


def dow_slots(weekday_index: int, start_hour: int = 8, end_hour: int = 12) -> list[int]:
    """
    Days-of-week slots for one weekday, encoded the way When2meet does.

    Args:
        weekday_index (int): 0=Sun .. 6=Sat (When2meet column order).
        start_hour (int): First slot hour.
        end_hour (int): End hour (exclusive).

    Returns:
        list[int]: Slot timestamps.
    """
    day0 = DOW_SUNDAY_0800 - 8 * 3600 + weekday_index * 86400
    return list(range(day0 + start_hour * 3600, day0 + end_hour * 3600, 900))


MON = dt.date(2026, 10, 5)
TUE = dt.date(2026, 10, 6)


def test_decode_days_of_week_slot() -> None:
    """The fixed 1978 week decodes to weekday + wall-clock minutes."""
    assert decode_days_of_week_slot(DOW_SUNDAY_0800) == ("Sun", 8 * 60)
    assert decode_days_of_week_slot(DOW_SUNDAY_0800 + 86400 + 900) == ("Mon", 8 * 60 + 15)
    assert is_days_of_week([DOW_SUNDAY_0800])
    assert not is_days_of_week([ny_ts(MON, "08:00")])


def test_specific_dates_busy_block() -> None:
    """A 09:30-11:00 event removes exactly the six slots it covers."""
    slots = day_slots(MON)
    req = FillPlanRequest(
        slots=slots, mode="specific_dates", event_tz="America/New_York",
        events=[ReviewedEvent(day="Mon", start="09:30", end="11:00")],
    )
    plan = compute_fill_plan(req)
    busy = set(slots) - set(plan.available_slots)
    assert busy == set(range(ny_ts(MON, "09:30"), ny_ts(MON, "11:00"), 900))
    assert plan.low_confidence_slots == []


def test_event_matched_by_weekday_only_on_that_day() -> None:
    """A Tue event doesn't touch Monday's slots."""
    slots = day_slots(MON) + day_slots(TUE)
    req = FillPlanRequest(slots=slots, mode="specific_dates", events=[ReviewedEvent(day="Tue", start="09:00", end="10:00")])
    plan = compute_fill_plan(req)
    assert set(day_slots(MON)) <= set(plan.available_slots)
    assert ny_ts(TUE, "09:00") not in plan.available_slots


def test_low_confidence_and_not_busy() -> None:
    """Low-confidence events are outlined; unchecked events are ignored."""
    slots = day_slots(MON)
    req = FillPlanRequest(
        slots=slots, mode="specific_dates",
        events=[
            ReviewedEvent(day="Mon", start="09:00", end="09:15", low_confidence=True),
            ReviewedEvent(day="Mon", start="10:00", end="11:00", counts_as_busy=False),
        ],
    )
    plan = compute_fill_plan(req)
    assert plan.low_confidence_slots == [ny_ts(MON, "09:00")]
    assert ny_ts(MON, "09:00") not in plan.available_slots
    assert ny_ts(MON, "10:00") in plan.available_slots


def test_buffer_and_min_free_block() -> None:
    """Buffers widen events; free gaps shorter than the minimum become unavailable."""
    slots = day_slots(MON)
    events = [
        ReviewedEvent(day="Mon", start="09:00", end="10:00"),
        ReviewedEvent(day="Mon", start="10:30", end="11:00"),
    ]
    buffered = compute_fill_plan(
        FillPlanRequest(slots=slots, mode="specific_dates", events=events, settings=PlanSettings(buffer_minutes=15))
    )
    assert ny_ts(MON, "08:45") not in buffered.available_slots
    assert ny_ts(MON, "08:30") in buffered.available_slots

    min_block = compute_fill_plan(
        FillPlanRequest(
            slots=slots, mode="specific_dates", events=events, settings=PlanSettings(min_free_block_minutes=45)
        )
    )
    # 10:00-10:30 is only 30 minutes free, so it is dropped.
    assert ny_ts(MON, "10:00") not in min_block.available_slots
    assert ny_ts(MON, "11:00") in min_block.available_slots


def test_cross_midnight_event() -> None:
    """An event ending after midnight blocks the next day's early slots."""
    slots = day_slots(MON, "22:00", "23:45") + [ny_ts(TUE, "00:00")]
    req = FillPlanRequest(slots=slots, mode="specific_dates", events=[ReviewedEvent(day="Mon", start="23:30", end="00:15")])
    plan = compute_fill_plan(req)
    assert ny_ts(TUE, "00:00") not in plan.available_slots
    assert ny_ts(MON, "23:15") in plan.available_slots


def test_ambiguous_weekday_and_mapping() -> None:
    """Two Mondays in a specific-dates event require a mapping."""
    next_mon = MON + dt.timedelta(days=7)
    slots = day_slots(MON) + day_slots(next_mon)
    events = [ReviewedEvent(day="Mon", start="09:00", end="10:00")]
    plan = compute_fill_plan(FillPlanRequest(slots=slots, mode="specific_dates", events=events))
    assert plan.ambiguous_weekdays == {"Mon": [MON, next_mon]}
    assert plan.available_slots == []

    resolved = compute_fill_plan(
        FillPlanRequest(slots=slots, mode="specific_dates", events=events, weekday_dates={"Mon": next_mon})
    )
    assert ny_ts(MON, "09:00") in resolved.available_slots
    assert ny_ts(next_mon, "09:00") not in resolved.available_slots


def test_event_with_explicit_date_and_unmatched() -> None:
    """Explicit dates win over weekdays; events on days outside the event are reported."""
    slots = day_slots(MON)
    events = [
        ReviewedEvent(day="Mon", date=MON, start="09:00", end="09:30"),
        ReviewedEvent(day="Fri", start="09:00", end="09:30"),
    ]
    plan = compute_fill_plan(FillPlanRequest(slots=slots, mode="specific_dates", events=events))
    assert ny_ts(MON, "09:00") not in plan.available_slots
    assert plan.unmatched_events == [1]


def test_days_of_week_same_timezone() -> None:
    """Days-of-week slots are matched by weekday and wall clock."""
    slots = dow_slots(1)  # Monday
    req = FillPlanRequest(
        slots=slots, mode="days_of_week", event_tz="America/New_York",
        events=[ReviewedEvent(day="Mon", start="09:00", end="09:30")],
    )
    plan = compute_fill_plan(req, today=dt.date(2026, 10, 6))
    busy = sorted(set(slots) - set(plan.available_slots))
    assert [decode_days_of_week_slot(ts) for ts in busy] == [("Mon", 540), ("Mon", 555)]


def test_days_of_week_other_timezone() -> None:
    """A Los Angeles event's 06:00 slot is 09:00 in New York."""
    slots = dow_slots(1, 5, 9)
    req = FillPlanRequest(
        slots=slots, mode="days_of_week", event_tz="America/Los_Angeles",
        events=[ReviewedEvent(day="Mon", start="09:00", end="09:15")],
    )
    plan = compute_fill_plan(req, today=dt.date(2026, 10, 6))
    busy = sorted(set(slots) - set(plan.available_slots))
    assert [decode_days_of_week_slot(ts) for ts in busy] == [("Mon", 360)]


def test_all_day_setting() -> None:
    """All-day events count only when the setting is on."""
    slots = day_slots(MON)
    events = [ReviewedEvent(day="Mon", start="00:00", end="00:00", all_day=True)]
    off = compute_fill_plan(FillPlanRequest(slots=slots, mode="specific_dates", events=events))
    on = compute_fill_plan(
        FillPlanRequest(slots=slots, mode="specific_dates", events=events, settings=PlanSettings(all_day_busy=True))
    )
    assert off.available_slots == slots
    assert on.available_slots == []


def test_fill_plan_endpoint() -> None:
    """The HTTP endpoint round-trips a request."""
    client = TestClient(app)
    slots = day_slots(MON)
    resp = client.post(
        "/fill-plan",
        json={"slots": slots, "mode": "specific_dates", "events": [{"day": "Mon", "start": "09:00", "end": "09:15"}]},
    )
    assert resp.status_code == 200
    assert ny_ts(MON, "09:00") not in resp.json()["available_slots"]
    assert client.get("/health").json()["status"] == "ok"
