"""Shared data classes, error types and worker-to-UI events."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import List, Optional


# --------------------------------------------------------------------------- #
# Errors: each carries the specific reason written to the log file
# --------------------------------------------------------------------------- #
class SourcingError(Exception):
    """Base class for every expected failure while processing one URL."""

    reason: str = "error"


class SiteUnreachableError(SourcingError):
    reason = "site unreachable"


class NoInvestorPageError(SourcingError):
    reason = "no investor page"


class NoPdfError(SourcingError):
    reason = "no PDF"


class DownloadFailedError(SourcingError):
    reason = "PDF download failed"


class PdfUnreadableError(SourcingError):
    reason = "PDF could not be opened"


class NoTextError(SourcingError):
    reason = "no extractable text"


class NoBalanceSheetError(SourcingError):
    reason = "no balance sheet"


class NoDateError(SourcingError):
    reason = "no date"


class ExcelFormatError(Exception):
    """The workbook cannot be used as a URL list (shown to the user as-is)."""


# --------------------------------------------------------------------------- #
# Phase 1 data
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DateGuess:
    """A date inferred from text. precision: 3 = full date, 2 = month, 1 = year."""

    value: date
    precision: int


@dataclass
class PdfCandidate:
    """One PDF link found on a company site, with the context used to rank it."""

    url: str
    link_text: str = ""
    row_text: str = ""            # text of the surrounding table row / list item
    heading_text: str = ""        # nearest heading above the link
    page_url: str = ""            # page the link was found on
    section_text: str = ""        # text of the link that led to that page
    # Filled in by ReportSelector:
    date_guess: Optional[DateGuess] = None
    date_source: str = ""         # "link text", "row/heading", "file name/URL", "Last-Modified"
    doc_score: float = 0.0
    tier: int = 1                 # 2 financial statements, 1 neutral, 0 presentation/notice/...


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
class JobState(Enum):
    PENDING = "pending"
    DOWNLOADED = "downloaded"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    """One unique URL to process. ``rows`` lists the Excel rows that hold it."""

    index: int
    url: str
    rows: List[int] = field(default_factory=list)
    state: JobState = JobState.PENDING
    pdf_paths: List[Path] = field(default_factory=list)
    ped: Optional[date] = None
    failure_reason: str = ""


# --------------------------------------------------------------------------- #
# Events sent from the worker thread to the UI through a queue
# --------------------------------------------------------------------------- #
@dataclass
class LogEvent:
    text: str
    level: str = "INFO"


@dataclass
class RowEvent:
    index: int
    status: str
    ped: str
    comments: str
    tag: str                      # "pending" | "working" | "ok" | "fail" | "cancelled"


@dataclass
class ProgressEvent:
    phase: str
    done: int
    total: int
    overall: float                # 0.0 - 1.0 across both phases


@dataclass
class SaveBlockedEvent:
    """The workbook is open in Excel. The UI answers through ``retry`` + ``answered``."""

    path: Path
    answered: threading.Event = field(default_factory=threading.Event)
    retry: bool = False


@dataclass
class FinishedEvent:
    succeeded: int
    failed: int
    cancelled: int
    stopped: bool
    log_path: Optional[Path] = None
    excel_saved_to: Optional[Path] = None
    excel_error: str = ""
