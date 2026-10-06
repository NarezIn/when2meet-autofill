"""
Turn reviewed busy events into the set of When2meet slots to mark available.

Every slot and every busy event is converted to an absolute time interval, so
timezones and DST are handled in one place:

* Specific-dates events: When2meet's slot timestamps are real instants.
* Days-of-the-week events: timestamps live in a fixed 1978 week and encode the
  event-timezone wall clock as if it were UTC. We decode that wall clock and
  place it in a reference week (the upcoming week by default).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from .models import WEEKDAYS, FillPlanRequest, FillPlanResponse, ReviewedEvent, Weekday, parse_hhmm

UTC = dt.timezone.utc


@dataclass(frozen=True)
class Interval:
    """A half-open time interval [start, end) in absolute time."""

    start: dt.datetime
    end: dt.datetime

    def overlaps(self, other: Interval) -> bool:
        """
        Check whether two half-open intervals share any time.

        Args:
            other (Interval): The other interval.

        Returns:
            bool: True if they overlap by more than zero seconds.
        """
        return self.start < other.end and other.start < self.end


def weekday_of(date: dt.date) -> Weekday:
    """
    Return the short weekday name of a date.

    Args:
        date (datetime.date): Any date.

    Returns:
        Weekday: "Mon".."Sun".
    """
    return WEEKDAYS[date.weekday()]


def decode_days_of_week_slot(timestamp: int) -> tuple[Weekday, int]:
    """
    Decode a days-of-the-week slot timestamp into a weekday and wall-clock minutes.

    Args:
        timestamp (int): A When2meet TimeOfSlot value from a days-of-the-week event.

    Returns:
        tuple[Weekday, int]: The weekday and minutes after midnight in the event's timezone.
    """
    moment = dt.datetime.fromtimestamp(timestamp, UTC)
    return weekday_of(moment.date()), moment.hour * 60 + moment.minute


def is_days_of_week(slots: list[int]) -> bool:
    """
    Guess the event mode from slot timestamps (days-of-week slots sit in 1978).

    Args:
        slots (list[int]): When2meet TimeOfSlot values.

    Returns:
        bool: True if every timestamp is from before 2001.
    """
    return bool(slots) and max(slots) < 1_000_000_000


def next_on_or_after(start: dt.date, weekday: Weekday) -> dt.date:
    """
    Find the first date on or after `start` that falls on `weekday`.

    Args:
        start (datetime.date): The earliest allowed date.
        weekday (Weekday): The weekday to look for.

    Returns:
        datetime.date: The matching date.
    """
    delta = (WEEKDAYS.index(weekday) - start.weekday()) % 7
    return start + dt.timedelta(days=delta)


def local_interval(date: dt.date, start_min: int, end_min: int, tz: ZoneInfo) -> Interval:
    """
    Build an absolute interval from a local date and minute range.

    An end at or before the start is treated as crossing midnight.

    Args:
        date (datetime.date): The local calendar date of the start.
        start_min (int): Start, minutes after local midnight.
        end_min (int): End, minutes after local midnight.
        tz (ZoneInfo): The timezone the times are in.

    Returns:
        Interval: The interval in absolute (UTC) time.
    """
    if end_min <= start_min:
        end_min += 24 * 60
    # Aware-datetime + timedelta is wall-clock arithmetic in Python, so DST days map correctly.
    midnight = dt.datetime.combine(date, dt.time(0), tzinfo=tz)
    start = midnight + dt.timedelta(minutes=start_min)
    end = midnight + dt.timedelta(minutes=end_min)
    return Interval(start.astimezone(UTC), end.astimezone(UTC))


def slot_intervals(req: FillPlanRequest, today: dt.date) -> dict[int, Interval]:
    """
    Convert each When2meet slot timestamp into an absolute interval.

    Args:
        req (FillPlanRequest): The request with slots, mode and timezones.
        today (datetime.date): Used as the reference week in days-of-week mode.

    Returns:
        dict[int, Interval]: Slot timestamp -> interval.
    """
    length = dt.timedelta(minutes=req.slot_minutes)
    out: dict[int, Interval] = {}
    if req.mode == "specific_dates":
        for ts in req.slots:
            start = dt.datetime.fromtimestamp(ts, UTC)
            out[ts] = Interval(start, start + length)
        return out

    event_tz = ZoneInfo(req.event_tz or req.screenshot_tz)
    reference = req.reference_date or today
    for ts in req.slots:
        weekday, minutes = decode_days_of_week_slot(ts)
        date = next_on_or_after(reference, weekday)
        out[ts] = local_interval(date, minutes, minutes + req.slot_minutes, event_tz)
    return out


def event_dates(
    event: ReviewedEvent,
    req: FillPlanRequest,
    slot_dates: list[dt.date],
    today: dt.date,
) -> tuple[list[dt.date], list[dt.date]]:
    """
    Work out which calendar dates (in the screenshot timezone) an event applies to.

    Args:
        event (ReviewedEvent): The busy event.
        req (FillPlanRequest): The request (mode, weekday_dates, reference_date).
        slot_dates (list[datetime.date]): Sorted distinct local dates covered by the slots.
        today (datetime.date): Fallback reference date for days-of-week mode.

    Returns:
        tuple[list[date], list[date]]: (dates to apply the event to, ambiguous candidate dates).
        The second list is non-empty only when the weekday matches several dates and the
        user has not said which one is meant.
    """
    if req.mode == "days_of_week":
        reference = req.reference_date or today
        base = next_on_or_after(reference, event.day)
        # Neighbouring weeks cover events that cross the reference week's edges after tz conversion.
        return [base - dt.timedelta(days=7), base, base + dt.timedelta(days=7)], []

    if event.date is not None:
        return [event.date], []
    if event.day in req.weekday_dates:
        return [req.weekday_dates[event.day]], []
    matches = [d for d in slot_dates if weekday_of(d) == event.day]
    if len(matches) > 1:
        return [], matches
    return matches, []


def busy_intervals(event: ReviewedEvent, dates: list[dt.date], tz: ZoneInfo, buffer_minutes: int) -> list[Interval]:
    """
    Expand an event into absolute busy intervals, one per date, with buffers.

    Args:
        event (ReviewedEvent): The busy event.
        dates (list[datetime.date]): Local dates the event applies to.
        tz (ZoneInfo): The screenshot timezone.
        buffer_minutes (int): Minutes added before and after.

    Returns:
        list[Interval]: The busy intervals.
    """
    if event.all_day:
        start_min, end_min = 0, 24 * 60
    else:
        start_min, end_min = parse_hhmm(event.start), parse_hhmm(event.end)
    pad = dt.timedelta(minutes=buffer_minutes)
    out = []
    for date in dates:
        base = local_interval(date, start_min, end_min, tz)
        out.append(Interval(base.start - pad, base.end + pad))
    return out


def drop_short_free_runs(available: list[int], intervals: dict[int, Interval], min_minutes: int) -> list[int]:
    """
    Remove free stretches shorter than the minimum free block.

    Args:
        available (list[int]): Slot timestamps currently marked available.
        intervals (dict[int, Interval]): Slot timestamp -> interval.
        min_minutes (int): Minimum length of a free stretch to keep.

    Returns:
        list[int]: The available slots that belong to long-enough free stretches.
    """
    if min_minutes <= 0 or not available:
        return available
    ordered = sorted(available, key=lambda ts: intervals[ts].start)
    runs: list[list[int]] = [[ordered[0]]]
    for ts in ordered[1:]:
        if intervals[ts].start == intervals[runs[-1][-1]].end:
            runs[-1].append(ts)
        else:
            runs.append([ts])
    keep: list[int] = []
    for run in runs:
        length = intervals[run[-1]].end - intervals[run[0]].start
        if length >= dt.timedelta(minutes=min_minutes):
            keep.extend(run)
    return keep


def compute_fill_plan(req: FillPlanRequest, today: dt.date | None = None) -> FillPlanResponse:
    """
    Decide which slots to mark available and which to outline as low confidence.

    Args:
        req (FillPlanRequest): Slots, event mode, timezones, reviewed events and settings.
        today (datetime.date | None): Override for "today" (tests); defaults to the real date.

    Returns:
        FillPlanResponse: Available slots, low-confidence slots, and any weekday ambiguity.
        When some weekday is ambiguous, no slots are returned so the caller can ask the user.
    """
    today = today or dt.date.today()
    screenshot_tz = ZoneInfo(req.screenshot_tz)
    intervals = slot_intervals(req, today)
    slot_dates = sorted({iv.start.astimezone(screenshot_tz).date() for iv in intervals.values()})

    busy: list[Interval] = []
    low: list[Interval] = []
    ambiguous: dict[Weekday, list[dt.date]] = {}
    unmatched: list[int] = []
    for index, event in enumerate(req.events):
        if not event.counts_as_busy or (event.all_day and not req.settings.all_day_busy):
            continue
        dates, candidates = event_dates(event, req, slot_dates, today)
        if candidates:
            ambiguous[event.day] = candidates
            continue
        if not dates:
            unmatched.append(index)
            continue
        busy.extend(busy_intervals(event, dates, screenshot_tz, req.settings.buffer_minutes))
        if event.low_confidence:
            low.extend(busy_intervals(event, dates, screenshot_tz, 0))

    if ambiguous:
        return FillPlanResponse(
            available_slots=[], low_confidence_slots=[], ambiguous_weekdays=ambiguous, unmatched_events=unmatched
        )

    available = [ts for ts, iv in intervals.items() if not any(iv.overlaps(b) for b in busy)]
    available = drop_short_free_runs(available, intervals, req.settings.min_free_block_minutes)
    low_slots = [ts for ts, iv in intervals.items() if any(iv.overlaps(b) for b in low)]
    return FillPlanResponse(
        available_slots=sorted(available),
        low_confidence_slots=sorted(low_slots),
        unmatched_events=unmatched,
    )
