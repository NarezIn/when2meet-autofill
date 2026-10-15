"""Unit tests for event colours and colour rules (matching and their effect on the fill plan)."""

from __future__ import annotations

import datetime as dt

import pytest

from screenshot2meet.colors import color_distance, parse_hex, passes_color_rules, to_hex
from screenshot2meet.models import FillPlanRequest, PlanSettings, ReviewedEvent
from screenshot2meet.slots import compute_fill_plan


def test_hex_round_trip() -> None:
    """BGR -> hex -> RGB keeps the channels in the right order."""
    assert to_hex((62, 95, 221)) == "#dd5f3e"
    assert parse_hex("#dd5f3e") == (221, 95, 62)
    assert parse_hex("#abc") == (170, 187, 204)
    assert parse_hex("red") is None


@pytest.mark.parametrize(
    "a,b,same",
    [
        ("#f9db69", "#f6d867", True),   # iOS yellow, portrait vs landscape
        ("#59a7d7", "#57a4d3", True),   # iOS blue, two screenshots
        ("#78c2fa", "#59a7d7", False),  # iOS light blue vs blue: different calendars
        ("#56a5dd", "#6c94eb", False),  # two Google blues
        ("#f9db69", "#dd5f3e", False),  # yellow vs red
    ],
)
def test_same_calendar_colour(a: str, b: str, same: bool) -> None:
    """Renderings of one calendar colour match; neighbouring calendar colours don't."""
    assert passes_color_rules(a, [b], "only") is same
    assert color_distance(a, b) is not None


def test_rule_modes() -> None:
    """Exclude drops matching events, only keeps just them; no rules or no colour = rules off / no match."""
    assert passes_color_rules("#dd5f3e", ["#dc5e3d"], "exclude") is False
    assert passes_color_rules("#f9db69", ["#dc5e3d"], "exclude") is True
    assert passes_color_rules("#f9db69", ["#dc5e3d"], "only") is False
    assert passes_color_rules("#f9db69", [], "only") is True
    assert passes_color_rules(None, ["#dc5e3d"], "only") is False
    assert passes_color_rules(None, ["#dc5e3d"], "exclude") is True


def test_fill_plan_applies_colour_rules() -> None:
    """Events left out by colour rules don't block slots and are reported."""
    base = int(dt.datetime(2026, 10, 5, 13, 0, tzinfo=dt.timezone.utc).timestamp())  # 09:00 New York
    slots = [base + 900 * i for i in range(8)]  # 09:00..11:00
    events = [
        ReviewedEvent(day="Mon", start="09:00", end="10:00", color="#dd5f3e"),
        ReviewedEvent(day="Mon", start="10:00", end="11:00", color="#f9db69"),
    ]

    def plan(mode: str, rules: list[str]) -> tuple[int, list[int]]:
        """
        Run the planner with colour rules.

        Args:
            mode (str): "exclude" or "only".
            rules (list[str]): Rule colours.

        Returns:
            tuple[int, list[int]]: (number of available slots, indices skipped by colour).
        """
        req = FillPlanRequest(
            slots=slots,
            mode="specific_dates",
            events=events,
            settings=PlanSettings(min_free_block_minutes=0, color_rules=rules, color_rule_mode=mode),
        )
        result = compute_fill_plan(req)
        return len(result.available_slots), result.color_skipped

    assert plan("exclude", []) == (0, [])
    assert plan("exclude", ["#dc5e3d"]) == (4, [0])
    assert plan("only", ["#dc5e3d"]) == (4, [1])
