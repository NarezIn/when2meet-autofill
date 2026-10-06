"""Debug overlay: annotate a screenshot with the axis fit, columns, grid and detected boxes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:
    from .extractor import ImageAnalysis

GREEN = (80, 220, 80)
ORANGE = (0, 165, 255)
CYAN = (255, 255, 0)
MAGENTA = (255, 0, 255)
YELLOW = (0, 230, 255)
RED = (60, 60, 255)


def _text(img: np.ndarray, text: str, org: tuple[int, int], color: tuple[int, int, int], scale: float = 0.6) -> None:
    """
    Draw readable text (dark outline + colour).

    Args:
        img (np.ndarray): Image to draw on.
        text (str): ASCII text.
        org (tuple[int, int]): Bottom-left corner.
        color (tuple[int, int, int]): BGR colour.
        scale (float): Font scale.

    Returns:
        None
    """
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def draw_debug(analysis: ImageAnalysis, threshold: float) -> bytes:
    """
    Render the debug overlay for one screenshot.

    Shows: axis labels (green = used in the fit, red = rejected) with the fitted
    hour ticks, grid bounds (cyan), column bounds and weekday (magenta), and
    each box with its times and confidence (green = ok, orange = low).

    Args:
        analysis (ImageAnalysis): Findings for the image.
        threshold (float): Low-confidence threshold.

    Returns:
        bytes: PNG data.
    """
    img = analysis.image.copy()
    width = img.shape[1]
    axis = analysis.axis
    if analysis.error:
        _text(img, f"ERROR: {analysis.error}"[:80], (10, 40), RED, 0.8)
    if axis:
        used = {id(label) for label in axis.inliers}
        for label in axis.labels:
            color = GREEN if id(label) in used else RED
            cv2.circle(img, (int(width * 0.13), int(label.y)), 5, color, -1)
            cv2.line(img, (0, int(label.y_raw)), (12, int(label.y_raw)), color, 2)
        lo = min(label.minutes for label in axis.labels) - 60
        hi = max(label.minutes for label in axis.labels) + 60
        for minutes in range(lo - lo % 60, hi + 1, 60):
            y = int(axis.minutes_to_y(minutes))
            cv2.line(img, (int(width * 0.135), y), (int(width * 0.15), y), YELLOW, 2)
        _text(img, f"axis: {len(axis.inliers)}/{len(axis.labels)} labels, rms {axis.residual_px:.2f}px, "
              f"{axis.px_per_hour:.1f}px/h", (10, 25), YELLOW)
    if analysis.grid:
        y0, y1 = analysis.grid
        cv2.line(img, (0, y0), (width, y0), CYAN, 2)
        cv2.line(img, (0, y1), (width, y1), CYAN, 2)
    for column in analysis.columns:
        cv2.line(img, (column.x0, 0), (column.x0, img.shape[0]), MAGENTA, 1)
        cv2.line(img, (column.x1, 0), (column.x1, img.shape[0]), MAGENTA, 1)
        y = (analysis.grid[0] - 8) if analysis.grid else 60
        label = f"col {column.index}: {column.day} ({column.quality})" + (f" {column.date}" if column.date else "")
        _text(img, label, (column.x0 + 6, y), MAGENTA)
    for event in analysis.events:
        x0, y0, x1, y1 = event.bbox
        color = ORANGE if event.confidence < threshold else GREEN
        cv2.rectangle(img, (x0, y0), (x1, y1), color, 2)
        tag = f"{event.start}-{event.end} c={event.confidence:.2f}"
        if event.minimum_height:
            tag += " min"
        if event.cut_off:
            tag += f" cut:{event.cut_off}"
        _text(img, tag, (x0 + 4, min(y1 - 4, y0 + 22)), color, 0.55)
    ok, png = cv2.imencode(".png", img)
    return png.tobytes() if ok else b""
