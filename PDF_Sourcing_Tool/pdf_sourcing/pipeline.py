"""Background worker: Phase 1 for every URL, then Phase 2, then the Excel update.

The worker never touches the UI. It reports through a ``queue.Queue`` of
events (see ``models``) that the UI drains on its own thread.
"""

from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .config import (
    APP_NAME,
    APP_VERSION,
    LOG_FILE_PREFIX,
    LOGGER_NAME,
    MAX_DOWNLOAD_ATTEMPTS,
    NOT_FOUND_TEXT,
    REPORTS_PER_SITE,
)
from .crawler import SiteCrawler
from .dates import format_ped
from .excel_io import ExcelUrlList, RowResult, UrlRow
from .http_client import HttpClient
from .models import (
    DownloadFailedError,
    FinishedEvent,
    Job,
    JobState,
    LogEvent,
    ProgressEvent,
    RowEvent,
    SaveBlockedEvent,
    SourcingError,
)
from .pdf_parser import KIND_UNSPECIFIED, BalanceSheetParser, PeriodEndResult
from .renderer import JsRenderer
from .selector import ReportDownloader, ReportSelector
from .text_utils import url_key

LOG = logging.getLogger(LOGGER_NAME)

STATUS_QUEUED = "⏳ Queued"
STATUS_SEARCHING = "🔎 Finding report…"
STATUS_DOWNLOADED = "📥 Report downloaded"
STATUS_READING = "📄 Reading PDF…"
STATUS_DONE = "✅ Done"
STATUS_FAILED = "❌ Failed"
STATUS_CANCELLED = "⏹ Cancelled"

PHASE_ONE = "Phase 1: finding reports"
PHASE_TWO = "Phase 2: reading dates"


def build_jobs(rows: Sequence[UrlRow]) -> List[Job]:
    """One job per unique URL; duplicate rows share the job and get the same result."""
    jobs: Dict[str, Job] = {}
    for item in rows:
        key = url_key(item.url)
        if key not in jobs:
            jobs[key] = Job(index=len(jobs), url=item.url)
        if item.row > 0:
            jobs[key].rows.append(item.row)
    return list(jobs.values())


class _QueueLogHandler(logging.Handler):
    """Forwards log records to the UI queue."""

    def __init__(self, events: "queue.Queue[object]") -> None:
        super().__init__(level=logging.INFO)
        self.events = events

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
            self.events.put(LogEvent(f"{stamp}  {record.getMessage()}", record.levelname))
        except Exception:  # logging must never raise
            self.handleError(record)


class SourcingWorker(threading.Thread):
    """Runs a whole batch in the background."""

    def __init__(
        self,
        jobs: List[Job],
        output_dir: Path,
        events: "queue.Queue[object]",
        stop_event: threading.Event,
        excel: Optional[ExcelUrlList] = None,
    ) -> None:
        super().__init__(name="pdf-sourcing-worker", daemon=True)
        self.jobs = jobs
        self.output_dir = Path(output_dir)
        self.events = events
        self.stop_event = stop_event
        self.excel = excel
        self._handlers: List[logging.Handler] = []
        self._log_path: Optional[Path] = None
        self._phase_two_done = 0

    # ------------------------------------------------------------------ #
    # Logging
    # ------------------------------------------------------------------ #
    def _start_logging(self) -> None:
        LOG.setLevel(logging.DEBUG)
        LOG.propagate = False
        for handler in list(LOG.handlers):
            LOG.removeHandler(handler)
        queue_handler = _QueueLogHandler(self.events)
        LOG.addHandler(queue_handler)
        self._handlers.append(queue_handler)
        try:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._log_path = self.output_dir / f"{LOG_FILE_PREFIX}_{stamp}.log"
            file_handler = logging.FileHandler(self._log_path, encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s"))
            LOG.addHandler(file_handler)
            self._handlers.append(file_handler)
        except OSError as exc:
            self._log_path = None
            LOG.warning("Could not create the log file in %s: %s", self.output_dir, exc)

    def _stop_logging(self) -> None:
        for handler in self._handlers:
            LOG.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        self._handlers.clear()

    # ------------------------------------------------------------------ #
    # UI events
    # ------------------------------------------------------------------ #
    def _row(self, job: Job, status: str, tag: str, ped: str = "", comments: str = "") -> None:
        self.events.put(RowEvent(job.index, status, ped, comments, tag))

    def _progress(self, phase: str, done: int, total: int, phase_one_done: int) -> None:
        steps = 2 * len(self.jobs) or 1
        overall = (phase_one_done + self._phase_two_done) / steps
        self.events.put(ProgressEvent(phase, done, total, min(1.0, overall)))

    def _fail(self, job: Job, reason: str, detail: str) -> None:
        """Record a failure: specific reason to the log, fixed text to the user."""
        job.state = JobState.FAILED
        job.failure_reason = reason
        LOG.error("❌ %s | reason: %s | %s", job.url, reason, detail)
        if self.excel is None:
            self._row(job, STATUS_FAILED, "fail", ped=NOT_FOUND_TEXT)
        else:
            self._row(job, STATUS_FAILED, "fail", comments=NOT_FOUND_TEXT)

    # ------------------------------------------------------------------ #
    # Thread body
    # ------------------------------------------------------------------ #
    def run(self) -> None:
        self._start_logging()
        http = HttpClient()
        renderer = JsRenderer()
        saved_to: Optional[Path] = None
        excel_error = ""
        try:
            LOG.info("🚀 %s %s: %d URL(s), output folder %s", APP_NAME, APP_VERSION, len(self.jobs), self.output_dir)
            if not renderer.available:
                LOG.info("ℹ️ Playwright is not installed; JavaScript-only pages are read as static HTML")
            self._phase_one(http, renderer)
            renderer.close()
            if not self.stop_event.is_set():
                self._phase_two()
            self._mark_cancelled()
            saved_to, excel_error = self._write_excel()
        except Exception as exc:  # last line of defence: the UI must always get FinishedEvent
            LOG.exception("Unexpected error; the run was stopped: %s", exc)
            excel_error = excel_error or f"Unexpected error: {exc}"
        finally:
            renderer.close()
            http.close()
            succeeded = sum(1 for j in self.jobs if j.state is JobState.DONE)
            failed = sum(1 for j in self.jobs if j.state is JobState.FAILED)
            cancelled = sum(1 for j in self.jobs if j.state is JobState.CANCELLED)
            LOG.info("🏁 Finished: %d with a date, %d not found, %d cancelled", succeeded, failed, cancelled)
            self._stop_logging()
            self.events.put(
                FinishedEvent(
                    succeeded=succeeded,
                    failed=failed,
                    cancelled=cancelled,
                    stopped=self.stop_event.is_set(),
                    log_path=self._log_path,
                    excel_saved_to=saved_to,
                    excel_error=excel_error,
                )
            )

    # ------------------------------------------------------------------ #
    # Phase 1
    # ------------------------------------------------------------------ #
    def _phase_one(self, http: HttpClient, renderer: JsRenderer) -> None:
        crawler = SiteCrawler(http, renderer)
        selector = ReportSelector(http)
        downloader = ReportDownloader(http)
        total = len(self.jobs)
        LOG.info("━━ %s (%d URL(s)) ━━", PHASE_ONE, total)
        self._progress(PHASE_ONE, 0, total, 0)

        for number, job in enumerate(self.jobs, start=1):
            if self.stop_event.is_set():
                LOG.info("⏹ Stop requested; remaining URLs skipped")
                break
            LOG.info("🔎 [%d/%d] %s", number, total, job.url)
            self._row(job, STATUS_SEARCHING, "working")
            try:
                job.pdf_paths = self._source_report(job, crawler, selector, downloader)
                job.state = JobState.DOWNLOADED
                self._row(job, STATUS_DOWNLOADED, "working")
            except SourcingError as exc:
                self._fail(job, exc.reason, str(exc))
                self._phase_two_done += 1
            except Exception as exc:
                LOG.debug("Unexpected error for %s", job.url, exc_info=True)
                self._fail(job, "unexpected error", f"{type(exc).__name__}: {exc}")
                self._phase_two_done += 1
            self._progress(PHASE_ONE, number, total, number)

    def _source_report(
        self, job: Job, crawler: SiteCrawler, selector: ReportSelector, downloader: ReportDownloader
    ) -> List[Path]:
        """Find, rank and download the latest report for one URL."""
        domain, candidates = crawler.find_report_candidates(job.url)
        ranked = selector.rank(candidates)
        for position, candidate in enumerate(ranked[:5], start=1):
            LOG.debug(
                "Rank %d: %s | tier %d | date %s (%s) | '%s'",
                position, candidate.url, candidate.tier,
                candidate.date_guess.value.isoformat() if candidate.date_guess else "none",
                candidate.date_source or "-", candidate.link_text[:80],
            )

        paths: List[Path] = []
        last_error: Optional[DownloadFailedError] = None
        max_attempts = REPORTS_PER_SITE + MAX_DOWNLOAD_ATTEMPTS - 1
        for candidate in ranked[:max_attempts]:
            if len(paths) >= REPORTS_PER_SITE:
                break
            try:
                path = downloader.download(candidate, domain, self.output_dir)
            except DownloadFailedError as exc:
                last_error = exc
                LOG.debug("Download failed, trying the next-ranked PDF: %s", exc)
                continue
            paths.append(path)
            LOG.info("   📥 Downloaded %s", path.name)
        if not paths:
            raise last_error or DownloadFailedError(f"{job.url}: no PDF could be downloaded")
        return paths

    # ------------------------------------------------------------------ #
    # Phase 2
    # ------------------------------------------------------------------ #
    def _phase_two(self) -> None:
        parser = BalanceSheetParser()
        pending = [job for job in self.jobs if job.state is JobState.DOWNLOADED]
        total = len(pending)
        phase_one_done = len(self.jobs)
        LOG.info("━━ %s (%d report(s)) ━━", PHASE_TWO, total)
        self._progress(PHASE_TWO, 0, total, phase_one_done)

        for number, job in enumerate(pending, start=1):
            if self.stop_event.is_set():
                LOG.info("⏹ Stop requested; remaining PDFs skipped")
                break
            self._row(job, STATUS_READING, "working")
            try:
                result = self._read_period_end(job, parser)
                job.ped = result.ped
                job.state = JobState.DONE
                text = format_ped(result.ped)
                kind = "" if result.kind == KIND_UNSPECIFIED else f"{result.kind} "
                LOG.info(
                    "✅ %s → %s (%sBalance Sheet, page %d%s)",
                    job.url, text, kind, result.page_number, ", read with OCR" if result.via_ocr else "",
                )
                self._row(job, STATUS_DONE, "ok", ped=text)
            except SourcingError as exc:
                self._fail(job, exc.reason, str(exc))
            except Exception as exc:
                LOG.debug("Unexpected error for %s", job.url, exc_info=True)
                self._fail(job, "unexpected error", f"{type(exc).__name__}: {exc}")
            self._phase_two_done += 1
            self._progress(PHASE_TWO, number, total, phase_one_done)

    @staticmethod
    def _read_period_end(job: Job, parser: BalanceSheetParser) -> PeriodEndResult:
        """Read the downloaded PDF(s) in rank order; the first with a date wins."""
        last_error: Optional[SourcingError] = None
        for path in job.pdf_paths:
            LOG.info("📄 Reading %s", path.name)
            try:
                return parser.extract_period_end(path)
            except SourcingError as exc:
                last_error = exc
                if len(job.pdf_paths) > 1:
                    LOG.debug("%s: %s (%s)", path.name, exc.reason, exc)
        assert last_error is not None  # pdf_paths is never empty for a downloaded job
        raise last_error

    def _mark_cancelled(self) -> None:
        for job in self.jobs:
            if job.state in (JobState.PENDING, JobState.DOWNLOADED):
                job.state = JobState.CANCELLED
                self._row(job, STATUS_CANCELLED, "cancelled")

    # ------------------------------------------------------------------ #
    # Excel
    # ------------------------------------------------------------------ #
    def _write_excel(self) -> Tuple[Optional[Path], str]:
        """Write finished rows back to the workbook. Returns ``(saved path, error text)``."""
        if self.excel is None:
            return None, ""
        results: Dict[int, RowResult] = {}
        for job in self.jobs:
            if job.state is JobState.DONE and job.ped is not None:
                result = RowResult(job.url, format_ped(job.ped), None)
            elif job.state is JobState.FAILED:
                result = RowResult(job.url, None, NOT_FOUND_TEXT)
            else:
                continue  # cancelled rows are left exactly as they were
            for row in job.rows:
                results[row] = result
        if not results:
            LOG.info("💾 Nothing to write to the workbook")
            return None, ""

        path = self.excel.path
        while True:
            try:
                self.excel.apply_results(results)
                self.excel.save()
                LOG.info("💾 Workbook updated: %s", path)
                return path, ""
            except PermissionError:
                LOG.warning("⚠️ %s is open in another program; waiting for it to be closed", path.name)
                blocked = SaveBlockedEvent(path)
                self.events.put(blocked)
                blocked.answered.wait()
                if blocked.retry:
                    continue
                return self._save_backup_copy(path)
            except Exception as exc:
                LOG.exception("The workbook could not be updated: %s", exc)
                return None, f"The workbook could not be updated: {exc}"

    def _save_backup_copy(self, original: Path) -> Tuple[Optional[Path], str]:
        """The user gave up on Retry: keep the results in a copy inside the output folder."""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = self.output_dir / f"{original.stem}_results_{stamp}.xlsx"
        try:
            self.excel.save_copy(backup)  # type: ignore[union-attr]
            LOG.warning("💾 Original workbook left untouched; results saved to %s", backup)
            return backup, ""
        except Exception as exc:
            LOG.error("Results could not be saved to a copy either: %s", exc)
            return None, f"The workbook was locked and a copy could not be saved: {exc}"
