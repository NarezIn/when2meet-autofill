"""
Merge events found in several screenshots and apply keyword rules.

The same event can appear in two screenshots (e.g. overlapping scroll
positions). Copies from different images that overlap by at least half are
treated as one event: complete copies win over cut-off ones, and copies that
disagree by more than 15 minutes are kept once with lowered confidence.
Events from the same image are never merged (side-by-side events may share
the same times).
"""

from __future__ import annotations

from .confidence import DISAGREEMENT_FACTOR, DISAGREEMENT_MINUTES
from .models import EventContext, ExtractedEvent, parse_hhmm


def _key(event: ExtractedEvent) -> str:
    """
    Group key: the date when known, else the weekday.

    Args:
        event (ExtractedEvent): An event.

    Returns:
        str: Group key.
    """
    return event.date.isoformat() if event.date else event.day


def _span(event: ExtractedEvent) -> tuple[int, int]:
    """
    Start and end in minutes.

    Args:
        event (ExtractedEvent): An event.

    Returns:
        tuple[int, int]: (start, end) minutes after midnight.
    """
    return parse_hhmm(event.start), parse_hhmm(event.end)


def _overlap_ratio(a: ExtractedEvent, b: ExtractedEvent) -> float:
    """
    Overlap of two events relative to the shorter one.

    Args:
        a (ExtractedEvent): First event.
        b (ExtractedEvent): Second event.

    Returns:
        float: 0..1.
    """
    (s1, e1), (s2, e2) = _span(a), _span(b)
    inter = max(0, min(e1, e2) - max(s1, s2))
    shorter = max(1, min(e1 - s1, e2 - s2))
    return inter / shorter


def _titles_compatible(a: ExtractedEvent, b: ExtractedEvent) -> bool:
    """
    Whether two titles could be the same event (either missing, or one truncated from the other).

    Args:
        a (ExtractedEvent): First event.
        b (ExtractedEvent): Second event.

    Returns:
        bool: True if compatible.
    """
    if not a.title or not b.title:
        return True
    ta, tb = (t.lower().rstrip(".…").strip() for t in (a.title, b.title))
    return ta.startswith(tb[:4]) or tb.startswith(ta[:4])


def _combine(a: ExtractedEvent, b: ExtractedEvent) -> ExtractedEvent:
    """
    Merge two copies of the same event.

    Args:
        a (ExtractedEvent): First copy.
        b (ExtractedEvent): Second copy (from another screenshot).

    Returns:
        ExtractedEvent: The merged event.
    """
    if (a.cut_off is None) != (b.cut_off is None):
        return a if a.cut_off is None else b
    if a.cut_off and b.cut_off and a.cut_off != b.cut_off:
        top_ok, bottom_ok = (a, b) if a.cut_off == "bottom" else (b, a)
        merged = top_ok.model_copy(update={"end": bottom_ok.end, "cut_off": None})
        return merged
    (s1, e1), (s2, e2) = _span(a), _span(b)
    best = a if a.confidence >= b.confidence else b
    if abs(s1 - s2) > DISAGREEMENT_MINUTES or abs(e1 - e2) > DISAGREEMENT_MINUTES:
        return best.model_copy(
            update={
                "confidence": round(best.confidence * DISAGREEMENT_FACTOR, 3),
                "reasons": [*best.reasons, "screenshots disagree by more than 15 min"],
            }
        )
    return best


def merge_events(events: list[ExtractedEvent], ctx: EventContext) -> list[ExtractedEvent]:
    """
    Deduplicate events across screenshots, then set low-confidence and busy defaults.

    Args:
        events (list[ExtractedEvent]): Events from all screenshots.
        ctx (EventContext): Threshold and keyword rules.

    Returns:
        list[ExtractedEvent]: Merged events sorted by day and start.
    """
    merged: list[ExtractedEvent] = []
    for event in sorted(events, key=lambda e: (e.source_image, _key(e), e.start)):
        match = next(
            (
                i
                for i, kept in enumerate(merged)
                if kept.source_image != event.source_image
                and _key(kept) == _key(event)
                and _overlap_ratio(kept, event) >= 0.5
                and _titles_compatible(kept, event)
            ),
            None,
        )
        if match is None:
            merged.append(event)
        else:
            merged[match] = _combine(merged[match], event)

    rules = [rule.strip().lower() for rule in ctx.keyword_rules if rule.strip()]
    out = []
    for event in merged:
        title = (event.title or "").lower()
        out.append(
            event.model_copy(
                update={
                    "low_confidence": event.confidence < ctx.confidence_threshold,
                    "counts_as_busy": not any(rule in title for rule in rules),
                }
            )
        )
    order = {day: i for i, day in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))}
    out.sort(key=lambda e: (e.date.isoformat() if e.date else "", order[e.day], e.start, e.bbox[0]))
    return out
