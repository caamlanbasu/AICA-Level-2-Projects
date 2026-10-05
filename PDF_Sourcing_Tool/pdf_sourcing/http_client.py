"""Polite HTTP access: one Session with retries, backoff, a delay and robots.txt checks."""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import (
    LOGGER_NAME,
    MAX_HTML_BYTES,
    MAX_RETRIES,
    REQUEST_DELAY_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_BACKOFF_FACTOR,
    RETRY_STATUS_CODES,
    USER_AGENT,
)

LOG = logging.getLogger(LOGGER_NAME)


class RobotsDisallowedError(Exception):
    """robots.txt does not allow this URL."""


@dataclass
class FetchResult:
    """Outcome of fetching a page."""

    url: str                       # final URL after redirects
    status: int
    content_type: str
    body: bytes = b""
    encoding: Optional[str] = None  # charset declared in the HTTP header, if any
    is_pdf: bool = False

    @property
    def is_html(self) -> bool:
        if self.is_pdf:
            return False
        if "html" in self.content_type or "xml" in self.content_type:
            return True
        return not self.content_type and b"<" in self.body[:2048]


class HttpClient:
    """Thin wrapper around ``requests.Session`` used for every network call."""

    def __init__(self) -> None:
        self.session = requests.Session()
        retry = Retry(
            total=MAX_RETRIES,
            connect=MAX_RETRIES,
            read=MAX_RETRIES,
            status=MAX_RETRIES,
            backoff_factor=RETRY_BACKOFF_FACTOR,
            status_forcelist=RETRY_STATUS_CODES,
            allowed_methods=frozenset({"GET", "HEAD"}),
            respect_retry_after_header=False,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self._robots: Dict[str, RobotFileParser] = {}
        self._resolves: Dict[str, bool] = {}
        self._last_request_at = 0.0

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """Release pooled connections."""
        self.session.close()

    def _pause(self) -> None:
        """Keep at least REQUEST_DELAY_SECONDS between two requests."""
        wait = REQUEST_DELAY_SECONDS - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def host_resolves(self, host: str) -> bool:
        """Cheap DNS check so guessed subdomains do not burn three retries each."""
        if host not in self._resolves:
            try:
                socket.getaddrinfo(host, None)
                self._resolves[host] = True
            except OSError:
                self._resolves[host] = False
        return self._resolves[host]

    # ------------------------------------------------------------------ #
    # robots.txt
    # ------------------------------------------------------------------ #
    def _robots_for(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        cached = self._robots.get(base)
        if cached is not None:
            return cached

        parser = RobotFileParser()
        try:
            self._pause()
            response = self.session.get(f"{base}/robots.txt", timeout=REQUEST_TIMEOUT_SECONDS)
            content_type = response.headers.get("Content-Type", "").lower()
            if response.status_code == 200 and "html" not in content_type:
                parser.parse(response.text.splitlines())
            else:
                parser.allow_all = True      # no usable robots.txt: nothing to restrict
        except requests.RequestException as exc:
            LOG.debug("robots.txt unavailable for %s (%s); assuming allowed", base, exc)
            parser.allow_all = True
        self._robots[base] = parser
        return parser

    def allowed(self, url: str) -> bool:
        """True when robots.txt permits fetching ``url`` with our User-Agent."""
        try:
            return self._robots_for(url).can_fetch(USER_AGENT, url)
        except Exception:  # a malformed robots.txt must never stop the run
            LOG.debug("robots.txt check failed for %s; assuming allowed", url, exc_info=True)
            return True

    def _check_robots(self, url: str) -> None:
        if not self.allowed(url):
            raise RobotsDisallowedError(f"robots.txt disallows {url}")

    # ------------------------------------------------------------------ #
    # Requests
    # ------------------------------------------------------------------ #
    def fetch(self, url: str) -> FetchResult:
        """GET a page. The body of a PDF response is not downloaded.

        Raises ``RobotsDisallowedError`` or ``requests.RequestException``.
        """
        self._check_robots(url)
        self._pause()
        response = self.session.get(url, timeout=REQUEST_TIMEOUT_SECONDS, stream=True, allow_redirects=True)
        try:
            content_type = response.headers.get("Content-Type", "").lower()
            result = FetchResult(url=response.url, status=response.status_code, content_type=content_type)
            if "application/pdf" in content_type:
                result.is_pdf = True
                return result
            chunks = []
            size = 0
            for chunk in response.iter_content(65536):
                chunks.append(chunk)
                size += len(chunk)
                if size >= MAX_HTML_BYTES:
                    break
            result.body = b"".join(chunks)
            if "charset=" in content_type:
                result.encoding = response.encoding
            return result
        finally:
            response.close()

    def head(self, url: str) -> Optional[requests.Response]:
        """HEAD a URL. Returns ``None`` on any failure or robots.txt block."""
        try:
            self._check_robots(url)
            self._pause()
            response = self.session.head(url, timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=True)
            response.close()
            return response
        except (requests.RequestException, RobotsDisallowedError) as exc:
            LOG.debug("HEAD %s failed: %s", url, exc)
            return None

    def open_stream(self, url: str) -> requests.Response:
        """GET with a streamed body for downloads. The caller must close the response."""
        self._check_robots(url)
        self._pause()
        return self.session.get(url, timeout=REQUEST_TIMEOUT_SECONDS, stream=True, allow_redirects=True)
