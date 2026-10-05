"""Site crawler: finds the investor section of a company site and its report PDFs."""

from __future__ import annotations

import heapq
import logging
from dataclasses import dataclass
from itertools import count
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from .config import (
    ALLOW_OFFSITE_PDF_HOSTS,
    DOWNLOAD_HREF_HINTS,
    INVESTOR_KEYWORDS,
    INVESTOR_PATHS,
    INVESTOR_SUBDOMAINS,
    LOGGER_NAME,
    MAX_CONTENT_TYPE_PROBES,
    MAX_INVESTOR_PAGES,
    MAX_PAGES_PER_SITE,
    MAX_SUBLINKS_PER_PAGE,
    MAX_SUBPAGE_DEPTH,
    MIN_REAL_PAGE_TEXT_CHARS,
    NON_PAGE_EXTENSIONS,
    REPORT_SECTION_KEYWORDS,
    SOFT_404_MARKERS,
)
from .http_client import FetchResult, HttpClient, RobotsDisallowedError
from .models import NoInvestorPageError, NoPdfError, PdfCandidate, SiteUnreachableError
from .renderer import JsRenderer
from .text_utils import (
    best_keyword_weight,
    canonical,
    host_of,
    is_pdf_url,
    normalise_text,
    registered_domain,
    url_tokens,
)

LOG = logging.getLogger(LOGGER_NAME)

_PRIORITY_REGIONS = ["nav", "header", "footer"]
_HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
_ROW_TEXT_LIMIT = 260
_SKIPPED_SCHEMES = ("mailto:", "tel:", "javascript:", "data:", "#")


@dataclass
class Link:
    """An anchor on a page."""

    url: str
    text: str
    in_priority_region: bool
    tag: Tag


@dataclass
class Page:
    """A fetched (or rendered) HTML page."""

    url: str
    soup: BeautifulSoup
    rendered: bool = False

    @property
    def visible_text_length(self) -> int:
        return len(self.soup.get_text(" ", strip=True))

    @property
    def title(self) -> str:
        return self.soup.title.get_text(" ", strip=True) if self.soup.title else ""


class DomainScope:
    """The company's own registered domain(s); subdomains are always in scope."""

    def __init__(self, *urls: str) -> None:
        self.domains: Set[str] = {registered_domain(u) for u in urls if u}
        self.label: str = registered_domain(urls[-1])

    def contains(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return False
        return registered_domain(parts.hostname) in self.domains


class SiteCrawler:
    """Phase 1 crawler.

    ``find_report_candidates`` returns every PDF link found in the investor
    section of a site. Ranking and downloading are done by other classes.
    """

    def __init__(self, http: HttpClient, renderer: Optional[JsRenderer] = None) -> None:
        self.http = http
        self.renderer = renderer

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def find_report_candidates(self, start_url: str) -> Tuple[str, List[PdfCandidate]]:
        """Return ``(domain, candidates)`` for a company URL.

        Raises SiteUnreachableError, NoInvestorPageError or NoPdfError.
        """
        home = self._fetch_home(start_url)
        scope = DomainScope(start_url, home.url)
        LOG.debug("Home page %s (domain %s)", home.url, scope.label)

        investor_pages = self._find_investor_pages(home, scope)
        if not investor_pages:
            raise NoInvestorPageError(f"no investor section found on {home.url}")
        LOG.info("   🧭 Investor page: %s", investor_pages[0].url)

        candidates = self._collect_pdfs(investor_pages, scope)
        if not candidates:
            raise NoPdfError(f"no PDF links found in the investor section of {home.url}")
        LOG.info("   📎 %d PDF link(s) found", len(candidates))
        return scope.label, candidates

    # ------------------------------------------------------------------ #
    # Fetching
    # ------------------------------------------------------------------ #
    def _fetch_home(self, start_url: str) -> Page:
        """Fetch the start page, trying a ``www.`` and an ``http://`` variant if needed."""
        parts = urlsplit(start_url)
        variants = [start_url]
        host = parts.netloc
        if not host.startswith("www.") and registered_domain(host.split(":")[0]) == host:
            variants.append(urlunsplit((parts.scheme, "www." + host, parts.path, parts.query, "")))
        if parts.scheme == "https":
            variants.append(urlunsplit(("http", host, parts.path, parts.query, "")))

        last_problem = "no response"
        for variant in variants:
            try:
                result = self.http.fetch(variant)
            except RobotsDisallowedError as exc:
                last_problem = str(exc)
                continue
            except requests.RequestException as exc:
                last_problem = f"{type(exc).__name__}: {exc}"
                LOG.debug("Could not fetch %s: %s", variant, last_problem)
                continue
            if result.status >= 400:
                last_problem = f"HTTP {result.status}"
                continue
            if not result.is_html:
                last_problem = f"not an HTML page ({result.content_type or 'unknown type'})"
                continue
            return Page(result.url, self._parse(result))
        raise SiteUnreachableError(f"{start_url}: {last_problem}")

    @staticmethod
    def _parse(result: FetchResult) -> BeautifulSoup:
        return BeautifulSoup(result.body, "lxml", from_encoding=result.encoding)

    def _fetch(self, url: str, scope: DomainScope) -> Optional[FetchResult]:
        """Fetch an in-scope URL; ``None`` on any failure, redirect off-site or non-200."""
        try:
            result = self.http.fetch(url)
        except RobotsDisallowedError as exc:
            LOG.debug("%s", exc)
            return None
        except requests.RequestException as exc:
            LOG.debug("Could not fetch %s: %s", url, exc)
            return None
        if result.status != 200:
            LOG.debug("HTTP %s for %s", result.status, url)
            return None
        if not result.is_pdf and not scope.contains(result.url):
            LOG.debug("%s redirected off-site to %s; ignored", url, result.url)
            return None
        return result

    def _fetch_page(self, url: str, scope: DomainScope) -> Optional[Page]:
        result = self._fetch(url, scope)
        if result is None or not result.is_html:
            return None
        return Page(result.url, self._parse(result))

    def _render(self, url: str, scope: DomainScope) -> Optional[Page]:
        """Render a page with Playwright (when installed and allowed by robots.txt)."""
        if self.renderer is None or not self.renderer.available or not self.http.allowed(url):
            return None
        LOG.debug("Static HTML of %s has no useful links; rendering with Playwright", url)
        rendered = self.renderer.render(url)
        if rendered is None:
            return None
        final_url, html = rendered
        if not scope.contains(final_url):
            return None
        return Page(final_url, BeautifulSoup(html, "lxml"), rendered=True)

    # ------------------------------------------------------------------ #
    # Links
    # ------------------------------------------------------------------ #
    @staticmethod
    def _anchor_text(anchor: Tag) -> str:
        parts = [anchor.get_text(" ", strip=True)]
        for attribute in ("title", "aria-label"):
            value = anchor.get(attribute)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        for image in anchor.find_all("img", alt=True):
            parts.append(str(image.get("alt", "")).strip())
        seen: List[str] = []
        for part in parts:
            if part and part not in seen:
                seen.append(part)
        return " ".join(seen)

    def _links(self, page: Page) -> List[Link]:
        """All usable anchors on a page: nav/header/footer links first, then the rest."""
        priority: List[Link] = []
        others: List[Link] = []
        for anchor in page.soup.find_all("a", href=True):
            href = str(anchor.get("href", "")).strip()
            if not href or href.lower().startswith(_SKIPPED_SCHEMES):
                continue
            try:
                absolute = urljoin(page.url, href)
            except ValueError:
                continue
            absolute = absolute.split("#", 1)[0]
            if urlsplit(absolute).scheme not in ("http", "https"):
                continue
            in_region = anchor.find_parent(_PRIORITY_REGIONS) is not None
            link = Link(absolute, self._anchor_text(anchor), in_region, anchor)
            (priority if in_region else others).append(link)
        return priority + others

    @staticmethod
    def _link_weight(link: Link, weights: Dict[str, int]) -> int:
        """Best keyword weight in the link text or, failing that, in its URL."""
        return max(
            best_keyword_weight(normalise_text(link.text), weights),
            best_keyword_weight(url_tokens(link.url), weights),
        )

    @staticmethod
    def _is_page_url(url: str) -> bool:
        path = urlsplit(url).path.lower()
        return not is_pdf_url(url) and not path.endswith(NON_PAGE_EXTENSIONS)

    # ------------------------------------------------------------------ #
    # Step 2-3: investor page
    # ------------------------------------------------------------------ #
    def _investor_links(self, page: Page, scope: DomainScope) -> List[Link]:
        """Investor links on a page, nav/header/footer matches ahead of all others."""
        best: Dict[str, Tuple[int, int, Link]] = {}
        own = canonical(page.url)
        for link in self._links(page):
            if not self._is_page_url(link.url) or canonical(link.url) == own:
                continue
            weight = self._link_weight(link, INVESTOR_KEYWORDS)
            if weight <= 0:
                continue
            if not scope.contains(link.url):
                LOG.debug("Investor link %s is on another domain; skipped", link.url)
                continue
            key = canonical(link.url)
            rank = (1 if link.in_priority_region else 0, weight)
            if key not in best or rank > best[key][:2]:
                best[key] = (rank[0], rank[1], link)
        ordered = sorted(best.values(), key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in ordered]

    def _looks_real(self, page: Page, home: Page) -> bool:
        """A guessed URL counts only if it is a real page, not a soft 404 or the home page."""
        if canonical(page.url) == canonical(home.url):
            return False
        if urlsplit(page.url).path in ("", "/") and host_of(page.url) == host_of(home.url):
            return False
        if page.visible_text_length < MIN_REAL_PAGE_TEXT_CHARS:
            return False
        heading = page.soup.find("h1")
        banner = f"{page.title} {heading.get_text(' ', strip=True) if heading else ''}".lower()
        return not any(marker in banner for marker in SOFT_404_MARKERS)

    def _find_investor_pages(self, home: Page, scope: DomainScope) -> List[Page]:
        pages: List[Page] = []

        # The URL the user gave may already be the investor / reports page.
        start_tokens = url_tokens(home.url)
        if urlsplit(home.url).path not in ("", "/") and (
            best_keyword_weight(start_tokens, INVESTOR_KEYWORDS)
            or best_keyword_weight(start_tokens, REPORT_SECTION_KEYWORDS)
        ):
            pages.append(home)

        links = self._investor_links(home, scope)
        if not links and not pages:
            rendered = self._render(home.url, scope)
            if rendered is not None:
                links = self._investor_links(rendered, scope)
        for link in links[:MAX_INVESTOR_PAGES]:
            page = self._fetch_page(link.url, scope)
            if page is not None:
                LOG.debug("Investor link '%s' -> %s", link.text[:60], page.url)
                pages.append(page)
        if pages:
            return pages

        root = urlsplit(home.url)
        base = f"{root.scheme}://{root.netloc}"
        for path in INVESTOR_PATHS:
            page = self._fetch_page(base + path, scope)
            if page is not None and self._looks_real(page, home):
                LOG.debug("Investor page found at common path %s", page.url)
                return [page]

        for prefix in INVESTOR_SUBDOMAINS:
            host = f"{prefix}.{scope.label}"
            if host == root.hostname or not self.http.host_resolves(host):
                continue
            page = self._fetch_page(f"https://{host}/", scope)
            if page is not None and page.visible_text_length >= MIN_REAL_PAGE_TEXT_CHARS:
                LOG.debug("Investor page found at subdomain %s", page.url)
                return [page]
        return []

    # ------------------------------------------------------------------ #
    # Step 4-5: report sections and PDF links
    # ------------------------------------------------------------------ #
    @staticmethod
    def _context(anchor: Tag) -> Tuple[str, str]:
        """Text of the row / list item around a link and of the nearest heading above it."""
        row_text = ""
        node = anchor.parent
        for _ in range(5):
            if node is None or not isinstance(node, Tag) or node.name in ("body", "html"):
                break
            text = node.get_text(" ", strip=True)
            if len(text) > _ROW_TEXT_LIMIT:
                break
            row_text = text
            if node.name in ("tr", "li", "article"):
                break
            node = node.parent
        heading = anchor.find_previous(_HEADINGS)
        heading_text = heading.get_text(" ", strip=True)[:200] if heading else ""
        return row_text, heading_text

    def _scan_page(
        self, page: Page, scope: DomainScope, section: str, probes_left: List[int]
    ) -> Tuple[List[PdfCandidate], List[Tuple[int, Link]]]:
        """PDF links on a page plus the report-section links worth following."""
        pdfs: List[PdfCandidate] = []
        sections: Dict[str, Tuple[int, Link]] = {}
        own = canonical(page.url)

        for link in self._links(page):
            in_scope = scope.contains(link.url)
            if is_pdf_url(link.url):
                if in_scope or ALLOW_OFFSITE_PDF_HOSTS:
                    pdfs.append(self._candidate(link, page, section))
                continue
            if not in_scope or not self._is_page_url(link.url) or canonical(link.url) == own:
                continue

            weight = self._link_weight(link, REPORT_SECTION_KEYWORDS)
            href = link.url.lower()
            if weight > 0 and probes_left[0] > 0 and any(hint in href for hint in DOWNLOAD_HREF_HINTS):
                # No .pdf ending, but it looks like a file download: ask for its Content-Type.
                probes_left[0] -= 1
                response = self.http.head(link.url)
                if response is not None and "application/pdf" in response.headers.get("Content-Type", "").lower():
                    pdfs.append(self._candidate(link, page, section))
                    continue
            if weight > 0:
                key = canonical(link.url)
                if key not in sections or weight > sections[key][0]:
                    sections[key] = (weight, link)

        ordered = sorted(sections.values(), key=lambda item: item[0], reverse=True)
        return pdfs, ordered[:MAX_SUBLINKS_PER_PAGE]

    def _candidate(self, link: Link, page: Page, section: str) -> PdfCandidate:
        row_text, heading_text = self._context(link.tag)
        return PdfCandidate(
            url=link.url,
            link_text=link.text,
            row_text=row_text,
            heading_text=heading_text,
            page_url=page.url,
            section_text=section or page.title,
        )

    def _collect_pdfs(self, investor_pages: List[Page], scope: DomainScope) -> List[PdfCandidate]:
        """Scan the investor page(s) and up to MAX_SUBPAGE_DEPTH levels of report sub-pages."""
        found: Dict[str, PdfCandidate] = {}
        visited: Set[str] = {canonical(page.url) for page in investor_pages}
        order = count()
        # Heap entries: (depth, -weight, tie-breaker, url, already-fetched page, section text)
        heap: List[Tuple[int, int, int, str, Optional[Page], str]] = [
            (0, 0, next(order), page.url, page, "") for page in investor_pages
        ]
        heapq.heapify(heap)
        fetched = 0
        probes_left = [MAX_CONTENT_TYPE_PROBES]

        while heap:
            depth, _, _, url, page, section = heapq.heappop(heap)
            if page is None:
                if fetched >= MAX_PAGES_PER_SITE:
                    LOG.debug("Page budget of %d reached; remaining sub-pages skipped", MAX_PAGES_PER_SITE)
                    break
                fetched += 1
                result = self._fetch(url, scope)
                if result is None:
                    continue
                if result.is_pdf:
                    # A section link that turned out to be a PDF (Content-Type application/pdf).
                    if scope.contains(result.url) or ALLOW_OFFSITE_PDF_HOSTS:
                        found.setdefault(canonical(result.url), PdfCandidate(
                            url=result.url, link_text=section, page_url=url, section_text=section))
                    continue
                if not result.is_html:
                    continue
                page = Page(result.url, self._parse(result))
                final_key = canonical(page.url)
                if final_key != canonical(url):
                    if final_key in visited:
                        continue
                    visited.add(final_key)

            pdfs, sections = self._scan_page(page, scope, section, probes_left)
            if not pdfs and not sections and not page.rendered:
                rendered = self._render(page.url, scope)
                if rendered is not None:
                    pdfs, sections = self._scan_page(rendered, scope, section, probes_left)
            LOG.debug("Scanned %s (depth %d): %d PDF link(s), %d section link(s)",
                      page.url, depth, len(pdfs), len(sections))

            for candidate in pdfs:
                key = canonical(candidate.url)
                existing = found.get(key)
                # Keep the occurrence with the richest context.
                if existing is None or len(candidate.link_text + candidate.row_text) > len(
                    existing.link_text + existing.row_text
                ):
                    found[key] = candidate

            if depth < MAX_SUBPAGE_DEPTH:
                for weight, link in sections:
                    key = canonical(link.url)
                    if key in visited:
                        continue
                    visited.add(key)
                    heapq.heappush(heap, (depth + 1, -weight, next(order), link.url, None, link.text))

        return list(found.values())
