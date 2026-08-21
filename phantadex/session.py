"""Chrome DevTools Protocol attachment and course-tab management."""

import os

from playwright.sync_api import sync_playwright

from . import interaction, logs, urls

log = logs.get_logger("session")


def quiet_node_driver():
    """Silences the Node driver's own deprecation warnings.

    Set before Playwright starts, and only when the caller has not already
    chosen its own ``NODE_OPTIONS``.
    """
    os.environ.setdefault("NODE_OPTIONS", "--no-deprecation")


class BrowserSession:
    """Attaches to a running Chrome over CDP and tracks the active course tab.

    Used as a context manager so Playwright is always stopped, including on the
    error paths the old inline ``sync_playwright()`` block leaked through.
    """

    def __init__(self, cdp_url):
        self.cdp_url = cdp_url
        self._playwright = None
        self.browser = None
        self.context = None

    def __enter__(self):
        quiet_node_driver()
        self._playwright = sync_playwright().start()
        logs.step(f"Phantadex Link · connecting to Chrome on {self.cdp_url}")
        try:
            self.browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
        except Exception as exc:
            self._playwright.stop()
            self._playwright = None
            raise ConnectionError(self._diagnose(exc)) from exc
        if not self.browser.contexts:
            # Playwright owns a client-side transport even when Chrome has no
            # usable context; stop it before surfacing the actionable error.
            self._disconnect_browser()
            self._playwright.stop()
            self._playwright = None
            raise RuntimeError("Connected to Chrome but it has no browser context open.")
        self.context = self.browser.contexts[0]
        self._guard_media()
        return self

    def _guard_media(self):
        """Mutes platform media pre-emptively, in this tab and in later ones."""
        interaction.install_media_guard(self.context)
        for page in self.context.pages:
            try:
                if urls.PLATFORM_HOST in page.url:
                    interaction.arm_media_guard(page)
            except Exception as exc:
                log.debug("Could not arm the media guard on a tab: %s", exc)

    def __exit__(self, exc_type, exc, traceback):
        self._disconnect_browser()
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception as stop_exc:
                log.debug("Playwright shutdown failed: %s", stop_exc)
            self._playwright = None
        return False

    def _disconnect_browser(self):
        """Disconnects the CDP client without closing the external Chrome process."""
        if self.browser is None:
            return
        close = getattr(self.browser, "close", None)
        try:
            if close is not None:
                close(reason="Phantadex disconnected")
        except Exception as exc:
            log.debug("Browser disconnect failed: %s", exc)
        finally:
            self.browser = None
            self.context = None

    def _diagnose(self, exc):
        """Turns an opaque CDP failure into an actionable message."""
        detail = str(exc)
        if "Browser context management is not supported" in detail:
            return (
                f"{self.cdp_url} answered, but refused browser-level control. "
                "Usually the debug port is held by something other than a full Chrome "
                "target (a relay, extension bridge, or another automation tool), or the "
                "Chrome that owns it is mid-restart. Check with "
                "`lsof -nP -iTCP:9222 -sTCP:LISTEN`; if it is not Chrome, close it and "
                "relaunch with `python3 scripts/start_chrome_debug.py`. If it is Chrome, "
                "retry once -- this can be transient."
            )
        if "ECONNREFUSED" in detail or "connect ECONNREFUSED" in detail:
            return (
                f"Nothing is listening on {self.cdp_url}. Start Chrome with "
                "`python3 scripts/start_chrome_debug.py` first."
            )
        return f"Could not attach to Chrome at {self.cdp_url}: {detail}"

    def find_course_page(self):
        """Returns the frontmost supported course tab, or ``None``."""
        for page in self.context.pages:
            try:
                if urls.PLATFORM_HOST in page.url:
                    page.bring_to_front()
                    return page
            except Exception as exc:
                log.debug("Could not inspect a tab: %s", exc)
        return None

    def open_course_home(self):
        """Opens the supported platform home page and returns its tab."""
        page = self.context.new_page()
        home = f"{urls.PLATFORM_ORIGIN}/"
        log.debug("Opening %s in a new tab", home)
        page.goto(home)
        return page

    def reclaim(self, page):
        """Returns a usable page, re-acquiring the tab if ``page`` was closed."""
        try:
            if not page.is_closed():
                return page
        except Exception as exc:
            log.debug("Page liveness check failed: %s", exc)

        logs.get_logger().warning("Target tab closed. Scanning for another course tab...")
        return self.find_course_page()
