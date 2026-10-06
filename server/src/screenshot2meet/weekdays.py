"""
Weekday and date parsing for calendar column headers.

The weekday lookup table is built from CLDR (via babel): full and abbreviated
weekday names, format and stand-alone forms, for every locale babel ships.
OCR text is normalised (lowercase, accents and periods stripped) and matched
exactly, or with a 1-character fuzzy match for names of 4+ characters.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import babel
from babel import Locale, localedata

from .models import WEEKDAYS, Weekday

MatchQuality = Literal["exact", "fuzzy", "date", "guessed"]

# Locales tried first when a name means different weekdays in different languages.
PREFERRED_LOCALES = ("en", "zh_Hans", "zh_Hant", "es")


def normalize(text: str) -> str:
    """
    Normalise OCR text for matching: lowercase, strip accents, periods and spaces.

    Args:
        text (str): Raw text.

    Returns:
        str: Normalised text (CJK characters are kept as is).
    """
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[.\s·•,，、]", "", stripped)


def _is_cjk(text: str) -> bool:
    """
    Whether a string contains CJK / kana / hangul characters.

    Args:
        text (str): Any string.

    Returns:
        bool: True if any character is in a CJK-ish Unicode block.
    """
    return any("぀" <= ch <= "鿿" or "가" <= ch <= "힯" for ch in text)


def _cache_path() -> Path:
    """
    Where the built table is cached (building it from CLDR takes ~15 s).

    Returns:
        Path: A JSON file path that includes the babel version.
    """
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "screenshot2meet"
    return base / f"weekdays-babel-{babel.__version__}.json"


@lru_cache(maxsize=1)
def weekday_table() -> dict[str, Weekday]:
    """
    Load the normalised-name -> weekday table, building and caching it on first use.

    Returns:
        dict[str, Weekday]: Lookup table.
    """
    path = _cache_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    table = build_weekday_table()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return table


def build_weekday_table() -> dict[str, Weekday]:
    """
    Build the normalised-name -> weekday table from CLDR for all locales.

    Names that mean different weekdays in different locales are resolved in
    favour of PREFERRED_LOCALES, then by majority.

    Returns:
        dict[str, Weekday]: Lookup table. Names shorter than 2 characters are left out.
    """
    votes: dict[str, Counter[Weekday]] = defaultdict(Counter)
    preferred: dict[str, Weekday] = {}
    for locale_id in localedata.locale_identifiers():
        try:
            days = Locale.parse(locale_id).days
        except Exception:  # some locale ids can't be parsed on every babel version
            continue
        for context in ("format", "stand-alone"):
            for width in ("wide", "abbreviated", "short"):
                try:
                    names = days[context][width]
                except KeyError:
                    continue
                for index, name in names.items():
                    key = normalize(name)
                    if len(key) < 2:
                        continue
                    weekday = WEEKDAYS[index]
                    votes[key][weekday] += 1
                    if locale_id in PREFERRED_LOCALES and key not in preferred:
                        preferred[key] = weekday
    table = {key: counter.most_common(1)[0][0] for key, counter in votes.items()}
    table.update(preferred)
    return table


def _within_one_edit(a: str, b: str) -> bool:
    """
    Whether two strings differ by at most one insertion, deletion or substitution.

    Args:
        a (str): First string.
        b (str): Second string.

    Returns:
        bool: True if the edit distance is <= 1.
    """
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = edits = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(a) == len(b):
            i += 1
        j += 1
    return edits + (len(b) - j) + (len(a) - i) <= 1


@dataclass(frozen=True)
class HeaderParse:
    """What we could read from one column header."""

    weekday: Weekday | None
    quality: MatchQuality | None
    day_of_month: int | None
    month: int | None
    text: str


def find_weekday(text: str) -> tuple[Weekday | None, MatchQuality | None]:
    """
    Find a weekday name in OCR text.

    CJK names are matched as substrings ("周一10月5日" contains "周一"); other
    names are matched per word, exactly or with one edit.

    Args:
        text (str): Raw OCR text of a header.

    Returns:
        tuple[Weekday | None, MatchQuality | None]: The weekday and match quality, or (None, None).
    """
    table = weekday_table()
    norm = normalize(text)

    cjk_hits = [(len(name), day) for name, day in table.items() if _is_cjk(name) and name in norm]
    if cjk_hits:
        return max(cjk_hits)[1], "exact"

    words = [normalize(w) for w in re.split(r"[\s,.·/\-]+|\d+", text.lower()) if w]
    words = [w for w in words if len(w) >= 2 and not _is_cjk(w)]
    for word in words:
        if word in table:
            return table[word], "exact"
    for word in words:
        if len(word) < 4:
            continue
        # One-character OCR slips ("Mondav"), or a longer abbreviation ("Thurs" -> "thursday").
        fuzzy = {
            day
            for name, day in table.items()
            if len(name) >= 4 and not _is_cjk(name) and (_within_one_edit(word, name) or name.startswith(word))
        }
        if len(fuzzy) == 1:
            return fuzzy.pop(), "fuzzy"
    return None, None


_CJK_DATE = re.compile(r"(?:(\d{1,2})\s*月\s*)?(\d{1,2})\s*日")
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})\b")
_LONE_NUMBER = re.compile(r"(?<!\d)(\d{1,2})(?!\d)")


def find_date(text: str) -> tuple[int | None, int | None]:
    """
    Find a day-of-month (and month, if shown) in header text.

    Handles "10月5日", "10/5" (month/day), and a lone day number ("Mon 5").

    Args:
        text (str): Raw OCR text of a header.

    Returns:
        tuple[int | None, int | None]: (day_of_month, month); either may be None.
    """
    if m := _CJK_DATE.search(text):
        month = int(m.group(1)) if m.group(1) else None
        return int(m.group(2)), month
    if m := _NUMERIC_DATE.search(text):
        month, day = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return day, month
    if m := _LONE_NUMBER.search(text):
        day = int(m.group(1))
        if 1 <= day <= 31:
            return day, None
    return None, None


def parse_header(text: str) -> HeaderParse:
    """
    Parse a column header into weekday + date parts.

    Args:
        text (str): Raw OCR text of a header (may contain several words joined by spaces).

    Returns:
        HeaderParse: The parse result.
    """
    weekday, quality = find_weekday(text)
    day, month = find_date(text)
    return HeaderParse(weekday=weekday, quality=quality, day_of_month=day, month=month, text=text)
