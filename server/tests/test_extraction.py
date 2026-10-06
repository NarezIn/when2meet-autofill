"""Unit tests for the extraction pieces: axis fitting, pixel->time, snapping, weekdays, confidence, merging."""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from screenshot2meet.blocks import Block, detect_blocks
from screenshot2meet.confidence import score_event
from screenshot2meet.extractor import block_times, snap
from screenshot2meet.merge import merge_events
from screenshot2meet.models import EventContext, ExtractedEvent
from screenshot2meet.ocr import OcrWord
from screenshot2meet.timeaxis import AxisError, AxisLabel, axis_words, fit_axis, parse_time_label, snap_labels
from screenshot2meet.weekdays import find_date, parse_header

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "ios_calendar_mon_tue.png"
# Fixture geometry: 07:00 is at y=648 and an hour is 94.25 px.
PPH = 94.25


def label(minutes: int, y: float, score: float = 1.0) -> AxisLabel:
    """
    Make a snapped axis label.

    Args:
        minutes (int): Minutes after midnight.
        y (float): Pixel row.
        score (float): OCR confidence.

    Returns:
        AxisLabel: The label.
    """
    return AxisLabel(str(minutes), minutes, y, y, True, score)


def fixture_axis():
    """
    Axis fit matching the fixture (07:00..00:00).

    Returns:
        AxisFit: The fit.
    """
    return fit_axis([label(m, 648 + (m - 420) * PPH / 60) for m in range(420, 1441, 60)])


# --- time labels -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text,minutes",
    [
        ("09:00", 540), ("9:30", 570), ("00:00", 0), ("9 AM", 540), ("12 PM", 720), ("12 AM", 0),
        ("3:30 p.m.", 930), ("Noon", 720), ("上午9时", 540), ("下午3时", 900), ("9时", 540),
        ("中午12时", 720), ("晚上10点", 1320),
    ],
)
def test_parse_time_label(text: str, minutes: int) -> None:
    """Hour labels in 24h, 12h and Chinese forms parse to minutes."""
    assert parse_time_label(text) == minutes


@pytest.mark.parametrize("text", ["今天", "5", "廿五", "Comp…", "25:00"])
def test_parse_time_label_rejects(text: str) -> None:
    """Non-time text is rejected."""
    assert parse_time_label(text) is None


def test_axis_words_filters_and_wraps_midnight() -> None:
    """Only left-strip labels count; the current-time pill is dropped; 00:00 after 23:00 becomes 1440."""
    words = [
        OcrWord("22:00", 1, 40, 2036, 140, 2072),
        OcrWord("23:00", 1, 40, 2132, 140, 2168),
        OcrWord("23:21", 1, 25, 2170, 133, 2209),
        OcrWord("00:00", 1, 40, 2225, 140, 2260),
        OcrWord("10:00", 1, 600, 100, 700, 130),  # not in the axis strip
        OcrWord("今天", 1, 41, 2355, 153, 2421),
    ]
    assert [m for _, m in axis_words(words, 1179)] == [1320, 1380, 1440]


# --- axis fitting ------------------------------------------------------------------


def test_fit_axis_exact() -> None:
    """A clean axis fits with ~0 residual."""
    axis = fixture_axis()
    assert axis.residual_px < 0.01
    assert axis.px_per_hour == pytest.approx(PPH)
    assert axis.y_to_minutes(648 + 2.5 * PPH) == pytest.approx(570)


def test_fit_axis_rejects_outlier() -> None:
    """RANSAC ignores a label that is far off the line."""
    labels = [label(m, 648 + (m - 420) * PPH / 60) for m in range(420, 900, 60)]
    labels.append(label(600, 1500))  # wrong
    axis = fit_axis(labels)
    assert len(axis.inliers) == len(labels) - 1
    assert axis.residual_px < 0.01


def test_fit_axis_needs_three_labels() -> None:
    """Fewer than three labels is an error."""
    with pytest.raises(AxisError):
        fit_axis([label(420, 648), label(480, 742)])


def test_snap_labels_to_lines_and_offset() -> None:
    """Labels snap to the nearest line; an unsnapped label gets the median offset."""
    pairs = [(OcrWord("07:00", 1, 47, 623, 142, 659), 420), (OcrWord("08:00", 1, 43, 718, 141, 754), 480),
             (OcrWord("09:00", 1, 43, 812, 141, 849), 540)]
    snapped = snap_labels(pairs, lines=[648.0, 742.5], tolerance=19)
    assert [s.snapped for s in snapped] == [True, True, False]
    assert snapped[0].y == 648.0
    assert snapped[2].y == pytest.approx(830.5 + 6.75)


# --- pixel -> time and snapping -----------------------------------------------------


def test_snap() -> None:
    """Snapping rounds to the nearest 15 minutes."""
    assert snap(533.1) == 540
    assert snap(547.4) == 540
    assert snap(547.6) == 555


def make_block(y0: int, y1: int, inset: float | None) -> Block:
    """
    Block with given rows.

    Args:
        y0 (int): Top row.
        y1 (int): Bottom row.
        inset (float | None): Accent bar inset.

    Returns:
        Block: The block.
    """
    return Block(166, y0, 659, y1, (0, 0, 0), inset, 1.0)


def test_block_times_regular_and_stacked_gap() -> None:
    """Fixture rows map to the right times, accounting for the gap between stacked blocks."""
    axis = fixture_axis()
    assert block_times(make_block(889, 1021, 12), axis) == (570, 660, False)  # 09:30-11:00
    assert block_times(make_block(1030, 1139, 12), axis) == (660, 735, False)  # 11:00-12:15


def test_block_times_minimum_height() -> None:
    """Compact blocks keep the edge that lands on the grid and default to 15 minutes."""
    axis = fixture_axis()
    assert block_times(make_block(1336, 1369, 8), axis) == (855, 870, True)  # top-anchored 14:15
    assert block_times(make_block(826, 859, 8), axis) == (540, 555, True)  # bottom-anchored 09:00-09:15
    # A real 30-minute block (taller, normal accent inset) is not compact.
    assert block_times(make_block(1313, 1351, 12), axis) == (840, 870, False)


def test_detect_blocks_synthetic_dark() -> None:
    """Two side-by-side blocks, a red time line and a gray handle -> exactly two blocks."""
    img = np.zeros((600, 500, 3), np.uint8)
    img[100:200, 10:240] = (71, 55, 33)  # block 1 (dark blue)
    img[110:190, 20:29] = (250, 195, 120)  # its accent bar
    img[100:200, 244:490] = (69, 35, 60)  # block 2 (purple), 4 px gap
    img[110:190, 254:263] = (245, 120, 220)
    img[300:306, 0:500] = (60, 60, 230)  # red current-time line
    img[350:500, 470:500] = (76, 78, 85)  # gray side handle
    blocks = detect_blocks(img, (0, 500), (0, 600), PPH)
    assert [(b.x0, b.y0, b.x1, b.y1) for b in blocks] == [(10, 100, 239, 199), (244, 100, 489, 199)]


def test_detect_blocks_splits_touching_by_accent_bars() -> None:
    """Touching side-by-side blocks are split at the second accent bar."""
    img = np.zeros((300, 500, 3), np.uint8)
    img[100:200, 10:490] = (71, 55, 33)
    img[110:190, 20:29] = (250, 195, 120)
    img[110:190, 260:269] = (250, 195, 120)
    blocks = detect_blocks(img, (0, 500), (0, 300), PPH)
    assert len(blocks) == 2
    assert blocks[0].x1 < 260 <= blocks[1].x0 + 14


def test_detect_blocks_light_mode() -> None:
    """Pastel blocks on white are found."""
    img = np.full((300, 400, 3), 255, np.uint8)
    img[50:150, 10:390] = (250, 222, 191)  # pastel blue
    img[60:140, 20:29] = (255, 122, 0)
    img[200:201, :] = (225, 225, 225)  # grid line
    blocks = detect_blocks(img, (0, 400), (0, 300), PPH)
    assert [(b.y0, b.y1) for b in blocks] == [(50, 149)]


# --- weekdays ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,day,quality",
    [
        ("周一10月5日", "Mon", "exact"), ("周二 10月6日", "Tue", "exact"), ("星期三", "Wed", "exact"),
        ("週四 10/8", "Thu", "exact"), ("Mon 5", "Mon", "exact"), ("TUE", "Tue", "exact"),
        ("Wednesday", "Wed", "exact"), ("Fri.", "Fri", "exact"), ("lunes 5", "Mon", "exact"),
        ("mar. 6", "Tue", "exact"), ("miércoles", "Wed", "exact"), ("Mié 7", "Wed", "exact"),
        ("sáb", "Sat", "exact"), ("Mondav", "Mon", "fuzzy"), ("Thurs", "Thu", "fuzzy"),
        ("Saturdy", "Sat", "fuzzy"),
    ],
)
def test_parse_header_weekday(text: str, day: str, quality: str) -> None:
    """Weekday names in English, Chinese (simplified/traditional) and Spanish, exact and fuzzy."""
    parsed = parse_header(text)
    assert (parsed.weekday, parsed.quality) == (day, quality)


def test_parse_header_no_weekday() -> None:
    """Text without a weekday yields None."""
    assert parse_header("10月5日").weekday is None
    assert parse_header("Chikhany's office").weekday is None


@pytest.mark.parametrize(
    "text,expected", [("周一10月5日", (5, 10)), ("Mon 5", (5, None)), ("10/6", (6, 10)), ("5日", (5, None)), ("Mon", (None, None))]
)
def test_find_date(text: str, expected: tuple) -> None:
    """Day-of-month (and month) are read from headers."""
    assert find_date(text) == expected


# --- confidence --------------------------------------------------------------------


def test_confidence_clean_event_is_high() -> None:
    """A normal block on a clean axis scores ~1."""
    conf = score_event(axis_residual_px=0.3, axis_ocr_score=0.99, weekday_quality="exact",
                       minimum_height=False, edge_sharpness=1.0, cut_off=False)
    assert conf.score == pytest.approx(1.0)
    assert conf.reasons == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"minimum_height": True},
        {"cut_off": True},
        {"weekday_quality": "guessed"},
        {"axis_residual_px": 8.0},
        {"axis_ocr_score": 0.55},
    ],
)
def test_confidence_each_signal_drops_below_threshold(kwargs: dict) -> None:
    """Each strong warning sign alone pushes the score below the 0.7 threshold."""
    base = dict(axis_residual_px=0.3, axis_ocr_score=0.99, weekday_quality="exact",
                minimum_height=False, edge_sharpness=1.0, cut_off=False)
    conf = score_event(**{**base, **kwargs})
    assert conf.score < 0.7
    assert conf.reasons


def test_confidence_mild_signals_stay_above() -> None:
    """Fuzzy weekday or slightly blurry edges alone don't flag the event."""
    conf = score_event(axis_residual_px=2.0, axis_ocr_score=0.92, weekday_quality="fuzzy",
                       minimum_height=False, edge_sharpness=0.6, cut_off=False)
    assert 0.7 <= conf.score < 1


# --- merging -----------------------------------------------------------------------


def event(start: str, end: str, image: int, **kw) -> ExtractedEvent:
    """
    Make an extracted Monday event.

    Args:
        start (str): HH:MM.
        end (str): HH:MM.
        image (int): Source image index.
        **kw: Other ExtractedEvent fields.

    Returns:
        ExtractedEvent: The event.
    """
    return ExtractedEvent(day="Mon", start=start, end=end, confidence=kw.pop("confidence", 1.0), source_image=image, **kw)


def test_merge_prefers_complete_over_cut_off() -> None:
    """A complete copy wins over a cut-off copy of the same event."""
    out = merge_events([event("09:00", "10:00", 0, cut_off="bottom", confidence=0.6), event("09:00", "11:00", 1)], EventContext())
    assert [(e.start, e.end, e.cut_off) for e in out] == [("09:00", "11:00", None)]


def test_merge_joins_two_cut_off_halves() -> None:
    """A bottom-cut copy and a top-cut copy combine into the full event."""
    out = merge_events(
        [event("09:00", "10:00", 0, cut_off="bottom", title="Lab"), event("09:30", "11:00", 1, cut_off="top", title="Lab")],
        EventContext(),
    )
    assert [(e.start, e.end, e.cut_off) for e in out] == [("09:00", "11:00", None)]


def test_merge_disagreement_lowers_confidence() -> None:
    """Copies more than 15 minutes apart are kept once and flagged."""
    out = merge_events([event("09:00", "10:00", 0), event("09:00", "10:30", 1)], EventContext())
    assert len(out) == 1
    assert out[0].low_confidence
    assert "disagree" in out[0].reasons[-1]


def test_merge_keeps_same_image_side_by_side() -> None:
    """Two identical-time events in one screenshot are both kept."""
    out = merge_events([event("11:00", "12:15", 0, bbox=(1, 0, 2, 0)), event("11:00", "12:15", 0, bbox=(3, 0, 4, 0))], EventContext())
    assert len(out) == 2


def test_merge_keyword_rules_set_not_busy() -> None:
    """Keyword rules default matching events to 'doesn't count as busy'."""
    out = merge_events([event("22:30", "22:45", 0, title="Plan out Tomorrow"), event("09:00", "10:00", 0, title="Class")],
                       EventContext(keyword_rules=["plan out tomorrow"]))
    assert {e.title: e.counts_as_busy for e in out} == {"Plan out Tomorrow": False, "Class": True}


def test_merge_different_dates_not_merged() -> None:
    """Same weekday, different dates (two weeks) stay separate."""
    a = event("09:00", "10:00", 0, date=dt.date(2026, 10, 5))
    b = event("09:00", "10:00", 1, date=dt.date(2026, 10, 12))
    assert len(merge_events([a, b], EventContext())) == 2


# --- end to end on the fixture (needs PaddleOCR models) ---------------------------


@pytest.mark.skipif(importlib.util.find_spec("paddleocr") is None, reason="PaddleOCR not installed")
def test_fixture_end_to_end() -> None:
    """The fixture yields the 12 expected events with the right low-confidence flags."""
    from screenshot2meet.cli import load_image
    from screenshot2meet.extractor import CvOcrExtractor

    events = CvOcrExtractor().extract([load_image(FIXTURE)], EventContext())
    got = sorted((e.day, e.start, e.end, e.low_confidence) for e in events)
    assert got == sorted([
        ("Mon", "09:00", "09:15", True), ("Mon", "09:30", "11:00", False), ("Mon", "11:00", "12:15", False),
        ("Mon", "11:00", "12:15", False), ("Mon", "14:00", "15:00", False), ("Mon", "14:15", "14:30", True),
        ("Mon", "22:30", "22:45", True), ("Tue", "09:00", "09:15", True), ("Tue", "09:30", "10:45", False),
        ("Tue", "14:00", "15:15", False), ("Tue", "15:30", "16:45", False), ("Tue", "22:30", "22:45", True),
    ])
    plan_out = [e for e in events if e.title == "Plan out Tomorrow"]
    assert plan_out and not any(e.counts_as_busy for e in plan_out)
