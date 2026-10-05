"""Date detection: full dates in PDF text, and best-guess dates for report links."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import List, Optional

from dateutil import parser as date_parser
from dateutil.relativedelta import relativedelta

from .config import EARLIEST_REPORT_YEAR, PED_OUTPUT_MONTHS
from .models import DateGuess

_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DAY = r"(?:3[01]|[12]\d|0?[1-9])"
_YEAR = r"(?:19|20)\d{2}"
_ORDINAL = r"(?:\s?(?:st|nd|rd|th))?"

# 31 March 2026 | 31st March, 2026 | 31-Mar-2026
_DMY_TEXT_RE = re.compile(
    rf"(?<!\d)({_DAY}){_ORDINAL}[\s\-./,]*(?:of\s+)?(?<![a-z])({_MONTH})(?![a-z])[\s\-./,']*({_YEAR})(?!\d)",
    re.IGNORECASE,
)
# March 31, 2026 | December 31st 2025
_MDY_TEXT_RE = re.compile(
    rf"(?<![a-z])({_MONTH})(?![a-z])\.?[\s\-]*({_DAY})(?!\d){_ORDINAL}[\s,\-./]*({_YEAR})(?!\d)",
    re.IGNORECASE,
)
# 31.03.2026 | 31/03/2026 | 31-03-2026
_DMY_NUM_RE = re.compile(rf"(?<![\d./\-])(\d{{1,2}})([./\-])(\d{{1,2}})\2({_YEAR})(?![\d])")
# 2026-03-31
_YMD_NUM_RE = re.compile(rf"(?<![\d./\-])({_YEAR})([./\-])(\d{{1,2}})\2(\d{{1,2}})(?![\d./\-]\d|\d)")
# 31032026 / 20260331 (file names only)
_DMY_COMPACT_RE = re.compile(rf"(?<!\d)(\d{{2}})(\d{{2}})({_YEAR})(?!\d)")
_YMD_COMPACT_RE = re.compile(rf"(?<!\d)({_YEAR})(\d{{2}})(\d{{2}})(?!\d)")

# March 2026
_MONTH_YEAR_RE = re.compile(
    rf"(?<![a-z])({_MONTH})(?![a-z])[\s\-./,']*({_YEAR})(?!\d)", re.IGNORECASE
)
# 2025-26 | 2025/2026 | 2025–26 (a financial year that spans two calendar years)
_SPLIT_YEAR_RE = re.compile(rf"(?<!\d)({_YEAR})\s?[\-/–—]\s?(\d{{4}}|\d{{2}})(?!\d)")
# FY26 | FY 2026 | FY'26
_FY_RE = re.compile(r"(?<![a-z])fy\s?'?\s?(\d{4}|\d{2})(?!\d)", re.IGNORECASE)
_PLAIN_YEAR_RE = re.compile(rf"(?<!\d)({_YEAR})(?!\d)")

_QUARTER_PATTERNS = (
    (re.compile(r"(?<![a-z0-9])q\s?([1-4])(?![0-9])", re.IGNORECASE), None),
    (re.compile(r"(?<![a-z0-9])([1-4])\s?q(?![a-z0-9])", re.IGNORECASE), None),
    (re.compile(r"\b(?:first|1st)\s+quarter", re.IGNORECASE), 1),
    (re.compile(r"\b(?:second|2nd)\s+quarter|\bhalf[\s\-]?year|\bh1\b|\bsix\s+months|\binterim\b", re.IGNORECASE), 2),
    (re.compile(r"\b(?:third|3rd)\s+quarter|\bnine\s+months|\b9m\b", re.IGNORECASE), 3),
    (re.compile(r"\b(?:fourth|4th)\s+quarter", re.IGNORECASE), 4),
)


def format_ped(value: date) -> str:
    """Format a date as DD-MMM-YYYY (English month, independent of the OS locale)."""
    return f"{value.day:02d}-{PED_OUTPUT_MONTHS[value.month - 1]}-{value.year}"


def _plausible_year(year: int, today: date) -> bool:
    return EARLIEST_REPORT_YEAR <= year <= today.year + 1


def _from_parts(day: int, month: int, year: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _from_month_name(day: str, month_name: str, year: str) -> Optional[date]:
    """Build a date from a textual month using python-dateutil."""
    try:
        return date_parser.parse(f"{int(day)} {month_name} {year}", dayfirst=True).date()
    except (ValueError, OverflowError):
        return None


def _month_end(year: int, month: int) -> date:
    return date(year, month, 1) + relativedelta(day=31)


def find_full_dates(text: str, include_compact: bool = False) -> List[date]:
    """Every complete date (day, month and year) found in ``text``.

    Understands "31 March 2026", "31st March, 2026", "March 31, 2026",
    "31-Mar-2026", "31.03.2026", "31/03/2026" and "2026-03-31". Numeric dates
    are read day-first unless only month-first is valid ("12/31/2025").
    """
    found: List[date] = []

    for match in _DMY_TEXT_RE.finditer(text):
        value = _from_month_name(match.group(1), match.group(2), match.group(3))
        if value:
            found.append(value)

    for match in _MDY_TEXT_RE.finditer(text):
        value = _from_month_name(match.group(2), match.group(1), match.group(3))
        if value:
            found.append(value)

    for match in _DMY_NUM_RE.finditer(text):
        first, second, year = int(match.group(1)), int(match.group(3)), int(match.group(4))
        value = _from_parts(first, second, year) if second <= 12 else _from_parts(second, first, year)
        if value:
            found.append(value)

    for match in _YMD_NUM_RE.finditer(text):
        value = _from_parts(int(match.group(4)), int(match.group(3)), int(match.group(1)))
        if value:
            found.append(value)

    if include_compact:
        for match in _DMY_COMPACT_RE.finditer(text):
            value = _from_parts(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            if value:
                found.append(value)
        for match in _YMD_COMPACT_RE.finditer(text):
            value = _from_parts(int(match.group(3)), int(match.group(2)), int(match.group(1)))
            if value:
                found.append(value)

    return found


def latest_period_date(text: str, today: Optional[date] = None) -> Optional[date]:
    """Most recent complete date in ``text`` that could be a period end date."""
    today = today or date.today()
    limit = today + timedelta(days=1)
    valid = [d for d in find_full_dates(text) if d.year >= EARLIEST_REPORT_YEAR and d <= limit]
    return max(valid) if valid else None


def _quarter_in(text: str) -> Optional[int]:
    for pattern, fixed in _QUARTER_PATTERNS:
        match = pattern.search(text)
        if match:
            return fixed if fixed is not None else int(match.group(1))
    return None


def infer_document_date(text: str, today: Optional[date] = None, is_file_name: bool = False) -> Optional[DateGuess]:
    """Best guess at the period a report link refers to.

    Order of preference: a complete date, then month + year, then a year.
    Year-only matches are placed on a nominal year end so that documents can
    be compared: a plain year ("2025") counts as 31 Dec 2025, a split
    financial year ("2025-26") as 31 Mar 2026, and a quarter marker
    ("Q2", "half year") moves that date back by the remaining quarters.
    """
    if not text:
        return None
    today = today or date.today()
    if is_file_name:
        text = text.replace("_", " ").replace("%20", " ").replace("+", " ")

    horizon = today + timedelta(days=366)
    full = [
        d for d in find_full_dates(text, include_compact=is_file_name)
        if _plausible_year(d.year, today) and d <= horizon
    ]
    if full:
        return DateGuess(max(full), 3)

    months: List[date] = []
    for match in _MONTH_YEAR_RE.finditer(text):
        year = int(match.group(2))
        parsed = _from_month_name("1", match.group(1), match.group(2))
        if parsed and _plausible_year(year, today):
            months.append(_month_end(parsed.year, parsed.month))
    if months:
        return DateGuess(max(months), 2)

    nominal: List[date] = []
    remainder = text
    for match in _SPLIT_YEAR_RE.finditer(text):
        start = int(match.group(1))
        end_raw = match.group(2)
        end = int(end_raw) if len(end_raw) == 4 else (start // 100) * 100 + int(end_raw)
        if end == start + 1 and _plausible_year(end, today):
            nominal.append(date(end, 3, 31))
            remainder = remainder.replace(match.group(0), " ")
    for match in _FY_RE.finditer(remainder):
        raw = match.group(1)
        year = int(raw) if len(raw) == 4 else 2000 + int(raw)
        if _plausible_year(year, today):
            nominal.append(date(year, 12, 31))
    for match in _PLAIN_YEAR_RE.finditer(remainder):
        year = int(match.group(1))
        if _plausible_year(year, today):
            nominal.append(date(year, 12, 31))
    if not nominal:
        return None

    value = max(nominal)
    quarter = _quarter_in(text)
    if quarter is not None and quarter < 4:
        shifted = value - relativedelta(months=3 * (4 - quarter))
        value = _month_end(shifted.year, shifted.month)
    return DateGuess(value, 1)
