"""
Calendar-screenshot extraction behind a small Extractor protocol.

CvOcrExtractor handles iOS Calendar day / multi-day views (dark and light):
OCR -> time axis fit -> day columns + headers -> blocks -> times + confidence.
A vision-model extractor can be added later by implementing Extractor.
"""

from __future__ import annotations

import base64
import datetime as dt
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from .blocks import Block, background_color, detect_blocks
from .confidence import score_event
from .debug import draw_debug
from .merge import merge_events
from .models import (
    WEEKDAYS,
    ColumnInfo,
    EventContext,
    ExtractedEvent,
    ImageReport,
    Weekday,
    format_hhmm,
)
from .ocr import OcrWord, get_engine
from .timeaxis import (
    AxisError,
    AxisFit,
    axis_words,
    detect_horizontal_lines,
    detect_vertical_lines,
    fit_axis,
    snap_labels,
)
from .weekdays import MatchQuality, parse_header

# Calibrated on fixtures/ios_calendar_mon_tue.png: iOS insets each block from its
# time range by a few pixels, leaving a visible gap between stacked blocks.
TOP_GAP_MIN = 3.3
BOTTOM_GAP_MIN = 2.4
SNAP_MIN = 15
# Compact (minimum-height) blocks: shorter than this fraction of an hour...
COMPACT_MAX_HEIGHT = 0.42
# ...with the accent bar inset less than this fraction of an hour from the top.
COMPACT_MAX_INSET = 0.106


class Extractor(Protocol):
    """Turns calendar screenshots into busy events."""

    def extract(self, images: list[np.ndarray], ctx: EventContext) -> list[ExtractedEvent]:
        """
        Extract busy events from screenshots.

        Args:
            images (list[np.ndarray]): BGR screenshots.
            ctx (EventContext): When2meet event context and settings.

        Returns:
            list[ExtractedEvent]: Events (merged across screenshots).
        """
        ...


@dataclass
class Column:
    """One day column in one screenshot."""

    index: int
    x0: int
    x1: int
    header: str = ""
    day: Weekday = "Mon"
    date: dt.date | None = None
    quality: MatchQuality = "guessed"
    blocks: list[Block] = field(default_factory=list)


@dataclass
class ImageAnalysis:
    """Everything found in one screenshot (used for events, reports and the debug overlay)."""

    index: int
    image: np.ndarray
    words: list[OcrWord] = field(default_factory=list)
    axis: AxisFit | None = None
    grid: tuple[int, int] | None = None
    columns: list[Column] = field(default_factory=list)
    events: list[ExtractedEvent] = field(default_factory=list)
    error: str | None = None


def snap(minutes: float, step: int = SNAP_MIN) -> int:
    """
    Round minutes to the nearest step.

    Args:
        minutes (float): Minutes after midnight.
        step (int): Grid in minutes.

    Returns:
        int: Snapped minutes.
    """
    return int(round(minutes / step) * step)


def block_times(block: Block, axis: AxisFit) -> tuple[int, int, bool]:
    """
    Convert a block's pixel edges to snapped start/end minutes.

    Compact (minimum-height) blocks only tell us one edge. iOS anchors them at
    the start, except when that would crowd the next event, in which case the
    end is anchored instead. We keep whichever edge lands closer to the
    15-minute grid (start by default) and assume a 15-minute length.

    Args:
        block (Block): The block.
        axis (AxisFit): The image's time axis.

    Returns:
        tuple[int, int, bool]: (start, end, drawn_at_minimum_height).
    """
    pph = axis.px_per_hour
    start_raw = axis.y_to_minutes(block.y0) - TOP_GAP_MIN
    end_raw = axis.y_to_minutes(block.y1 + 1) + BOTTOM_GAP_MIN
    compact = block.height < COMPACT_MAX_HEIGHT * pph and (
        block.accent_inset is None or block.accent_inset < COMPACT_MAX_INSET * pph
    )
    if compact:
        top_error = abs(start_raw - snap(start_raw))
        bottom_error = abs(end_raw - snap(end_raw))
        start = snap(end_raw) - SNAP_MIN if bottom_error + 2 < top_error else snap(start_raw)
        return start, start + SNAP_MIN, True
    start, end = snap(start_raw), snap(end_raw)
    return start, max(end, start + SNAP_MIN), False


def find_columns(image: np.ndarray, bg: np.ndarray, axis_right: int, y_range: tuple[int, int]) -> list[Column]:
    """
    Split the grid into day columns at the vertical separator lines.

    Args:
        image (np.ndarray): BGR screenshot.
        bg (np.ndarray): Grid background colour.
        axis_right (int): Right edge of the time labels.
        y_range (tuple[int, int]): Rows to search for separators.

    Returns:
        list[Column]: Columns left to right (without headers yet).
    """
    width = image.shape[1]
    seps = [x for x in detect_vertical_lines(image, bg, y_range) if x > axis_right]
    bounds = [int(round(x)) for x in seps]
    if not bounds or bounds[0] > 0.3 * width:
        bounds.insert(0, axis_right + 4)
    if width - bounds[-1] > 0.15 * width:
        bounds.append(width)
    columns = []
    for left, right in zip(bounds, bounds[1:]):
        if right - left >= 0.08 * width:
            columns.append(Column(index=len(columns), x0=left + 2, x1=right - 1))
    return columns


def grid_bounds(
    image: np.ndarray, bg: np.ndarray, axis: AxisFit, lines: list[float], header_bottom: int
) -> tuple[int, int]:
    """
    Vertical bounds of the visible time grid, from the first and last hour labels.

    The top extends up to the separator under the header (or the all-day band)
    if one is within 3/4 hour; the bottom extends down to the tab bar.

    Args:
        image (np.ndarray): BGR screenshot.
        bg (np.ndarray): Grid background colour.
        axis (AxisFit): Time axis.
        lines (list[float]): Horizontal grid line rows.
        header_bottom (int): Bottom of the lowest header text.

    Returns:
        tuple[int, int]: [y0, y1) of the grid.
    """
    height = image.shape[0]
    pph = axis.px_per_hour
    first = axis.minutes_to_y(min(label.minutes for label in axis.inliers))
    last = axis.minutes_to_y(max(label.minutes for label in axis.inliers))
    above = [y for y in lines if first - 0.75 * pph <= y < first - 0.1 * pph]
    y0 = int(max(above) + 2) if above else int(max(header_bottom + 2, first - 0.5 * pph))

    y1 = int(min(height, last + 0.75 * pph))
    diff = np.abs(image.astype(np.int16) - bg.astype(np.int16)).max(axis=2)
    for y in range(int(last) + 2, y1):
        if (diff[y] > 10).mean() >= 0.9:
            y1 = y
            break
    return y0, y1


def read_headers(columns: list[Column], words: list[OcrWord], y0: int, ctx: EventContext) -> None:
    """
    Read each column's header: the text row closest above the grid that parses.

    Args:
        columns (list[Column]): Columns to fill in (header, day, date, quality).
        words (list[OcrWord]): OCR words of the image.
        y0 (int): Top of the grid.
        ctx (EventContext): Supplies the event's dates and year for date headers.

    Returns:
        None
    """
    for column in columns:
        candidates = [w for w in words if w.y1 <= y0 + 4 and column.x0 - 10 <= w.cx <= column.x1 + 10]
        rows: list[list[OcrWord]] = []
        for word in sorted(candidates, key=lambda w: -w.cy):
            if rows and abs(rows[-1][0].cy - word.cy) < 15:
                rows[-1].append(word)
            else:
                rows.append([word])
        for row in rows:
            text = " ".join(w.text for w in sorted(row, key=lambda w: w.x0))
            parsed = parse_header(text)
            if parsed.weekday or parsed.day_of_month:
                column.header = text
                if parsed.weekday:
                    column.day = parsed.weekday
                    column.quality = parsed.quality or "exact"
                column.date = header_date(parsed.day_of_month, parsed.month, ctx)
                break


def header_date(day_of_month: int | None, month: int | None, ctx: EventContext) -> dt.date | None:
    """
    Turn a header's day-of-month (and month) into a date, using the event's dates when known.

    Args:
        day_of_month (int | None): Day of month read from the header.
        month (int | None): Month read from the header, if any.
        ctx (EventContext): The event's dates (specific-dates mode) and year.

    Returns:
        datetime.date | None: The date, or None if it can't be pinned down.
    """
    if day_of_month is None:
        return None
    matches = [d for d in ctx.dates if d.day == day_of_month and (month is None or d.month == month)]
    if len(matches) == 1:
        return matches[0]
    if month is not None:
        try:
            return dt.date(ctx.year or (ctx.dates[0].year if ctx.dates else dt.date.today().year), month, day_of_month)
        except ValueError:
            return None
    return None


def resolve_weekdays(columns: list[Column]) -> None:
    """
    Fill in weekdays that the header didn't give: from the date, then from neighbours.

    Args:
        columns (list[Column]): Columns after read_headers.

    Returns:
        None
    """
    for column in columns:
        if column.header and column.quality in ("exact", "fuzzy"):
            if column.date and WEEKDAYS[column.date.weekday()] != column.day:
                column.date = None  # the date digits disagree with the weekday name; trust the name
            continue
        if column.date:
            column.day = WEEKDAYS[column.date.weekday()]
            column.quality = "date"
    known = [c for c in columns if c.quality != "guessed"]
    for column in columns:
        if column.quality != "guessed":
            continue
        if known:
            ref = min(known, key=lambda c: abs(c.index - column.index))
            column.day = WEEKDAYS[(WEEKDAYS.index(ref.day) + column.index - ref.index) % 7]
            if ref.date:
                column.date = ref.date + dt.timedelta(days=column.index - ref.index)
        else:
            column.day = WEEKDAYS[column.index % 7]


class CvOcrExtractor:
    """OpenCV + OCR extractor for iOS Calendar day / multi-day screenshots."""

    def extract(self, images: list[np.ndarray], ctx: EventContext) -> list[ExtractedEvent]:
        """
        Extract and merge busy events from screenshots.

        Args:
            images (list[np.ndarray]): BGR screenshots.
            ctx (EventContext): Event context and settings.

        Returns:
            list[ExtractedEvent]: Merged events.
        """
        return self.extract_with_reports(images, ctx)[0]

    def extract_with_reports(
        self, images: list[np.ndarray], ctx: EventContext, debug: bool = False
    ) -> tuple[list[ExtractedEvent], list[ImageReport], list[ImageAnalysis]]:
        """
        Extract events and also return per-image reports (and debug overlays if asked).

        Args:
            images (list[np.ndarray]): BGR screenshots.
            ctx (EventContext): Event context and settings.
            debug (bool): Whether to render the annotated PNG for each image.

        Returns:
            tuple: (merged events, per-image reports, raw per-image analyses).
        """
        engine = get_engine(ctx.ocr_engine, tuple(ctx.ocr_languages))
        analyses = [self.analyze_image(i, image, ctx, engine) for i, image in enumerate(images)]
        events = merge_events([e for a in analyses for e in a.events], ctx)
        reports = []
        for analysis in analyses:
            report = self.report(analysis)
            if debug:
                png = draw_debug(analysis, ctx.confidence_threshold)
                report.debug_png = base64.b64encode(png).decode("ascii")
            reports.append(report)
        return events, reports, analyses

    def analyze_image(self, index: int, image: np.ndarray, ctx: EventContext, engine: object) -> ImageAnalysis:
        """
        Run the full pipeline on one screenshot.

        Args:
            index (int): Position of the image in the upload.
            image (np.ndarray): BGR screenshot.
            ctx (EventContext): Event context and settings.
            engine (object): OCR engine (has read() and read_extra()).

        Returns:
            ImageAnalysis: Findings for this image; `error` is set if the time axis couldn't be read.
        """
        result = ImageAnalysis(index=index, image=image)
        height, width = image.shape[:2]
        result.words = engine.read(image)  # type: ignore[attr-defined]
        pairs = axis_words(result.words, width)
        if len(pairs) < 3:
            result.error = f"found {len(pairs)} time labels on the left; need at least 3"
            return result

        top, bottom = int(pairs[0][0].cy), int(pairs[-1][0].cy)
        axis_right = max(w.x1 for w, _ in pairs)
        bg = background_color(image[top:bottom, axis_right:])
        columns = find_columns(image, bg, axis_right, (top, bottom))
        if not columns:
            result.error = "couldn't find any day columns"
            return result

        x_ranges = [(c.x0, c.x1) for c in columns]
        edges = sorted({c.x0 - 2 for c in columns} | {c.x1 + 1 for c in columns})
        margins = [(max(0, x - 7), x) for x in edges if x > axis_right + 8] + [(x + 1, min(width, x + 8)) for x in edges]
        lines = detect_horizontal_lines(image, bg, x_ranges, margins, (0, height))

        approx_pph = np.median(
            [60 * (b[0].cy - a[0].cy) / (b[1] - a[1]) for a, b in zip(pairs, pairs[1:]) if b[1] > a[1]]
        )
        labels = snap_labels(pairs, lines, tolerance=0.2 * float(approx_pph))
        try:
            axis = fit_axis(labels)
        except AxisError as exc:
            result.error = str(exc)
            return result
        result.axis = axis

        header_words = [w for w in result.words if w.y1 < axis.minutes_to_y(axis.inliers[0].minutes) - 0.3 * axis.px_per_hour]
        header_bottom = max((w.y1 for w in header_words if w.cx > axis_right), default=0)
        y0, y1 = grid_bounds(image, bg, axis, lines, header_bottom)
        result.grid = (y0, y1)

        read_headers(columns, result.words, y0, ctx)
        if any(not c.header for c in columns) and hasattr(engine, "read_extra"):
            extra = engine.read_extra(image[:y0])  # type: ignore[attr-defined]
            if extra:
                read_headers([c for c in columns if not c.header], extra, y0, ctx)
        resolve_weekdays(columns)
        result.columns = columns

        for column in columns:
            column.blocks = detect_blocks(image, (column.x0, column.x1), (y0, y1), axis.px_per_hour)
            for block in column.blocks:
                result.events.append(self.block_event(block, column, axis, (y0, y1), result.words, index, ctx))
        return result

    @staticmethod
    def block_event(
        block: Block,
        column: Column,
        axis: AxisFit,
        grid: tuple[int, int],
        words: list[OcrWord],
        index: int,
        ctx: EventContext,
    ) -> ExtractedEvent:
        """
        Build an ExtractedEvent (times, title, cut-off, confidence) from a block.

        Args:
            block (Block): The block.
            column (Column): Its day column.
            axis (AxisFit): The image's time axis.
            grid (tuple[int, int]): Grid bounds [y0, y1).
            words (list[OcrWord]): OCR words of the image (for the title).
            index (int): Source image index.
            ctx (EventContext): Settings (confidence threshold).

        Returns:
            ExtractedEvent: The event.
        """
        start, end, minimum = block_times(block, axis)
        cut_off = None
        if block.y0 <= grid[0] + 2:
            cut_off = "top"
            start = snap(axis.y_to_minutes(grid[0]))
        elif block.y1 >= grid[1] - 3:
            cut_off = "bottom"
            end = snap(axis.y_to_minutes(grid[1]))
        start = max(0, min(start, 1440 - SNAP_MIN))
        end = max(start + SNAP_MIN, min(end, 1440))

        inside = [w for w in words if block.x0 <= w.cx <= block.x1 and block.y0 <= w.cy <= block.y1]
        title = " ".join(w.text for w in sorted(inside, key=lambda w: (round(w.y0 / 10), w.x0))) or None

        conf = score_event(
            axis_residual_px=axis.residual_px,
            axis_ocr_score=axis.mean_ocr_score,
            weekday_quality=column.quality,
            minimum_height=minimum,
            edge_sharpness=block.edge_sharpness,
            cut_off=cut_off is not None,
        )
        return ExtractedEvent(
            day=column.day,
            date_label=column.header or None,
            date=column.date,
            start=format_hhmm(start),
            end=format_hhmm(end),
            title=title,
            confidence=round(conf.score, 3),
            cut_off=cut_off,
            source_image=index,
            column=column.index,
            bbox=(block.x0, block.y0, block.x1, block.y1),
            weekday_guessed=column.quality == "guessed",
            minimum_height=minimum,
            reasons=conf.reasons,
            low_confidence=conf.score < ctx.confidence_threshold,
        )

    @staticmethod
    def report(analysis: ImageAnalysis) -> ImageReport:
        """
        Summarise an analysis for the API.

        Args:
            analysis (ImageAnalysis): Findings for one image.

        Returns:
            ImageReport: The report (without the debug PNG).
        """
        height, width = analysis.image.shape[:2]
        return ImageReport(
            source_image=analysis.index,
            width=width,
            height=height,
            axis_residual_px=round(analysis.axis.residual_px, 2) if analysis.axis else None,
            axis_labels=len(analysis.axis.inliers) if analysis.axis else 0,
            grid=analysis.grid,
            columns=[
                ColumnInfo(
                    source_image=analysis.index,
                    column=c.index,
                    x0=c.x0,
                    x1=c.x1,
                    header_text=c.header,
                    day=c.day,
                    date=c.date,
                    quality=c.quality,
                )
                for c in analysis.columns
            ],
            error=analysis.error,
        )
