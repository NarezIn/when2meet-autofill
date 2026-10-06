"""
Computed confidence for extracted events (not a model probability).

The score starts at 1.0 and is multiplied by one factor per signal. Each
factor that lowers the score also adds a short human-readable reason, which
the review screen shows.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .weekdays import MatchQuality

WEEKDAY_FACTOR: dict[MatchQuality, float] = {"exact": 1.0, "fuzzy": 0.9, "date": 0.9, "guessed": 0.6}
MIN_HEIGHT_FACTOR = 0.6
CUT_OFF_FACTOR = 0.6
DISAGREEMENT_FACTOR = 0.6
DISAGREEMENT_MINUTES = 15


@dataclass
class Confidence:
    """A running confidence score with the reasons it was lowered."""

    score: float = 1.0
    reasons: list[str] = field(default_factory=list)

    def apply(self, factor: float, reason: str) -> None:
        """
        Multiply in a factor; record the reason if it lowers the score.

        Args:
            factor (float): Multiplier in 0..1.
            reason (str): Why (shown to the user).

        Returns:
            None
        """
        factor = max(0.0, min(1.0, factor))
        if factor < 0.999:
            self.score *= factor
            self.reasons.append(reason)


def axis_residual_factor(residual_px: float) -> float:
    """
    Penalty for a poor time-axis fit; no penalty up to 3 px RMS.

    Args:
        residual_px (float): RMS residual of the axis fit in pixels.

    Returns:
        float: Factor in [0.4, 1].
    """
    return 1.0 if residual_px <= 3 else max(0.4, 1 - 0.1 * (residual_px - 3))


def axis_ocr_factor(mean_score: float) -> float:
    """
    Penalty for low OCR confidence on the axis labels; none at 0.9 or above.

    Args:
        mean_score (float): Mean OCR confidence of the labels used in the fit.

    Returns:
        float: Factor in [0.5, 1].
    """
    return max(0.5, min(1.0, 0.5 + 0.5 * (mean_score - 0.5) / 0.4))


def edge_factor(sharpness: float) -> float:
    """
    Small penalty for blurry top/bottom edges.

    Args:
        sharpness (float): 0..1 from blocks.detect_blocks.

    Returns:
        float: Factor in [0.85, 1].
    """
    return 0.85 + 0.15 * max(0.0, min(1.0, sharpness))


def score_event(
    *,
    axis_residual_px: float,
    axis_ocr_score: float,
    weekday_quality: MatchQuality,
    minimum_height: bool,
    edge_sharpness: float,
    cut_off: bool,
) -> Confidence:
    """
    Combine the per-event signals into a confidence score.

    Args:
        axis_residual_px (float): RMS residual of the image's axis fit.
        axis_ocr_score (float): Mean OCR confidence of the axis labels.
        weekday_quality (MatchQuality): How the column's weekday was determined.
        minimum_height (bool): Block drawn at iOS's minimum height (true length unknown).
        edge_sharpness (float): Sharpness of the block's top/bottom edges (0..1).
        cut_off (bool): Block touches the top or bottom of the visible grid.

    Returns:
        Confidence: Score in 0..1 plus reasons.
    """
    conf = Confidence()
    conf.apply(axis_residual_factor(axis_residual_px), f"time axis fit off by {axis_residual_px:.1f}px")
    conf.apply(axis_ocr_factor(axis_ocr_score), f"time labels hard to read ({axis_ocr_score:.2f})")
    conf.apply(WEEKDAY_FACTOR[weekday_quality], f"weekday {weekday_quality}")
    if minimum_height:
        conf.apply(MIN_HEIGHT_FACTOR, "short event drawn at minimum height; length assumed 15 min")
    conf.apply(edge_factor(edge_sharpness), "blurry block edges")
    if cut_off:
        conf.apply(CUT_OFF_FACTOR, "cut off at the edge of the screenshot")
    return conf
