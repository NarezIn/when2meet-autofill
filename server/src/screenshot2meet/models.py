"""Pydantic models shared by the extractor, the slot planner and the HTTP API."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Weekday = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAYS: tuple[Weekday, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

EventMode = Literal["specific_dates", "days_of_week"]


def parse_hhmm(value: str) -> int:
    """
    Convert an "HH:MM" string into minutes after midnight.

    Args:
        value (str): A time such as "09:30" or "24:00".

    Returns:
        int: Minutes after midnight (0..1440).

    Raises:
        ValueError: If the string is not a valid HH:MM time.
    """
    hours_text, sep, minutes_text = value.strip().partition(":")
    if not sep:
        raise ValueError(f"expected HH:MM, got {value!r}")
    hours, minutes = int(hours_text), int(minutes_text)
    if not (0 <= hours <= 24 and 0 <= minutes < 60) or (hours == 24 and minutes):
        raise ValueError(f"time out of range: {value!r}")
    return hours * 60 + minutes


def format_hhmm(minutes: int) -> str:
    """
    Convert minutes after midnight into an "HH:MM" string.

    Args:
        minutes (int): Minutes after midnight (0..1440).

    Returns:
        str: The time formatted as "HH:MM".
    """
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class EventContext(BaseModel):
    """What the extension knows about the open When2meet event."""

    mode: EventMode = "days_of_week"
    event_tz: str | None = None
    screenshot_tz: str = "America/New_York"
    dates: list[dt.date] = Field(
        default_factory=list,
        description="Calendar dates covered by a specific-dates event, in screenshot_tz.",
    )
    ocr_engine: Literal["paddle", "tesseract"] = "paddle"
    ocr_languages: list[str] = Field(default_factory=lambda: ["en", "ch", "chinese_cht", "es"])
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    keyword_rules: list[str] = Field(default_factory=lambda: ["Plan out Tomorrow"])
    year: int | None = Field(default=None, description="Year for dates read from headers; defaults to dates/today.")


class ColumnInfo(BaseModel):
    """How one day column of one screenshot was identified (for the review screen)."""

    source_image: int
    column: int
    x0: int
    x1: int
    header_text: str
    day: Weekday
    date: dt.date | None = None
    quality: Literal["exact", "fuzzy", "date", "guessed"]


class ImageReport(BaseModel):
    """Per-screenshot diagnostics: axis fit, columns, and an optional annotated PNG."""

    source_image: int
    width: int
    height: int
    axis_residual_px: float | None = None
    axis_labels: int = 0
    grid: tuple[int, int] | None = None
    columns: list[ColumnInfo] = Field(default_factory=list)
    error: str | None = None
    debug_png: str | None = Field(default=None, description="Base64 PNG with the debug overlay.")


class ExtractResponse(BaseModel):
    """Result of POST /extract."""

    events: list[ExtractedEvent]
    images: list[ImageReport]


class ExtractedEvent(BaseModel):
    """One busy block found in a screenshot."""

    day: Weekday
    date_label: str | None = None
    date: dt.date | None = None
    start: str
    end: str
    title: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    cut_off: Literal["top", "bottom"] | None = None
    source_image: int = 0
    column: int = 0
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    weekday_guessed: bool = False
    minimum_height: bool = False
    reasons: list[str] = Field(default_factory=list)
    low_confidence: bool = False
    counts_as_busy: bool = True

    @field_validator("start", "end")
    @classmethod
    def _check_time(cls, value: str) -> str:
        """
        Validate that a time field is HH:MM.

        Args:
            value (str): The incoming time string.

        Returns:
            str: The normalised HH:MM string.
        """
        return format_hhmm(parse_hhmm(value))


class ReviewedEvent(BaseModel):
    """A busy event after the user reviewed it in the side panel."""

    day: Weekday
    date: dt.date | None = None
    start: str
    end: str
    title: str | None = None
    low_confidence: bool = False
    counts_as_busy: bool = True
    all_day: bool = False

    @field_validator("start", "end")
    @classmethod
    def _check_time(cls, value: str) -> str:
        """
        Validate that a time field is HH:MM.

        Args:
            value (str): The incoming time string.

        Returns:
            str: The normalised HH:MM string.
        """
        return format_hhmm(parse_hhmm(value))


class PlanSettings(BaseModel):
    """User settings that affect slot computation."""

    buffer_minutes: int = Field(default=0, ge=0, le=240)
    min_free_block_minutes: int = Field(default=15, ge=0, le=24 * 60)
    all_day_busy: bool = False


class FillPlanRequest(BaseModel):
    """Body of POST /fill-plan."""

    slots: list[int] = Field(description="When2meet TimeOfSlot timestamps (seconds).")
    mode: EventMode
    event_tz: str | None = None
    screenshot_tz: str = "America/New_York"
    slot_minutes: int = 15
    events: list[ReviewedEvent]
    settings: PlanSettings = Field(default_factory=PlanSettings)
    weekday_dates: dict[Weekday, dt.date] = Field(
        default_factory=dict,
        description="For specific-dates events where a weekday occurs more than once.",
    )
    reference_date: dt.date | None = Field(
        default=None,
        description="Days-of-week mode: anchor week used for timezone/DST conversion. Defaults to today.",
    )


class FillPlanResponse(BaseModel):
    """Result of POST /fill-plan."""

    available_slots: list[int]
    low_confidence_slots: list[int]
    ambiguous_weekdays: dict[Weekday, list[dt.date]] = Field(default_factory=dict)
    unmatched_events: list[int] = Field(
        default_factory=list, description="Indices of events that matched no day of the event."
    )


ExtractResponse.model_rebuild()
