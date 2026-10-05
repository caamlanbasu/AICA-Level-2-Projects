"""Optional JavaScript rendering with Playwright.

Playwright is imported lazily. If the package or its Chromium browser is not
installed the renderer reports itself as unavailable and the tool carries on
with static HTML only.
"""

from __future__ import annotations

import importlib.util
import logging
from typing import Any, Optional, Tuple

from .config import LOGGER_NAME, USER_AGENT

LOG = logging.getLogger(LOGGER_NAME)

RENDER_TIMEOUT_MS = 30_000
SETTLE_MS = 1_500


class JsRenderer:
    """Renders a page in headless Chromium and returns the resulting HTML.

    All calls must come from the same thread (the worker thread), which is
    how Playwright's synchronous API has to be used.
    """

    def __init__(self) -> None:
        self._installed = importlib.util.find_spec("playwright") is not None
        self._playwright: Any = None
        self._browser: Any = None
        self._failed = False

    @property
    def available(self) -> bool:
        """False once Playwright is known to be missing or unusable."""
        return self._installed and not self._failed

    def _ensure_browser(self) -> bool:
        if self._browser is not None:
            return True
        if not self.available:
            return False
        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            LOG.info("🧩 Playwright started for JavaScript-rendered pages")
            return True
        except Exception as exc:  # missing browser binaries, sandbox issues, ...
            self._failed = True
            LOG.warning(
                "Playwright is installed but could not start (%s). "
                "Run 'playwright install chromium' to enable JavaScript rendering.",
                str(exc).splitlines()[0] if str(exc) else type(exc).__name__,
            )
            self.close()
            return False

    def render(self, url: str) -> Optional[Tuple[str, str]]:
        """Return ``(final_url, html)`` after scripts have run, or ``None`` on failure."""
        if not self._ensure_browser():
            return None
        page = None
        try:
            page = self._browser.new_page(user_agent=USER_AGENT)
            try:
                page.goto(url, wait_until="networkidle", timeout=RENDER_TIMEOUT_MS)
            except Exception:
                # Some sites never go idle; use whatever has loaded so far.
                LOG.debug("Playwright: %s did not reach network idle", url)
            page.wait_for_timeout(SETTLE_MS)
            return page.url, page.content()
        except Exception as exc:
            LOG.debug("Playwright could not render %s: %s", url, exc)
            return None
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def close(self) -> None:
        """Shut the browser down. Safe to call more than once."""
        for closer in (getattr(self._browser, "close", None), getattr(self._playwright, "stop", None)):
            if closer is not None:
                try:
                    closer()
                except Exception:
                    pass
        self._browser = None
        self._playwright = None
