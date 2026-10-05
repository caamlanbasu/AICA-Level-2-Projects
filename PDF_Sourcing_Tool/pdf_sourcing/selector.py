"""Report selector: ranks PDF links to find the latest report, then downloads it."""

from __future__ import annotations

import logging
import re
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import unquote, urlsplit

import requests

from .config import (
    DOWNLOAD_CHUNK_BYTES,
    FINANCIAL_DOC_KEYWORDS,
    FINANCIAL_DOC_MIN_SCORE,
    LOGGER_NAME,
    MAX_LAST_MODIFIED_PROBES,
    MAX_PDF_BYTES,
    NON_FINANCIAL_DOC_KEYWORDS,
)
from .dates import infer_document_date
from .http_client import HttpClient, RobotsDisallowedError
from .models import DateGuess, DownloadFailedError, PdfCandidate
from .text_utils import document_type_score, file_name_from_url, normalise_text, sanitise_filename

LOG = logging.getLogger(LOGGER_NAME)

_SOURCE_ORDER = {"link text": 4, "row/heading": 3, "file name/URL": 2, "Last-Modified": 1, "": 0}
_CONTEXT_WEIGHT = 0.5
_FILENAME_RE = re.compile(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", re.IGNORECASE)


class ReportSelector:
    """Ranks PDF candidates so that the latest full financial report comes first."""

    def __init__(self, http: HttpClient, today: Optional[date] = None) -> None:
        self.http = http
        self.today = today or date.today()

    # ------------------------------------------------------------------ #
    def rank(self, candidates: List[PdfCandidate]) -> List[PdfCandidate]:
        """Return the candidates best-first.

        Documents that look like full financial statements beat neutral ones,
        which beat presentations, press releases and notices. Within a group
        the most recent date wins; the date comes from the first of: link
        text, surrounding row or heading, file name or URL, Last-Modified.
        """
        for candidate in candidates:
            self._score_type(candidate)
            self._date_from_text(candidate)

        # Last-Modified is the last resort and costs a request, so it is only
        # asked for the most promising undated documents.
        undated = sorted((c for c in candidates if c.date_guess is None), key=lambda c: (c.tier, c.doc_score), reverse=True)
        for candidate in undated[:MAX_LAST_MODIFIED_PROBES]:
            self._date_from_last_modified(candidate)

        return sorted(candidates, key=self._sort_key, reverse=True)

    @staticmethod
    def _sort_key(candidate: PdfCandidate) -> Tuple[int, int, int, int, float]:
        guess = candidate.date_guess
        return (
            candidate.tier,
            guess.value.toordinal() if guess else 0,
            guess.precision if guess else 0,
            _SOURCE_ORDER.get(candidate.date_source, 0),
            candidate.doc_score,
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _score_type(candidate: PdfCandidate) -> None:
        def type_score(text: str) -> float:
            return document_type_score(normalise_text(text), FINANCIAL_DOC_KEYWORDS, NON_FINANCIAL_DOC_KEYWORDS)

        # The link's own words decide. A generic link ("Download", an icon) is
        # described by its table row instead. Headings count for half.
        score = type_score(f"{candidate.link_text} {file_name_from_url(candidate.url)}")
        if score == 0:
            score = type_score(candidate.row_text)
        score += _CONTEXT_WEIGHT * type_score(f"{candidate.heading_text} {candidate.section_text}")
        candidate.doc_score = score
        if score >= FINANCIAL_DOC_MIN_SCORE:
            candidate.tier = 2
        elif score < 0:
            candidate.tier = 0
        else:
            candidate.tier = 1

    def _date_from_text(self, candidate: PdfCandidate) -> None:
        url_path = unquote(urlsplit(candidate.url).path)
        sources = (
            ("link text", candidate.link_text, False),
            ("row/heading", f"{candidate.row_text} | {candidate.heading_text}", False),
            ("file name/URL", file_name_from_url(candidate.url), True),
            ("file name/URL", url_path, True),
        )
        for label, text, is_file_name in sources:
            guess = infer_document_date(text, self.today, is_file_name=is_file_name)
            if guess is not None:
                candidate.date_guess = guess
                candidate.date_source = label
                return

    def _date_from_last_modified(self, candidate: PdfCandidate) -> None:
        response = self.http.head(candidate.url)
        if response is None:
            return
        header = response.headers.get("Last-Modified")
        if not header:
            return
        try:
            modified = parsedate_to_datetime(header).date()
        except (TypeError, ValueError):
            return
        candidate.date_guess = DateGuess(modified, 3)
        candidate.date_source = "Last-Modified"


class ReportDownloader:
    """Streams a PDF to disk as ``<domain>_<date or year>_<original name>.pdf``."""

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    @staticmethod
    def _date_label(guess: Optional[DateGuess]) -> str:
        if guess is None:
            return "undated"
        return guess.value.isoformat() if guess.precision >= 2 else str(guess.value.year)

    @staticmethod
    def _original_name(candidate: PdfCandidate, response: requests.Response) -> str:
        disposition = response.headers.get("Content-Disposition", "")
        match = _FILENAME_RE.search(disposition)
        name = unquote(match.group(1)).strip() if match else file_name_from_url(response.url)
        name = name or file_name_from_url(candidate.url) or "report"
        return re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE)

    def download(self, candidate: PdfCandidate, domain: str, output_dir: Path) -> Path:
        """Download one candidate. Raises DownloadFailedError if it is not a real PDF."""
        try:
            response = self.http.open_stream(candidate.url)
        except RobotsDisallowedError as exc:
            raise DownloadFailedError(str(exc)) from exc
        except requests.RequestException as exc:
            raise DownloadFailedError(f"{candidate.url}: {type(exc).__name__}: {exc}") from exc

        part_path: Optional[Path] = None
        try:
            if response.status_code != 200:
                raise DownloadFailedError(f"{candidate.url}: HTTP {response.status_code}")

            file_name = "_".join(
                (
                    sanitise_filename(domain, 60),
                    self._date_label(candidate.date_guess),
                    sanitise_filename(self._original_name(candidate, response)),
                )
            ) + ".pdf"
            target = output_dir / file_name
            part_path = target.with_name(target.name + ".part")

            chunks = response.iter_content(DOWNLOAD_CHUNK_BYTES)
            head = b""
            for chunk in chunks:
                head += chunk
                if len(head) >= 1024:
                    break
            if b"%PDF" not in head[:1024]:
                raise DownloadFailedError(f"{candidate.url}: response is not a PDF (no %PDF signature)")

            size = len(head)
            with open(part_path, "wb") as handle:
                handle.write(head)
                for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > MAX_PDF_BYTES:
                        raise DownloadFailedError(f"{candidate.url}: larger than {MAX_PDF_BYTES // (1024 * 1024)} MB")
                    handle.write(chunk)
            part_path.replace(target)
            LOG.debug("Saved %s (%.1f MB)", target.name, size / (1024 * 1024))
            return target
        except (requests.RequestException, OSError) as exc:
            raise DownloadFailedError(f"{candidate.url}: {type(exc).__name__}: {exc}") from exc
        finally:
            response.close()
            if part_path is not None and part_path.exists():
                try:
                    part_path.unlink()
                except OSError:
                    pass
