"""
Event colours and colour rules.

Each extracted event carries its calendar colour as "#rrggbb": the bright accent
pixels of an iOS block (its bar, dot or text), or the fill of a Google Calendar
card. Colour rules compare colours by CIE Lab distance (Delta E 1976). On the
fixtures, one calendar's colour varies by at most ~1.2 between screenshots, while
the two closest different calendars (iOS light blue #78c2fa vs blue #59a7d7) are
~10.9 apart, so the match threshold sits between them.
"""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np

# Two colours closer than this (Delta E 1976) count as the same calendar colour.
MATCH_DELTA_E = 6.0

ColorRuleMode = Literal["exclude", "only"]


def to_hex(bgr: tuple[int, int, int] | np.ndarray) -> str:
    """
    Format a BGR colour as "#rrggbb".

    Args:
        bgr (tuple[int, int, int] | np.ndarray): Blue, green, red (0..255).

    Returns:
        str: The hex colour.
    """
    b, g, r = (int(c) for c in bgr)
    return f"#{r:02x}{g:02x}{b:02x}"


def parse_hex(color: str) -> tuple[int, int, int] | None:
    """
    Parse "#rrggbb" (or "#rgb") into RGB.

    Args:
        color (str): The hex colour.

    Returns:
        tuple[int, int, int] | None: (r, g, b), or None if it isn't a hex colour.
    """
    text = color.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return None
    try:
        value = int(text, 16)
    except ValueError:
        return None
    return (value >> 16) & 255, (value >> 8) & 255, value & 255


def to_lab(rgb: tuple[int, int, int]) -> np.ndarray:
    """
    Convert an RGB colour to CIE Lab (L 0..100, a/b roughly -128..127).

    Args:
        rgb (tuple[int, int, int]): Red, green, blue (0..255).

    Returns:
        np.ndarray: [L, a, b] as floats.
    """
    pixel = np.array([[list(rgb)]], dtype=np.float32) / 255.0
    return cv2.cvtColor(pixel, cv2.COLOR_RGB2Lab)[0, 0].astype(float)


def color_distance(a: str, b: str) -> float | None:
    """
    Delta E 1976 between two hex colours.

    Args:
        a (str): First colour, "#rrggbb".
        b (str): Second colour, "#rrggbb".

    Returns:
        float | None: The distance, or None if either isn't a hex colour.
    """
    rgb_a, rgb_b = parse_hex(a), parse_hex(b)
    if rgb_a is None or rgb_b is None:
        return None
    return float(np.linalg.norm(to_lab(rgb_a) - to_lab(rgb_b)))


def matches_any(color: str | None, rules: list[str]) -> bool:
    """
    Whether an event colour matches one of the rule colours.

    Args:
        color (str | None): The event's colour, if known.
        rules (list[str]): Rule colours, "#rrggbb".

    Returns:
        bool: True if some rule colour is within MATCH_DELTA_E.
    """
    if not color:
        return False
    return any((d := color_distance(color, rule)) is not None and d <= MATCH_DELTA_E for rule in rules)


def passes_color_rules(color: str | None, rules: list[str], mode: ColorRuleMode) -> bool:
    """
    Whether an event still counts as busy under the colour rules.

    Args:
        color (str | None): The event's colour, if known.
        rules (list[str]): Rule colours; an empty list turns the rules off.
        mode (ColorRuleMode): "exclude" = events in these colours don't count;
            "only" = only events in these colours count.

    Returns:
        bool: True if the event should count as busy.
    """
    if not rules:
        return True
    hit = matches_any(color, rules)
    return hit if mode == "only" else not hit
