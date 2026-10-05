"""Central configuration for PDF Sourcing Tool.

Every keyword list, limit and file name the tool relies on lives here so it can
be extended without touching the logic. Keyword tables map a phrase to a weight:
a higher weight means a stronger match. Matching is case-insensitive, ignores
punctuation, treats "&" as "and", and accepts a plural "s" on the last word.
"""

from __future__ import annotations

from typing import Dict, Final, Tuple

# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #
APP_NAME: Final[str] = "PDF Sourcing Tool"
APP_VERSION: Final[str] = "1.0.2"
LOGGER_NAME: Final[str] = "pdf_sourcing"
LOG_FILE_PREFIX: Final[str] = "pdf_sourcing_log"

# --------------------------------------------------------------------------- #
# HTTP behaviour
# --------------------------------------------------------------------------- #
USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT_SECONDS: Final[int] = 20
MAX_RETRIES: Final[int] = 3
RETRY_BACKOFF_FACTOR: Final[float] = 1.0          # waits 1s, 2s, 4s between retries
RETRY_STATUS_CODES: Final[Tuple[int, ...]] = (429, 500, 502, 503, 504)
REQUEST_DELAY_SECONDS: Final[float] = 0.75        # pause between any two requests
MAX_HTML_BYTES: Final[int] = 6 * 1024 * 1024
MAX_PDF_BYTES: Final[int] = 250 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES: Final[int] = 64 * 1024

# --------------------------------------------------------------------------- #
# Crawl limits
# --------------------------------------------------------------------------- #
MAX_INVESTOR_PAGES: Final[int] = 3                # investor links followed from the home page
MAX_SUBPAGE_DEPTH: Final[int] = 2                 # levels of report sub-pages below the investor page
MAX_PAGES_PER_SITE: Final[int] = 18               # sub-pages fetched per company
MAX_SUBLINKS_PER_PAGE: Final[int] = 8             # best report-section links followed per page
MAX_CONTENT_TYPE_PROBES: Final[int] = 10          # HEAD checks for links without a .pdf ending
MAX_LAST_MODIFIED_PROBES: Final[int] = 8          # HEAD checks for PDFs with no visible date
MIN_REAL_PAGE_TEXT_CHARS: Final[int] = 200        # below this a guessed page is not "real content"

# PDFs are often stored on a CDN (for example q4cdn.com). Pages are only ever
# crawled on the company's own domain and its subdomains; this switch decides
# whether a PDF *linked from* those pages may be downloaded from another host.
ALLOW_OFFSITE_PDF_HOSTS: Final[bool] = True

# Phase 1 downloads this many of the top-ranked reports per company. Phase 2
# reads them in rank order and stops at the first one that yields a date.
# 1 = download only the top-ranked PDF.
REPORTS_PER_SITE: Final[int] = 1
MAX_DOWNLOAD_ATTEMPTS: Final[int] = 3             # next-ranked PDFs tried if a download fails

# --------------------------------------------------------------------------- #
# Phase 1 keywords
# --------------------------------------------------------------------------- #
INVESTOR_KEYWORDS: Final[Dict[str, int]] = {
    "investor relations": 10,
    "investors": 9,
    "investor": 9,
    "shareholder": 7,
    "stockholder": 7,
    "financial information": 6,
    "financials": 6,
    "ir": 5,                                      # whole word / whole path segment only
}

INVESTOR_PATHS: Final[Tuple[str, ...]] = (
    "/investors",
    "/investor-relations",
    "/investor",
    "/ir",
    "/en/investors",
    "/about/investors",
    "/about-us/investors",
    "/corporate/investors",
)

INVESTOR_SUBDOMAINS: Final[Tuple[str, ...]] = ("ir", "investors", "investor")

REPORT_SECTION_KEYWORDS: Final[Dict[str, int]] = {
    "financial reports": 10,
    "annual reports": 10,
    "annual report": 10,
    "financial results": 10,
    "financial statements": 10,
    "reports and presentations": 8,
    "results and reports": 9,
    "quarterly results": 9,
    "filings": 6,
    # Close variants seen on real sites.
    "financial report": 10,
    "financial statement": 10,
    "financial result": 10,
    "quarterly result": 9,
    "quarterly reports": 8,
    "financial information": 7,
    "financials": 6,
    "results": 5,
    "reports": 5,
}

# Page titles / headings that mean a guessed URL is a "soft 404".
SOFT_404_MARKERS: Final[Tuple[str, ...]] = (
    "404", "page not found", "not found", "page cannot be found",
    "page can't be found", "no longer available", "error",
)

# Links without a .pdf ending are only probed (HEAD) when the href hints at a file.
DOWNLOAD_HREF_HINTS: Final[Tuple[str, ...]] = (
    "download", "document", "attachment", "file", "media", "asset",
    "static-files", "getfile", "blob", "upload",
)

# Extensions that are never pages worth crawling.
NON_PAGE_EXTENSIONS: Final[Tuple[str, ...]] = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".zip", ".rar",
    ".xls", ".xlsx", ".xlsm", ".csv", ".doc", ".docx", ".ppt", ".pptx", ".mp3",
    ".mp4", ".wav", ".avi", ".mov", ".xml", ".json", ".rss", ".ics", ".css", ".js",
)

# --------------------------------------------------------------------------- #
# Report ranking keywords
# --------------------------------------------------------------------------- #
# Documents likely to contain full financial statements.
FINANCIAL_DOC_KEYWORDS: Final[Dict[str, int]] = {
    "annual report": 10,
    "annual financial report": 10,
    "integrated annual report": 10,
    "financial statements": 10,
    "financial statement": 10,
    "financial results": 9,
    "financial result": 9,
    "financial report": 9,
    "annual accounts": 9,
    "report and accounts": 9,
    "balance sheet": 9,
    "10-k": 9,
    "20-f": 8,
    "integrated report": 8,
    "audited": 7,
    "10-q": 7,
    "quarterly report": 7,
    "quarterly results": 7,
    "interim report": 7,
    "half year": 6,
    "half yearly": 6,
    "interim": 5,
    "results": 5,
    "consolidated": 4,
    "standalone": 4,
    "accounts": 3,
}

# Documents that are not financial statements.
NON_FINANCIAL_DOC_KEYWORDS: Final[Dict[str, int]] = {
    "presentation": -8,
    "press release": -8,
    "news release": -8,
    "media release": -8,
    "notice": -8,
    "transcript": -8,
    "earnings call": -8,
    "conference call": -8,
    "newspaper": -8,
    "advertisement": -8,
    "shareholding pattern": -8,
    "postal ballot": -8,
    "voting": -8,
    "intimation": -7,
    "policy": -7,
    "policies": -7,
    "code of conduct": -7,
    "articles of association": -7,
    "corporate governance": -6,
    "sustainability": -6,
    "esg": -6,
    "csr": -6,
    "proxy": -6,
    "fact sheet": -6,
    "factsheet": -6,
    "brochure": -6,
    "annual return": -6,
    "credit rating": -6,
    "unclaimed": -6,
    "agm": -5,
    "egm": -5,
    "annual general meeting": -5,
    "dividend": -5,
    "prospectus": -5,
    "memorandum": -5,
    "subsidiary": -4,
    "subsidiaries": -4,
}

FINANCIAL_DOC_MIN_SCORE: Final[int] = 5           # at or above: treated as full financial statements

# --------------------------------------------------------------------------- #
# Phase 2 keywords
# --------------------------------------------------------------------------- #
BALANCE_SHEET_HEADINGS: Final[Tuple[str, ...]] = (
    "Balance Sheet",
    "Balance Sheets",
    "Consolidated Balance Sheet",
    "Consolidated Balance Sheets",
    "Standalone Balance Sheet",
    "Statement of Financial Position",
    "Statements of Financial Position",
    "Consolidated Statement of Financial Position",
    "Statement of Assets and Liabilities",
)

# A page only counts as the Balance Sheet if it contains all of these ...
BALANCE_SHEET_REQUIRED_PHRASES: Final[Tuple[str, ...]] = ("Total assets",)
# ... and at least one of these (on the same page or on the page that continues it).
BALANCE_SHEET_ANY_OF_PHRASES: Final[Tuple[str, ...]] = (
    "Total liabilities",
    "Total equity",
    "Equity and liabilities",
)

CONSOLIDATED_MARKERS: Final[Tuple[str, ...]] = ("consolidated",)
STANDALONE_MARKERS: Final[Tuple[str, ...]] = (
    "standalone", "stand-alone", "unconsolidated", "separate", "parent company", "company only",
)

# The first table rows of a Balance Sheet: the column headers end before them.
BALANCE_SHEET_FIRST_ROWS: Final[Tuple[str, ...]] = (
    "assets", "non-current assets", "non current assets", "current assets",
    "equity and liabilities", "liabilities", "fixed assets",
)

HEADER_ZONE_MAX_LINES: Final[int] = 14            # lines read below the heading for column headers
MIN_TEXT_CHARS_PER_PAGE: Final[int] = 25          # fewer characters = page has no extractable text
OCR_DPI: Final[int] = 200
OCR_MAX_PAGES: Final[int] = 80                    # OCR is slow; cap the pages read per PDF
EARLIEST_REPORT_YEAR: Final[int] = 1995

# --------------------------------------------------------------------------- #
# Excel
# --------------------------------------------------------------------------- #
URL_HEADER_KEYWORDS: Final[Tuple[str, ...]] = ("url", "link", "website")
PED_HEADER: Final[str] = "Latest PED"
COMMENTS_HEADER: Final[str] = "Comments"
COMMENTS_HEADER_ALIASES: Final[Tuple[str, ...]] = ("comments", "comment")
NEW_URL_HEADER: Final[str] = "URL"                # written when a list has no header row
HEADER_SCAN_ROWS: Final[int] = 15
PED_COLUMN_WIDTH: Final[float] = 16.0
COMMENTS_COLUMN_WIDTH: Final[float] = 24.0

# --------------------------------------------------------------------------- #
# User-facing text
# --------------------------------------------------------------------------- #
NOT_FOUND_TEXT: Final[str] = "Reports not found"
PED_OUTPUT_MONTHS: Final[Tuple[str, ...]] = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
