"""
List ("agenda" / "schedule") layouts, e.g. Google Calendar's Schedule view.

These screenshots have no time axis. Each event is a coloured card whose text
states its time range ("14:30–15:00", "2:30 – 3:30pm"), and each day is
labelled once on the left ("WED" over "14"), next to its first card. Week or
month headers ("OCTOBER 18 – 24") give the month.

Pipeline: OCR words -> cards (solid colour blocks) -> time range per card
(none = all-day) -> day label above-left of each card -> month from the
nearest header -> ExtractedEvent.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from functools import lru_cache

import cv2
import numpy as np
from babel import Locale

from .blocks import accent_color, accent_mask, background_color
from .colors import to_hex
from .confidence import CUT_OFF_FACTOR, WEEKDAY_FACTOR, Confidence, axis_ocr_factor
from .models import EventContext, ExtractedEvent, Weekday, format_hhmm
from .ocr import OcrWord
from .timeaxis import parse_time_label
from .weekdays import PREFERRED_LOCALES, MatchQuality, find_weekday, normalize

# One side of a time range: optional CJK day-part, hour, optional minutes, optional am/pm.
_SIDE = r"(?:上午|下午|中午|凌晨|早上|晚上|傍晚)?\s*\d{1,2}(?:[:：.]\d{2})?\s*(?:[aApP]\.?\s?[mM]\.?)?"
_RANGE = re.compile(rf"({_SIDE})\s*[–—\-~〜至到]\s*({_SIDE})")
_AMPM = re.compile(r"([ap])\.?\s?m\.?$", re.IGNORECASE)
_CJK_PART = re.compile(r"^(上午|下午|中午|凌晨|早上|晚上|傍晚)")
_DAY_NUMBER = re.compile(r"^\d{1,2}$")
_CJK_MONTH = re.compile(r"(\d{1,2})\s*月")
_LEADING_DAY = re.compile(r"^\D*?(\d{1,2})(?!\d)")

# A card is a solid block at least this wide (fraction of the image) and tall (px)...
CARD_MIN_WIDTH = 0.3
CARD_MIN_HEIGHT = 30
# ...that fills at least this much of its bounding box (text alone is far sparser).
CARD_MIN_FILL = 0.6
# Colour distance from the background that counts as "not background".
CARD_DIFF = 28
# A day label may sit this far below its card's top edge (labels are vertically centred-ish).
LABEL_SLACK_PX = 40
ALL_DAY_FACTOR = 0.9


@dataclass
class DayLabel:
    """A day label in the left gutter ("WED" + "14")."""

    text: str
    day: Weekday
    quality: MatchQuality
    day_of_month: int | None
    box: tuple[int, int, int, int]
    date: dt.date | None = None


@dataclass
class MonthHeader:
    """A week/month header ("OCTOBER 18 – 24"): month of its first day, and that day."""

    y: float
    month: int
    first_day: int | None


@dataclass
class AgendaResult:
    """What an agenda screenshot yielded."""

    labels: list[DayLabel] = field(default_factory=list)
    events: list[ExtractedEvent] = field(default_factory=list)


def _with_ampm(side: str, ampm: str) -> int | None:
    """
    Parse one side of a range, forcing an am/pm suffix.

    Args:
        side (str): Time text without am/pm, e.g. "2:30".
        ampm (str): "a" or "p".

    Returns:
        int | None: Minutes after midnight, or None if it doesn't parse.
    """
    return parse_time_label(f"{side}{ampm}m")


def parse_time_range(text: str) -> tuple[int, int] | None:
    """
    Find a time range in a line of text.

    Accepts "14:30–15:00", "2:30 – 3:30pm", "11am – 12:30pm", "下午2:30 - 3:30".
    One side must have minutes or am/pm, so "18 – 24" (a date range) is not a time.
    A missing am/pm (English, suffix on the end time) or day-part (Chinese, prefix on
    the start time) is copied from the other side.

    Args:
        text (str): OCR text of one line.

    Returns:
        tuple[int, int] | None: (start, end) minutes after midnight; end may be <= start
        for events that cross midnight. None if there is no time range.
    """
    for m in _RANGE.finditer(text):
        a, b = (re.sub(r"\s+", "", s) for s in m.groups())
        if not any(":" in s or "：" in s or "." in s or _AMPM.search(s) for s in (a, b)):
            continue
        a_ampm, b_ampm = _AMPM.search(a), _AMPM.search(b)
        start = parse_time_label(a)
        end = parse_time_label(b)
        if b_ampm and not a_ampm and not _CJK_PART.match(a):
            suffix = b_ampm.group(1).lower()
            same = _with_ampm(a, suffix)
            other = _with_ampm(a, "a" if suffix == "p" else "p")
            # "11 – 1pm" means 11am; "2:30 – 3:30pm" means 2:30pm.
            start = same if same is not None and end is not None and same < end else (other if other is not None else same)
        if (part := _CJK_PART.match(a)) and not _CJK_PART.match(b):
            end = parse_time_label(part.group(1) + b)
        if start is not None and end is not None and start != end:
            return start, end
    return None


@lru_cache(maxsize=1)
def month_table() -> dict[str, int]:
    """
    Month names (wide and abbreviated, normalised) for the preferred CLDR locales.

    Returns:
        dict[str, int]: Normalised name -> month number (1..12).
    """
    table: dict[str, int] = {}
    for code in PREFERRED_LOCALES:
        locale = Locale.parse(code)
        for context in ("format", "stand-alone"):
            for width in ("wide", "abbreviated"):
                for number, name in locale.months[context][width].items():
                    key = normalize(name).rstrip(".")
                    if (len(key) >= 3 or any(ord(ch) > 0x2E80 for ch in key)) and not key.isdigit():
                        table.setdefault(key, number)
    return table


def parse_month_header(text: str) -> tuple[int, int | None] | None:
    """
    Read a week/month header such as "OCTOBER 18 – 24", "Oct 25 – Nov 1" or "10月18日 - 24日".

    Args:
        text (str): OCR text of one line.

    Returns:
        tuple[int, int | None] | None: (month of the first day, first day-of-month), or None.
    """
    if m := _CJK_MONTH.search(text):
        month = int(m.group(1))
        if 1 <= month <= 12:
            day = re.search(r"月\s*(\d{1,2})", text)
            return month, int(day.group(1)) if day else None
    table = month_table()
    for word in re.split(r"[\s,–—\-~]+", text):
        key = normalize(word).rstrip(".")
        if key in table:
            first = _LEADING_DAY.match(text[text.lower().find(word.lower()) + len(word):])
            return table[key], int(first.group(1)) if first else None
    return None


def find_cards(image: np.ndarray, x_min: int) -> list[tuple[int, int, int, int]]:
    """
    Find solid coloured cards right of the day-label gutter.

    Args:
        image (np.ndarray): BGR screenshot.
        x_min (int): Cards start right of this x.

    Returns:
        list[tuple[int, int, int, int]]: (x0, y0, x1, y1) boxes, top to bottom.
    """
    height, width = image.shape[:2]
    bg = background_color(image).astype(np.int16)
    diff = np.abs(image.astype(np.int16) - bg).max(axis=2)
    mask = (diff > CARD_DIFF).astype(np.uint8)
    mask[:, : max(0, x_min)] = 0
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
    cards = []
    for x, y, w, h, area in stats[1:count]:
        if w >= CARD_MIN_WIDTH * width and h >= CARD_MIN_HEIGHT and area >= CARD_MIN_FILL * w * h:
            cards.append((int(x), int(y), int(x + w - 1), int(y + h - 1)))
    return sorted(cards, key=lambda c: c[1])


def find_day_labels(words: list[OcrWord], gutter_right: int) -> list[DayLabel]:
    """
    Pair weekday names in the left gutter with the day number just below them.

    Args:
        words (list[OcrWord]): OCR words of the image.
        gutter_right (int): Right edge of the label gutter.

    Returns:
        list[DayLabel]: Labels, top to bottom.
    """
    gutter = [w for w in words if w.x1 <= gutter_right + 10]
    numbers = [w for w in gutter if _DAY_NUMBER.match(w.text.strip())]
    used: set[int] = set()
    labels = []
    for word in sorted(gutter, key=lambda w: w.y0):
        if id(word) in used or word in numbers:
            continue
        day, quality = find_weekday(word.text)
        if day is None:
            continue
        below = [
            n for n in numbers
            if id(n) not in used and 0 <= n.y0 - word.y1 < 2.5 * (word.y1 - word.y0) + 20 and n.x0 < word.x1 and word.x0 < n.x1
        ]
        number = min(below, key=lambda n: n.y0, default=None)
        box = (word.x0, word.y0, word.x1, word.y1)
        text = word.text
        if number is not None:
            used.add(id(number))
            box = (min(word.x0, number.x0), word.y0, max(word.x1, number.x1), number.y1)
            text = f"{word.text} {number.text}"
        labels.append(
            DayLabel(
                text=text,
                day=day,
                quality=quality or "exact",
                day_of_month=int(number.text) if number is not None else None,
                box=box,
            )
        )
    return labels


def label_date(label: DayLabel, headers: list[MonthHeader], ctx: EventContext) -> dt.date | None:
    """
    Date of a day label: the event's dates first, then the month from the headers.

    Args:
        label (DayLabel): The label (weekday + day of month).
        headers (list[MonthHeader]): Week/month headers in the image.
        ctx (EventContext): The event's dates and year.

    Returns:
        datetime.date | None: The date, or None if it can't be pinned down.
    """
    dom = label.day_of_month
    if dom is None:
        return None
    weekday_index = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun").index(label.day)
    above = [h for h in headers if h.y < label.box[1]]
    below = [h for h in headers if h.y > label.box[1]]
    month = None
    if above:
        header = above[-1]
        month = header.month if header.first_day is None or dom >= header.first_day else header.month % 12 + 1
    elif below:
        header = below[0]
        # Above the first header we are in the week before it: same month, or the previous one.
        month = header.month if header.first_day is None or dom < header.first_day else (header.month - 2) % 12 + 1
    matches = [d for d in ctx.dates if d.day == dom and (month is None or d.month == month)]
    if len(matches) == 1:
        return matches[0]
    years = [ctx.year] if ctx.year else sorted({d.year for d in ctx.dates}) or [dt.date.today().year]
    for year in years + [y + 1 for y in years] + [y - 1 for y in years]:
        for m in [month] if month else range(1, 13):
            try:
                candidate = dt.date(year, m, dom)
            except ValueError:
                continue
            if candidate.weekday() == weekday_index:
                return candidate
    return None


def analyze_agenda(image: np.ndarray, words: list[OcrWord], index: int, ctx: EventContext) -> AgendaResult | None:
    """
    Read a list-layout screenshot, if it is one.

    Args:
        image (np.ndarray): BGR screenshot.
        words (list[OcrWord]): OCR words of the image.
        index (int): Position of the image in the upload.
        ctx (EventContext): Event context and settings.

    Returns:
        AgendaResult | None: Labels and events, or None if no time range or day label was found.
    """
    height, width = image.shape[:2]
    ranges = [(w, r) for w in words if (r := parse_time_range(w.text))]
    labels = find_day_labels(words, int(0.2 * width))
    if not ranges or not labels:
        return None
    gutter_right = max(label.box[2] for label in labels)
    headers = sorted(
        (MonthHeader(y=w.cy, month=p[0], first_day=p[1]) for w in words if w.x0 > gutter_right and (p := parse_month_header(w.text))),
        key=lambda h: h.y,
    )
    for label in labels:
        label.date = label_date(label, headers, ctx)

    cards = find_cards(image, gutter_right + 10)
    # A time range outside every card (e.g. a plain-text list) becomes its own "card".
    for word, _ in ranges:
        if not any(c[0] <= word.cx <= c[2] and c[1] <= word.cy <= c[3] for c in cards):
            cards.append((word.x0, word.y0 - 2 * (word.y1 - word.y0), word.x1, word.y1))
    cards.sort(key=lambda c: c[1])
    if cards and cards[0][1] + LABEL_SLACK_PX < labels[0].box[1]:
        # Cards above the first label belong to a day whose label scrolled off the top:
        # guess the day before the first label, and let the user confirm it.
        first = labels[0]
        days = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        labels.insert(
            0,
            DayLabel(
                text="",
                day=days[(days.index(first.day) - 1) % 7],
                quality="guessed",
                day_of_month=None,
                box=(first.box[0], 0, first.box[2], 0),
                date=first.date - dt.timedelta(days=1) if first.date else None,
            ),
        )

    result = AgendaResult(labels=labels)
    for x0, y0, x1, y1 in cards:
        inside = sorted(
            (w for w in words if x0 <= w.cx <= x1 and y0 <= w.cy <= y1), key=lambda w: (round(w.y0 / 10), w.x0)
        )
        time_words = [(w, r) for w in inside if (r := parse_time_range(w.text))]
        owner = [label for label in labels if label.box[1] <= y0 + LABEL_SLACK_PX]
        label = owner[-1] if owner else labels[0]
        conf = Confidence()
        conf.apply(WEEKDAY_FACTOR[label.quality], f"weekday {label.quality}")
        if time_words:
            word, (start, end) = time_words[0]
            conf.apply(axis_ocr_factor(word.score), f"time text hard to read ({word.score:.2f})")
            title_words = [w for w in inside if w is not word]
            all_day = False
            if end <= start:
                end = 24 * 60  # crosses midnight; the next day's part is in the next day's list
        else:
            title_words = inside
            start, end, all_day = 0, 24 * 60, True
            conf.apply(ALL_DAY_FACTOR, "no time shown; treated as all-day")
        cut_off = "top" if y0 <= 2 else "bottom" if y1 >= height - 3 else None
        if cut_off and not time_words:
            conf.apply(CUT_OFF_FACTOR, "cut off at the edge of the screenshot; its time may be hidden")
        title = " ".join(w.text for w in title_words) or None
        card = image[y0 : y1 + 1, x0 : x1 + 1]
        fill = accent_color(card, accent_mask(card)) or tuple(int(c) for c in np.median(card.reshape(-1, 3), axis=0))
        result.events.append(
            ExtractedEvent(
                day=label.day,
                date_label=label.text or None,
                date=label.date,
                start=format_hhmm(start),
                end=format_hhmm(end),
                title=title,
                color=to_hex(fill),
                confidence=round(conf.score, 3),
                cut_off=cut_off if not time_words else None,
                source_image=index,
                column=labels.index(label),
                bbox=(x0, y0, x1, y1),
                weekday_guessed=label.quality == "guessed",
                all_day=all_day,
                reasons=conf.reasons,
                low_confidence=conf.score < ctx.confidence_threshold,
            )
        )
    return result
