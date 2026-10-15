"""Unit tests for list layouts (Google Calendar Schedule view): time ranges, month headers, day labels, end to end."""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

from screenshot2meet.agenda import DayLabel, MonthHeader, find_day_labels, label_date, parse_month_header, parse_time_range
from screenshot2meet.models import EventContext
from screenshot2meet.ocr import OcrWord

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "google_cal_eg.png"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("14:30–15:00", (870, 900)),
        ("14:30-15:00", (870, 900)),
        ("2:30 – 3:30pm", (870, 930)),
        ("11 – 1pm", (660, 780)),
        ("11am – 12:30pm", (660, 750)),
        ("9:00 AM - 10:00 AM", (540, 600)),
        ("10pm – 1am", (1320, 60)),
        ("下午2:30 - 3:30", (870, 930)),
        ("Lunch, 12:00–13:00", (720, 780)),
    ],
)
def test_parse_time_range(text: str, expected: tuple[int, int]) -> None:
    """Common time-range spellings parse to (start, end) minutes."""
    assert parse_time_range(text) == expected


@pytest.mark.parametrize("text", ["OCTOBER 18 – 24", "CSCI-GA 2565 Office Hour (BY)", "Columbus Day", "12"])
def test_parse_time_range_rejects(text: str) -> None:
    """Date ranges, course numbers and plain titles are not time ranges."""
    assert parse_time_range(text) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("OCTOBER 18 – 24", (10, 18)), ("Oct 25 – Nov 1", (10, 25)), ("10月18日 - 24日", (10, 18)),
        ("10月", (10, None)), ("十月", (10, None)), ("Halloween", None),
    ],
)
def test_parse_month_header(text: str, expected: tuple[int, int] | None) -> None:
    """Week headers give the month of their first day."""
    assert parse_month_header(text) == expected


def word(text: str, x0: int, y0: int, x1: int, y1: int) -> OcrWord:
    """
    Make an OCR word.

    Args:
        text (str): Text.
        x0 (int): Left.
        y0 (int): Top.
        x1 (int): Right.
        y1 (int): Bottom.

    Returns:
        OcrWord: The word.
    """
    return OcrWord(text, 1.0, x0, y0, x1, y1)


def test_find_day_labels_pairs_weekday_and_number() -> None:
    """A weekday name pairs with the day number right below it; titles are ignored."""
    words = [word("WED", 52, 304, 137, 340), word("14", 57, 369, 134, 427), word("Columbus Day", 224, 121, 518, 170)]
    labels = find_day_labels(words, 200)
    assert [(lb.day, lb.day_of_month, lb.text) for lb in labels] == [("Wed", 14, "WED 14")]


def test_label_date_uses_headers_and_event_dates() -> None:
    """Month comes from the header above, or the header below for the first week shown."""
    headers = [MonthHeader(y=760, month=10, first_day=18)]
    above = DayLabel("MON 12", "Mon", "exact", 12, (50, 90, 140, 210))
    below = DayLabel("WED 21", "Wed", "exact", 21, (50, 830, 140, 950))
    ctx = EventContext(year=2026)
    assert label_date(above, headers, ctx) == dt.date(2026, 10, 12)
    assert label_date(below, headers, ctx) == dt.date(2026, 10, 21)
    ctx_dates = EventContext(dates=[dt.date(2026, 10, 12), dt.date(2026, 10, 13)])
    assert label_date(above, [], ctx_dates) == dt.date(2026, 10, 12)


@pytest.mark.skipif(
    importlib.util.find_spec("paddleocr") is None or not FIXTURE.exists(), reason="PaddleOCR or fixture missing"
)
def test_google_schedule_end_to_end() -> None:
    """The Google Calendar fixture yields 4 timed events and 2 all-day events on the right dates."""
    from screenshot2meet.cli import load_image
    from screenshot2meet.extractor import CvOcrExtractor

    events = CvOcrExtractor().extract([load_image(FIXTURE)], EventContext(year=2026))
    got = [(e.day, e.date.isoformat() if e.date else None, e.start, e.end, e.all_day) for e in events]
    assert got == [
        ("Mon", "2026-10-12", "00:00", "24:00", True),
        ("Wed", "2026-10-14", "14:30", "15:00", False),
        ("Wed", "2026-10-14", "15:00", "16:00", False),
        ("Wed", "2026-10-21", "15:00", "16:00", False),
        ("Wed", "2026-10-28", "15:00", "16:00", False),
        ("Sat", "2026-10-31", "00:00", "24:00", True),
    ]
    assert not any(e.low_confidence for e in events)
