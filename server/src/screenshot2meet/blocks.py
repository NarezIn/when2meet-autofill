"""
Find event blocks inside one day column.

Per column: take the most common colour as background, mask pixels that
differ from it (ignoring gray grid lines), close small holes, and take
connected components. iOS draws a saturated accent bar on the left of every
event; components holding more than one bar are split at the bars, which
separates touching side-by-side or stacked events.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .timeaxis import line_like_mask

DIFF_THRESHOLD = 28


@dataclass
class Block:
    """One event block in image coordinates (inclusive pixel bounds)."""

    x0: int
    y0: int
    x1: int
    y1: int
    color: tuple[int, int, int]
    accent_inset: float | None
    edge_sharpness: float

    @property
    def height(self) -> int:
        """
        Height in pixels.

        Returns:
            int: y1 - y0 + 1.
        """
        return self.y1 - self.y0 + 1

    @property
    def width(self) -> int:
        """
        Width in pixels.

        Returns:
            int: x1 - x0 + 1.
        """
        return self.x1 - self.x0 + 1


def background_color(region: np.ndarray) -> np.ndarray:
    """
    Most common colour of a region (colours quantised to 8 levels per channel).

    Args:
        region (np.ndarray): BGR pixels.

    Returns:
        np.ndarray: BGR colour (uint8, shape (3,)).
    """
    quant = (region.reshape(-1, 3) // 32).astype(np.int32)
    keys = quant[:, 0] * 64 + quant[:, 1] * 8 + quant[:, 2]
    mode = np.bincount(keys).argmax()
    members = region.reshape(-1, 3)[keys == mode]
    return np.median(members, axis=0).astype(np.uint8)


def accent_mask(region: np.ndarray) -> np.ndarray:
    """
    Pixels of iOS's bright, saturated accent bars.

    Args:
        region (np.ndarray): BGR pixels.

    Returns:
        np.ndarray: Boolean mask.
    """
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    return (hsv[..., 2] > 150) & (hsv[..., 1] > 70)


def _accent_bars(acc: np.ndarray, min_height: int) -> list[tuple[int, int, int, int]]:
    """
    Tall, narrow accent bars in an accent mask.

    Bars are solid (>= 80% filled), 5-14 px wide and at least 45% of the box
    height. Text glyphs fail at least one test: "l" is too thin, "T" too wide
    for its height, and CJK characters or "g" are hollow (fill <= ~65%).

    Args:
        acc (np.ndarray): Boolean accent mask of one component's bounding box.
        min_height (int): Minimum bar height in pixels.

    Returns:
        list[tuple[int, int, int, int]]: Bars as (x0, y0, x1, y1), inclusive, in mask coordinates.
    """
    count, _, stats, _ = cv2.connectedComponentsWithStats(acc.astype(np.uint8), connectivity=8)
    min_height = max(min_height, int(0.45 * acc.shape[0]))
    bars = []
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        if 5 <= w <= 14 and h >= min_height and h >= 2 * w and area >= 0.8 * w * h:
            bars.append((int(x), int(y), int(x + w - 1), int(y + h - 1)))
    return bars


def _edge_sharpness(diff: np.ndarray, x0: int, x1: int, y: int, inward: int) -> float:
    """
    How abrupt a horizontal edge is: the largest one-row jump over the total jump across 6 rows.

    Args:
        diff (np.ndarray): Per-pixel difference from the background (column region).
        x0 (int): Left of the block (region coordinates).
        x1 (int): Right of the block (inclusive).
        y (int): Edge row (first/last row of the block).
        inward (int): +1 for a top edge, -1 for a bottom edge.

    Returns:
        float: 0..1; crisp screenshots give ~1.
    """
    width = x1 - x0 + 1
    a, b = x0 + width // 5, x1 - width // 5
    rows = [y + inward * k for k in range(-3, 3)]
    if min(rows) < 0 or max(rows) >= diff.shape[0] or b <= a:
        return 1.0
    profile = np.array([diff[r, a:b].mean() for r in rows])
    total = abs(profile[-1] - profile[0])
    if total < 1:
        return 1.0
    return float(min(1.0, np.abs(np.diff(profile)).max() / total))


def detect_blocks(image: np.ndarray, x_range: tuple[int, int], y_range: tuple[int, int], px_per_hour: float) -> list[Block]:
    """
    Find event blocks in one day column.

    Args:
        image (np.ndarray): BGR screenshot.
        x_range (tuple[int, int]): Column bounds [x0, x1) excluding separators.
        y_range (tuple[int, int]): Grid bounds [y0, y1).
        px_per_hour (float): Vertical scale from the time axis fit.

    Returns:
        list[Block]: Blocks sorted by (top, left), in image coordinates.
    """
    cx0, cx1 = x_range
    gy0, gy1 = y_range
    region = image[gy0:gy1, cx0:cx1]
    bg = background_color(region)
    diff = np.abs(region.astype(np.int16) - bg.astype(np.int16)).max(axis=2)
    mask = (diff > DIFF_THRESHOLD) & ~line_like_mask(region, bg)
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    acc = accent_mask(region)
    min_height = max(6, int(0.12 * px_per_hour))
    min_bar = max(6, int(0.1 * px_per_hour))

    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    pieces: list[tuple[int, int, int, int]] = []
    for i in range(1, count):
        x, y, w, h, _ = stats[i]
        if h < min_height or w < 20:
            continue
        comp = labels[y : y + h, x : x + w] == i
        bars = _accent_bars(acc[y : y + h, x : x + w] & comp, min_bar)
        pieces.extend(_split_by_bars(comp, x, y, bars))

    blocks: list[Block] = []
    for x, y, x1, y1 in pieces:
        h, w = y1 - y + 1, x1 - x + 1
        if h < min_height or w < 20:
            continue
        sub_hsv = hsv[y : y1 + 1, x : x1 + 1]
        sub_mask = mask[y : y1 + 1, x : x1 + 1].astype(bool)
        sat = float(np.median(sub_hsv[..., 1][sub_mask]))
        hue = float(np.median(sub_hsv[..., 0][sub_mask]))
        bars = _accent_bars(acc[y : y1 + 1, x : x1 + 1], min_bar)
        if sat < 30 and not bars:
            continue  # gray overlays such as the side handle
        is_red = (hue < 8 or hue > 170) and sat > 120
        if is_red and h < 0.2 * px_per_hour and w > 3 * h and not bars:
            continue  # the red current-time line
        color = tuple(int(c) for c in np.median(region[y : y1 + 1, x : x1 + 1][sub_mask], axis=0))
        left_bars = [bar for bar in bars if bar[0] < 0.3 * w]
        inset = float(min(bar[1] for bar in left_bars)) if left_bars else None
        sharp = min(_edge_sharpness(diff, x, x1, y, 1), _edge_sharpness(diff, x, x1, y1, -1))
        blocks.append(Block(cx0 + x, gy0 + y, cx0 + x1, gy0 + y1, color, inset, sharp))  # type: ignore[arg-type]
    blocks.sort(key=lambda b: (b.y0, b.x0))
    return blocks


def _split_by_bars(
    comp: np.ndarray, ox: int, oy: int, bars: list[tuple[int, int, int, int]]
) -> list[tuple[int, int, int, int]]:
    """
    Split a component that contains several accent bars into one piece per bar.

    Bars at different x split it into side-by-side pieces (cut just left of each
    later bar); bars at the same x but different y split it into stacked pieces
    (cut halfway between bars).

    Args:
        comp (np.ndarray): Boolean mask of the component within its bounding box.
        ox (int): Bounding box left (region coordinates).
        oy (int): Bounding box top (region coordinates).
        bars (list[tuple[int, int, int, int]]): Accent bars in bounding-box coordinates.

    Returns:
        list[tuple[int, int, int, int]]: Pieces as tight (x0, y0, x1, y1) boxes in region coordinates.
    """
    if len(bars) <= 1:
        return [_tight_box(comp, ox, oy)] if comp.any() else []
    bars = sorted(bars, key=lambda b: (b[0], b[1]))
    first = bars[0]
    # Side by side: another bar starts well to the right of the first one.
    right = [b for b in bars if b[0] - first[0] > 20]
    if right:
        cut = min(b[0] for b in right) - 4
        left_part, right_part = comp.copy(), comp.copy()
        left_part[:, cut:] = False
        right_part[:, :cut] = False
        left_bars = [b for b in bars if b[0] < cut]
        right_bars = [(b[0], b[1], b[2], b[3]) for b in bars if b[0] >= cut]
        return _split_by_bars(left_part, ox, oy, left_bars) + _split_by_bars(right_part, ox, oy, right_bars)
    # Stacked: same x, cut between consecutive bars.
    stacked = sorted(bars, key=lambda b: b[1])
    pieces = []
    top = 0
    for upper, lower in zip(stacked, stacked[1:]):
        cut = (upper[3] + lower[1]) // 2
        part = comp.copy()
        part[:top] = False
        part[cut:] = False
        if part.any():
            pieces.append(_tight_box(part, ox, oy))
        top = cut
    part = comp.copy()
    part[:top] = False
    if part.any():
        pieces.append(_tight_box(part, ox, oy))
    return pieces


def _tight_box(part: np.ndarray, ox: int, oy: int) -> tuple[int, int, int, int]:
    """
    Tight bounding box of a boolean mask, offset into region coordinates.

    Args:
        part (np.ndarray): Boolean mask.
        ox (int): x offset.
        oy (int): y offset.

    Returns:
        tuple[int, int, int, int]: (x0, y0, x1, y1) inclusive.
    """
    ys, xs = np.nonzero(part)
    return ox + int(xs.min()), oy + int(ys.min()), ox + int(xs.max()), oy + int(ys.max())
