"""PDF parser: finds the Balance Sheet page of a report and its period end date.

Text extraction uses pdfplumber first and PyMuPDF (fitz) as a fallback. Pages
without extractable text are read with OCR (pytesseract) when it is available.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pdfplumber

from .config import (
    BALANCE_SHEET_ANY_OF_PHRASES,
    BALANCE_SHEET_FIRST_ROWS,
    BALANCE_SHEET_HEADINGS,
    BALANCE_SHEET_REQUIRED_PHRASES,
    CONSOLIDATED_MARKERS,
    HEADER_ZONE_MAX_LINES,
    LOGGER_NAME,
    MIN_TEXT_CHARS_PER_PAGE,
    OCR_DPI,
    OCR_MAX_PAGES,
    STANDALONE_MARKERS,
)
from .dates import latest_period_date
from .models import NoBalanceSheetError, NoDateError, NoTextError, PdfUnreadableError

try:  # PyMuPDF: fallback text extractor and fast page renderer for OCR
    import pymupdf as fitz  # type: ignore[import-not-found]
except ImportError:  # older PyMuPDF releases only expose the "fitz" name
    try:
        import fitz  # type: ignore[import-not-found,no-redef]
    except ImportError:
        fitz = None  # type: ignore[assignment]

try:  # OCR is optional
    import pytesseract  # type: ignore[import-not-found]
except ImportError:
    pytesseract = None  # type: ignore[assignment]

LOG = logging.getLogger(LOGGER_NAME)

KIND_CONSOLIDATED = "consolidated"
KIND_STANDALONE = "standalone"
KIND_UNSPECIFIED = "unspecified"
_KIND_PRIORITY = {KIND_CONSOLIDATED: 0, KIND_UNSPECIFIED: 1, KIND_STANDALONE: 2}
_HEADING_LINE_MAX_CHARS = 160
_ANCHOR_RE = re.compile(r"(?:as\s+at|as\s+of|as\s+on|ended|ending)\b", re.IGNORECASE)


def _phrase_pattern(phrase: str) -> "re.Pattern[str]":
    """Pattern for a phrase that tolerates missing or extra spaces between words."""
    words = phrase.lower().replace("&", " and ").split()
    return re.compile(r"\s*".join(re.escape(word) for word in words), re.IGNORECASE)


_HEADING_PATTERNS = [_phrase_pattern(h) for h in sorted(BALANCE_SHEET_HEADINGS, key=len, reverse=True)]
_REQUIRED_PATTERNS = [_phrase_pattern(p) for p in BALANCE_SHEET_REQUIRED_PHRASES]
_ANY_OF_PATTERNS = [_phrase_pattern(p) for p in BALANCE_SHEET_ANY_OF_PHRASES]
_FIRST_ROW_RE = re.compile(
    r"^\W*(?:[ivxa-d1-9]{1,4}[.)]\s*)?(?:"
    + "|".join(re.escape(row) for row in sorted(BALANCE_SHEET_FIRST_ROWS, key=len, reverse=True))
    + r")\b\W*$",
    re.IGNORECASE,
)


@dataclass
class BalanceSheetHit:
    """A page that passed the Balance Sheet test."""

    page_number: int               # 1-based
    kind: str
    ped: Optional[date]
    via_ocr: bool = False


@dataclass
class PeriodEndResult:
    """Result of Phase 2 for one PDF."""

    ped: date
    page_number: int
    kind: str
    via_ocr: bool


class _PdfTextSource:
    """Page text from pdfplumber, falling back to PyMuPDF, with optional OCR."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._plumber: Any = None
        self._fitz_doc: Any = None
        self._cache: Dict[int, str] = {}
        self._ocr_ready: Optional[bool] = None

        errors: List[str] = []
        try:
            self._plumber = pdfplumber.open(str(path))
            self.page_count = len(self._plumber.pages)
        except Exception as exc:  # corrupt or encrypted file
            errors.append(f"pdfplumber: {exc or type(exc).__name__}")
            self._plumber = None
        if fitz is not None:
            try:
                self._fitz_doc = fitz.open(str(path))
                if self._plumber is None:
                    self.page_count = self._fitz_doc.page_count
            except Exception as exc:
                errors.append(f"PyMuPDF: {exc or type(exc).__name__}")
                self._fitz_doc = None
        if self._plumber is None and self._fitz_doc is None:
            raise PdfUnreadableError(f"{path.name}: {'; '.join(errors) or 'cannot be opened'}")

    def __enter__(self) -> "_PdfTextSource":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        for handle in (self._plumber, self._fitz_doc):
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass
        self._plumber = None
        self._fitz_doc = None

    # ------------------------------------------------------------------ #
    def page_text(self, index: int) -> str:
        """Text of a page (0-based): pdfplumber first, PyMuPDF when that gives nothing."""
        if index in self._cache:
            return self._cache[index]
        text = ""
        if self._plumber is not None:
            try:
                page = self._plumber.pages[index]
                text = page.extract_text() or ""
                page.flush_cache()
            except Exception as exc:
                LOG.debug("pdfplumber failed on page %d of %s: %s", index + 1, self.path.name, exc)
        if len(text.strip()) < MIN_TEXT_CHARS_PER_PAGE and self._fitz_doc is not None:
            try:
                text = self._fitz_doc.load_page(index).get_text("text") or text
            except Exception as exc:
                LOG.debug("PyMuPDF failed on page %d of %s: %s", index + 1, self.path.name, exc)
        self._cache[index] = text
        for stale in [key for key in self._cache if key < index - 1]:
            del self._cache[stale]
        return text

    # ------------------------------------------------------------------ #
    @property
    def ocr_available(self) -> bool:
        """True when pytesseract and the Tesseract program are both installed."""
        if self._ocr_ready is None:
            if pytesseract is None:
                self._ocr_ready = False
            else:
                try:
                    pytesseract.get_tesseract_version()
                    self._ocr_ready = True
                except Exception:
                    self._ocr_ready = False
        return self._ocr_ready

    def page_ocr(self, index: int) -> str:
        """OCR text of a page (0-based); "" when the page cannot be rendered."""
        try:
            if self._fitz_doc is not None:
                from PIL import Image

                pixmap = self._fitz_doc.load_page(index).get_pixmap(dpi=OCR_DPI, alpha=False)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            elif self._plumber is not None:
                image = self._plumber.pages[index].to_image(resolution=OCR_DPI).original
            else:
                return ""
            return pytesseract.image_to_string(image) or ""
        except Exception as exc:
            LOG.debug("OCR failed on page %d of %s: %s", index + 1, self.path.name, exc)
            return ""


class BalanceSheetParser:
    """Phase 2: read the Balance Sheet period end date out of a report PDF."""

    def __init__(self, today: Optional[date] = None) -> None:
        self.today = today

    # ------------------------------------------------------------------ #
    def extract_period_end(self, pdf_path: Path) -> PeriodEndResult:
        """Return the period end date of the (preferably consolidated) Balance Sheet.

        Raises PdfUnreadableError, NoTextError, NoBalanceSheetError or NoDateError.
        """
        with _PdfTextSource(pdf_path) as source:
            hits: List[BalanceSheetHit] = []
            textless: List[int] = []
            pages_with_text = 0

            for index in range(source.page_count):
                text = source.page_text(index)
                if len(text.strip()) < MIN_TEXT_CHARS_PER_PAGE:
                    textless.append(index)
                    continue
                pages_with_text += 1
                hit = self._inspect(text, lambda i=index: self._next_text(source, i), index + 1)
                if hit is not None:
                    hits.append(hit)
                    if hit.kind == KIND_CONSOLIDATED and hit.ped is not None:
                        break  # best possible answer; no need to read further

            best = self._choose(hits)
            if best is None and textless:
                if source.ocr_available:
                    LOG.info("   🔍 %d page(s) without text in %s; trying OCR", len(textless), pdf_path.name)
                    hits.extend(self._ocr_pass(source, textless))
                    best = self._choose(hits)
                elif pages_with_text == 0:
                    raise NoTextError(f"{pdf_path.name}: no extractable text and OCR is not available")

            if best is not None and best.ped is not None:
                return PeriodEndResult(best.ped, best.page_number, best.kind, best.via_ocr)
            if hits:
                pages = ", ".join(str(h.page_number) for h in hits[:5])
                raise NoDateError(f"{pdf_path.name}: Balance Sheet found on page {pages} but no period end date")
            if pages_with_text == 0 and textless:
                raise NoTextError(f"{pdf_path.name}: no readable text, even with OCR")
            raise NoBalanceSheetError(f"{pdf_path.name}: no Balance Sheet page in {source.page_count} page(s)")

    @staticmethod
    def _next_text(source: _PdfTextSource, index: int) -> str:
        return source.page_text(index + 1) if index + 1 < source.page_count else ""

    def _ocr_pass(self, source: _PdfTextSource, pages: List[int]) -> List[BalanceSheetHit]:
        """Run the Balance Sheet test on OCR text of the pages that had no text."""
        selected = pages[:OCR_MAX_PAGES]
        allowed = set(selected)
        texts: Dict[int, str] = {}

        def ocr(index: int) -> str:
            if index not in allowed:
                return ""
            if index not in texts:
                texts[index] = source.page_ocr(index)
            return texts[index]

        hits: List[BalanceSheetHit] = []
        for index in selected:
            hit = self._inspect(ocr(index), lambda i=index: ocr(i + 1), index + 1)
            if hit is not None:
                hit.via_ocr = True
                hits.append(hit)
                if hit.kind == KIND_CONSOLIDATED and hit.ped is not None:
                    break
        return hits

    @staticmethod
    def _choose(hits: List[BalanceSheetHit]) -> Optional[BalanceSheetHit]:
        """Consolidated first, then unlabelled, then standalone; earliest page wins."""
        dated = [h for h in hits if h.ped is not None]
        if not dated:
            return None
        return min(dated, key=lambda h: (_KIND_PRIORITY[h.kind], h.page_number))

    # ------------------------------------------------------------------ #
    def _inspect(self, text: str, following_text: Callable[[], str], page_number: int) -> Optional[BalanceSheetHit]:
        """Decide whether a page is a Balance Sheet and, if so, read its date."""
        lines = [re.sub(r"\s+", " ", line).strip().replace("&", "and") for line in text.splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            return None
        joined = " ".join(lines)

        # Table-of-contents and index pages mention the heading but have no totals.
        if not all(pattern.search(joined) for pattern in _REQUIRED_PATTERNS):
            return None
        heading_index = self._heading_line(lines)
        if heading_index is None:
            return None
        if not any(pattern.search(joined) for pattern in _ANY_OF_PATTERNS):
            following = re.sub(r"\s+", " ", following_text()).replace("&", "and")
            if not any(pattern.search(following) for pattern in _ANY_OF_PATTERNS):
                return None

        kind = self._kind(lines, heading_index)
        ped = self._period_end(lines, heading_index)
        LOG.debug("Balance Sheet candidate on page %d (%s), date %s", page_number, kind, ped)
        return BalanceSheetHit(page_number, kind, ped)

    @staticmethod
    def _heading_line(lines: List[str]) -> Optional[int]:
        """Index of the heading line; a short, title-like line is preferred."""
        matches = [i for i, line in enumerate(lines) if any(p.search(line) for p in _HEADING_PATTERNS)]
        if not matches:
            return None
        short = [i for i in matches if len(lines[i]) <= _HEADING_LINE_MAX_CHARS]
        return short[0] if short else matches[0]

    @staticmethod
    def _kind(lines: List[str], heading_index: int) -> str:
        def classify(text: str) -> Optional[str]:
            lowered = text.lower()
            if any(marker in lowered for marker in CONSOLIDATED_MARKERS):
                return KIND_CONSOLIDATED
            if any(marker in lowered for marker in STANDALONE_MARKERS):
                return KIND_STANDALONE
            return None

        own = classify(lines[heading_index])
        if own:
            return own
        nearby = " ".join(lines[max(0, heading_index - 3): heading_index + 3] + lines[:4])
        return classify(nearby) or KIND_UNSPECIFIED

    def _period_end(self, lines: List[str], heading_index: int) -> Optional[date]:
        """Latest date in the heading and column headers of a Balance Sheet page."""
        # Zone 1: the heading and the column-header lines right below it.
        end = min(len(lines), heading_index + 1 + HEADER_ZONE_MAX_LINES)
        for i in range(heading_index + 1, end):
            if _FIRST_ROW_RE.match(lines[i]):
                end = i
                break
        found = latest_period_date(" ".join(lines[heading_index:end]), self.today)
        if found:
            return found

        # Zone 2: everything above the first "Total assets" line (page header included).
        total_line = next(
            (i for i, line in enumerate(lines) if all(p.search(line) for p in _REQUIRED_PATTERNS)), len(lines)
        )
        found = latest_period_date(" ".join(lines[:total_line]), self.today)
        if found:
            return found

        # Zone 3: anywhere on the page, but only dates introduced by "as at", "ended", ...
        # (signature blocks at the foot of the page carry later, unrelated dates).
        anchored: List[date] = []
        joined = " ".join(lines)
        for match in _ANCHOR_RE.finditer(joined):
            candidate = latest_period_date(joined[match.end(): match.end() + 40], self.today)
            if candidate:
                anchored.append(candidate)
        return max(anchored) if anchored else None
