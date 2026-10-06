"""
Time axis: parse hour labels, find horizontal grid lines, and fit y -> minutes.

Each label's vertical centre is snapped to the nearest grid line (labels are
drawn centred on their hour line). Then a RANSAC line fit (y = a + b * minutes)
rejects outliers such as the red current-time pill.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

import numpy as np

from .ocr import OcrWord

_HHMM = re.compile(r"^(\d{1,2})[:：.](\d{2})$")
_12H = re.compile(r"^(\d{1,2})(?:[:：.](\d{2}))?([ap])m?$")
_ZH = re.compile(r"^(上午|下午|中午|凌晨|早上|晚上|傍晚)?(\d{1,2})(?:[时時点點:：](\d{2})?)?分?$")
_NOON = {"noon", "中午", "正午", "mediodia"}
_PM_WORDS = {"下午", "晚上", "傍晚"}


def parse_time_label(text: str) -> int | None:
    """
    Parse an hour label into minutes after midnight.

    Accepts "09:00", "9 AM", "9:30 p.m.", "Noon", "上午9时", "下午3时", "9时".

    Args:
        text (str): Raw OCR text.

    Returns:
        int | None: Minutes after midnight (0..1439), or None if it isn't a time label.
    """
    compact = re.sub(r"[\s.]", "", text.strip().lower())
    if compact in _NOON:
        return 12 * 60
    if m := _HHMM.match(compact):
        hours, minutes = int(m.group(1)), int(m.group(2))
        return hours * 60 + minutes if hours <= 24 and minutes < 60 else None
    if m := _12H.match(compact):
        hours, minutes = int(m.group(1)), int(m.group(2) or 0)
        if not 1 <= hours <= 12 or minutes >= 60:
            return None
        hours = hours % 12 + (12 if m.group(3) == "p" else 0)
        return hours * 60 + minutes
    if m := _ZH.match(compact):
        prefix, hours, minutes = m.group(1), int(m.group(2)), int(m.group(3) or 0)
        if prefix is None and m.group(0).isdigit():
            return None  # a bare number is not a time label
        if hours > 24 or minutes >= 60:
            return None
        if prefix in _PM_WORDS and hours < 12:
            hours += 12
        if prefix == "凌晨" and hours == 12:
            hours = 0
        return (hours % 24) * 60 + minutes
    return None


@dataclass(frozen=True)
class AxisLabel:
    """An hour label found on the axis."""

    text: str
    minutes: int
    y_raw: float
    y: float
    snapped: bool
    score: float


@dataclass
class AxisFit:
    """Linear map between pixel rows and minutes after midnight: y = a + b * minutes."""

    a: float
    b: float
    residual_px: float
    labels: list[AxisLabel]
    inliers: list[AxisLabel] = field(default_factory=list)

    def y_to_minutes(self, y: float) -> float:
        """
        Convert a pixel row to minutes after midnight.

        Args:
            y (float): Pixel row.

        Returns:
            float: Minutes (may exceed 1440 past midnight).
        """
        return (y - self.a) / self.b

    def minutes_to_y(self, minutes: float) -> float:
        """
        Convert minutes after midnight to a pixel row.

        Args:
            minutes (float): Minutes after midnight.

        Returns:
            float: Pixel row.
        """
        return self.a + self.b * minutes

    @property
    def px_per_hour(self) -> float:
        """
        Pixels per hour.

        Returns:
            float: 60 * b.
        """
        return 60 * self.b

    @property
    def mean_ocr_score(self) -> float:
        """
        Mean OCR confidence of the labels used in the fit.

        Returns:
            float: 0..1.
        """
        used = self.inliers or self.labels
        return float(np.mean([label.score for label in used])) if used else 0.0


class AxisError(RuntimeError):
    """Raised when the time axis can't be read."""


def axis_words(words: list[OcrWord], image_width: int, fraction: float = 0.15) -> list[tuple[OcrWord, int]]:
    """
    Pick OCR words in the left strip that parse as time labels.

    Minutes are made monotonic top to bottom (00:00 after 23:00 becomes 1440).

    Args:
        words (list[OcrWord]): All OCR words of the image.
        image_width (int): Image width in pixels.
        fraction (float): Width fraction of the axis strip.

    Returns:
        list[tuple[OcrWord, int]]: (word, minutes) sorted by y.
    """
    found = []
    for word in words:
        if word.cx > image_width * fraction:
            continue
        minutes = parse_time_label(word.text)
        # Hour labels sit on quarter hours; this drops the current-time pill (e.g. "23:21").
        if minutes is not None and minutes % 15 == 0:
            found.append((word, minutes))
    found.sort(key=lambda pair: pair[0].cy)
    out: list[tuple[OcrWord, int]] = []
    offset = 0
    for word, minutes in found:
        if out and minutes + offset < out[-1][1] - 120:
            offset += 1440
        out.append((word, minutes + offset))
    return out


def line_like_mask(image: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """
    Pixels that look like thin grid lines: gray (unsaturated) and a little off the background.

    Args:
        image (np.ndarray): BGR image.
        bg (np.ndarray): Background BGR colour.

    Returns:
        np.ndarray: Boolean mask (H, W).
    """
    img = image.astype(np.int16)
    diff = np.abs(img - bg.astype(np.int16)).max(axis=2)
    saturation = img.max(axis=2) - img.min(axis=2)
    return (diff >= 4) & (diff <= 110) & (saturation <= 25)


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """
    Find runs of consecutive True values.

    Args:
        flags (np.ndarray): 1-D boolean array.

    Returns:
        list[tuple[int, int]]: (start, end) inclusive index pairs.
    """
    padded = np.concatenate([[False], flags, [False]]).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return [(int(s), int(e) - 1) for s, e in zip(edges[::2], edges[1::2])]


def detect_horizontal_lines(
    image: np.ndarray,
    bg: np.ndarray,
    x_ranges: list[tuple[int, int]],
    margin_ranges: list[tuple[int, int]],
    y_range: tuple[int, int],
) -> list[float]:
    """
    Find thin horizontal grid lines.

    A row counts as a line if most pixels in the column margins (never covered
    by event blocks) or a good share of the whole grid width are line-like.

    Args:
        image (np.ndarray): BGR image.
        bg (np.ndarray): Background colour.
        x_ranges (list[tuple[int, int]]): Grid column x ranges [x0, x1).
        margin_ranges (list[tuple[int, int]]): Narrow strips next to column separators [x0, x1).
        y_range (tuple[int, int]): Rows to search [y0, y1).

    Returns:
        list[float]: Line centre rows.
    """
    y0, y1 = y_range
    mask = line_like_mask(image[y0:y1], bg)
    full = np.concatenate([mask[:, a:b] for a, b in x_ranges], axis=1).mean(axis=1) if x_ranges else 0
    margins = np.concatenate([mask[:, a:b] for a, b in margin_ranges], axis=1).mean(axis=1) if margin_ranges else 0
    rows = (np.asarray(margins) >= 0.6) | (np.asarray(full) >= 0.5)
    return [y0 + (s + e) / 2 for s, e in _runs(rows) if e - s <= 3]


def detect_vertical_lines(image: np.ndarray, bg: np.ndarray, y_range: tuple[int, int]) -> list[float]:
    """
    Find thin vertical separators (day column boundaries).

    Args:
        image (np.ndarray): BGR image.
        bg (np.ndarray): Background colour.
        y_range (tuple[int, int]): Rows to look at [y0, y1).

    Returns:
        list[float]: Separator centre columns.
    """
    y0, y1 = y_range
    mask = line_like_mask(image[y0:y1], bg)
    cols = mask.mean(axis=0) >= 0.5
    return [(s + e) / 2 for s, e in _runs(cols) if e - s <= 3]


def snap_labels(pairs: list[tuple[OcrWord, int]], lines: list[float], tolerance: float) -> list[AxisLabel]:
    """
    Snap each label's centre to the nearest grid line.

    Labels with no line within `tolerance` keep their centre plus the median
    centre-to-line offset of the snapped labels.

    Args:
        pairs (list[tuple[OcrWord, int]]): (word, minutes) from axis_words.
        lines (list[float]): Horizontal line rows.
        tolerance (float): Maximum snapping distance in pixels.

    Returns:
        list[AxisLabel]: Labels with snapped y.
    """
    snapped: list[AxisLabel] = []
    offsets: list[float] = []
    for word, minutes in pairs:
        nearest = min(lines, key=lambda y: abs(y - word.cy), default=None)
        if nearest is not None and abs(nearest - word.cy) <= tolerance:
            offsets.append(nearest - word.cy)
            snapped.append(AxisLabel(word.text, minutes, word.cy, nearest, True, word.score))
        else:
            snapped.append(AxisLabel(word.text, minutes, word.cy, word.cy, False, word.score))
    shift = float(np.median(offsets)) if offsets else 0.0
    return [
        label if label.snapped else AxisLabel(label.text, label.minutes, label.y_raw, label.y_raw + shift, False, label.score)
        for label in snapped
    ]


def fit_axis(labels: list[AxisLabel], tolerance_px: float = 3.0) -> AxisFit:
    """
    Robustly fit y = a + b * minutes with RANSAC over label pairs, then least squares on inliers.

    Args:
        labels (list[AxisLabel]): Snapped axis labels.
        tolerance_px (float): Inlier distance in pixels.

    Returns:
        AxisFit: The fitted map with its RMS residual over inliers.

    Raises:
        AxisError: With fewer than 3 consistent labels.
    """
    if len(labels) < 3:
        raise AxisError(f"need at least 3 hour labels on the time axis, found {len(labels)}")
    minutes = np.array([label.minutes for label in labels], dtype=float)
    ys = np.array([label.y for label in labels], dtype=float)

    best: tuple[int, float, np.ndarray] | None = None
    for i, j in itertools.combinations(range(len(labels)), 2):
        if minutes[i] == minutes[j]:
            continue
        b = (ys[j] - ys[i]) / (minutes[j] - minutes[i])
        if b <= 0:
            continue
        a = ys[i] - b * minutes[i]
        errors = np.abs(ys - (a + b * minutes))
        inliers = errors <= tolerance_px
        count, spread = int(inliers.sum()), float(errors[inliers].mean())
        if best is None or count > best[0] or (count == best[0] and spread < best[1]):
            best = (count, spread, inliers)
    if best is None or best[0] < 3:
        raise AxisError("the hour labels don't line up; couldn't fit a time axis")

    inliers = best[2]
    b, a = np.polyfit(minutes[inliers], ys[inliers], 1)
    residual = float(np.sqrt(np.mean((ys[inliers] - (a + b * minutes[inliers])) ** 2)))
    return AxisFit(
        a=float(a),
        b=float(b),
        residual_px=residual,
        labels=labels,
        inliers=[label for label, keep in zip(labels, inliers) if keep],
    )
