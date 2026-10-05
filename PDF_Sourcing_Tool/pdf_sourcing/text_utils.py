"""URL handling, keyword matching and file-name helpers shared by all modules."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Dict, List, Mapping, Optional, Tuple
from urllib.parse import unquote, urldefrag, urlsplit, urlunsplit

import tldextract

# Use the public-suffix snapshot bundled with tldextract: no network call on
# first use, and identical behaviour inside a PyInstaller build.
_TLD_EXTRACT = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=())

_URL_LIKE_RE = re.compile(
    r"^(?:https?://)?(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,24}(?::\d+)?(?:[/?#]\S*)?$",
    re.IGNORECASE,
)
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)
_PDF_URL_RE = re.compile(r"\.pdf(?:$|[?#&])", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


# --------------------------------------------------------------------------- #
# URLs
# --------------------------------------------------------------------------- #
def looks_like_url(value: object) -> bool:
    """True when a cell value / user entry has the shape of a web address."""
    if value is None:
        return False
    text = str(value).strip()
    if not text or any(ch.isspace() for ch in text):
        return False
    if text.lower().startswith(("http://", "https://")):
        return normalise_url(text) is not None      # also covers IP addresses and localhost
    return bool(_URL_LIKE_RE.match(text))


def normalise_url(raw: object) -> Optional[str]:
    """Strip whitespace, add ``https://`` when missing and drop the fragment.

    Returns ``None`` for blanks and for anything that is not an http(s) address.
    """
    if raw is None:
        return None
    text = str(raw).strip().strip("\"'<>")
    if not text or any(ch.isspace() for ch in text):
        return None
    if not _SCHEME_RE.match(text):
        text = "https://" + text.lstrip("/")
    try:
        parts = urlsplit(text)
        _ = parts.port  # raises ValueError for a malformed port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname
    if parts.username is not None or not re.fullmatch(r"[a-z0-9.\-]+", host):
        return None      # "mailto:a@b.com", e-mail addresses, stray symbols
    if "." not in host and host != "localhost":
        return None
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def url_key(url: str) -> str:
    """Key used to detect duplicates: ignores scheme, ``www.`` and a trailing slash."""
    parts = urlsplit(url)
    host = (parts.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    key = host + parts.path.rstrip("/")
    return key + ("?" + parts.query if parts.query else "")


def canonical(url: str) -> str:
    """Canonical form used for visited-page bookkeeping (keeps scheme and host)."""
    clean, _ = urldefrag(url)
    parts = urlsplit(clean)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))


def host_of(url: str) -> str:
    """Lower-case host name of a URL, without port."""
    return (urlsplit(url).hostname or "").lower()


def registered_domain(host_or_url: str) -> str:
    """Registered domain of a host ("ir.example.co.uk" -> "example.co.uk").

    Hosts without a public suffix (IP addresses, localhost) are returned as-is.
    """
    host = host_of(host_or_url) if "://" in host_or_url else host_or_url.lower()
    extracted = _TLD_EXTRACT(host)
    if extracted.domain and extracted.suffix:
        return f"{extracted.domain}.{extracted.suffix}".lower()
    return host


def is_pdf_url(url: str) -> bool:
    """True when the URL path (or a query value) ends in ``.pdf``."""
    return bool(_PDF_URL_RE.search(url))


def url_tokens(url: str) -> str:
    """Host and path of a URL as normalised words, for keyword matching."""
    parts = urlsplit(url)
    return normalise_text(f"{parts.hostname or ''} {unquote(parts.path)}")


def file_name_from_url(url: str) -> str:
    """Last path segment of a URL, decoded ("" when there is none)."""
    path = unquote(urlsplit(url).path)
    return path.rstrip("/").rsplit("/", 1)[-1] if path else ""


# --------------------------------------------------------------------------- #
# Keyword matching
# --------------------------------------------------------------------------- #
def normalise_text(value: str) -> str:
    """Lower-case, turn "&" into "and" and every other symbol into one space."""
    text = (value or "").lower().replace("&", " and ")
    return _NON_ALNUM_RE.sub(" ", text).strip()


@lru_cache(maxsize=None)
def _keyword_pattern(keyword: str) -> "re.Pattern[str]":
    """Whole-word pattern for a keyword; the last word may carry a plural "s"."""
    words = normalise_text(keyword).split()
    body = r"\s+".join(re.escape(word) for word in words)
    plural = "" if len(words[-1]) <= 3 else r"(?:s|es)?"
    return re.compile(rf"(?<![a-z0-9]){body}{plural}(?![a-z0-9])")


def keyword_hits(normalised: str, weights: Mapping[str, int]) -> List[Tuple[str, int]]:
    """All keywords from ``weights`` found in already-normalised text."""
    return [(kw, weight) for kw, weight in weights.items() if _keyword_pattern(kw).search(normalised)]


def best_keyword_weight(normalised: str, weights: Mapping[str, int]) -> int:
    """Highest weight among the matching keywords (0 when nothing matches)."""
    hits = keyword_hits(normalised, weights)
    return max((weight for _, weight in hits), default=0)


def document_type_score(normalised: str, positive: Dict[str, int], negative: Dict[str, int]) -> int:
    """Strongest positive keyword plus strongest negative keyword."""
    best = max((w for _, w in keyword_hits(normalised, positive)), default=0)
    worst = min((w for _, w in keyword_hits(normalised, negative)), default=0)
    return best + worst


# --------------------------------------------------------------------------- #
# File names
# --------------------------------------------------------------------------- #
def sanitise_filename(name: str, max_length: int = 90) -> str:
    """Make a string safe to use as a file name on Windows, macOS and Linux."""
    cleaned = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._- ")
    cleaned = cleaned[:max_length].rstrip("._- ")
    if not cleaned:
        cleaned = "report"
    if cleaned.split(".")[0].upper() in _WINDOWS_RESERVED:
        cleaned = "_" + cleaned
    return cleaned
