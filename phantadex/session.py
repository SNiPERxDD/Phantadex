"""Chrome DevTools Protocol attachment and course-tab management."""

import os

from playwright.sync_api import sync_playwright

from . import chrome, interaction, logs, urls

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
        self._release_media()
        self._disconnect_browser()
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception as stop_exc:
                log.debug("Playwright shutdown failed: %s", stop_exc)
            self._playwright = None
        return False

    def _release_media(self):
        """Hands muting back to the user in tabs that outlive this session."""
        if self.context is None:
            return
        for page in self.context.pages:
            try:
                if urls.PLATFORM_HOST in page.url:
                    interaction.release_media_guard(page)
            except Exception as exc:
                log.debug("Could not release the media guard on a tab: %s", exc)

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

    def _port_check_hint(self):
        """Returns the command that names what holds the debug port.

        Both the port and the command come from the launcher, so the two halves
        of the tool name the same port even for an endpoint the launcher parses
        loosely -- a scheme-less ``PHANTADEX_CDP_URL`` reads as no port at all to
        a strict parser, which would send the user to inspect the wrong one.
        """
        return chrome.port_owner_command(chrome.debug_port(self.cdp_url))

    def _diagnose(self, exc):
        """Turns an opaque CDP failure into an actionable message."""
        detail = str(exc)
        if "Browser context management is not supported" in detail:
            return (
                f"{self.cdp_url} answered, but refused browser-level control. "
                "Usually the debug port is held by something other than a full Chrome "
                "target (a relay, extension bridge, or another automation tool), or the "
                f"Chrome that owns it is mid-restart. Check with `{self._port_check_hint()}`; "
                "if it is not Chrome, close it and relaunch with `pdex chrome`. If it is "
                "Chrome, retry once -- this can be transient."
            )
        if "ECONNREFUSED" in detail or "connect ECONNREFUSED" in detail:
            return f"Nothing is listening on {self.cdp_url}. Start Chrome with `pdex chrome` first."
        return f"Could not attach to Chrome at {self.cdp_url}: {detail}"

    def find_course_page(self, course_url=""):
        """Returns the tab to work in, or ``None``.

        Given a ``course_url`` the tab is pointed at it, which also settles
        which course is meant. Without one, a tab already inside a course wins
        over one merely on the platform -- the catalogue and the enrolment list
        are the same host but carry no course to read. Chrome reports tabs in
        the order they were opened, not in the order they were last looked at,
        so with several of them open the choice is announced rather than made
        silently. A platform tab outside any course is still returned when it is
        all there is, so the caller can say what it found instead of reporting
        no tab at all.
        """
        tabs = self._platform_tabs()
        if course_url:
            return self._open(tabs[0] if tabs else self.context.new_page(), course_url)
        if not tabs:
            return None
        in_course = [page for page in tabs if self._is_in_course(page)]
        chosen = in_course or tabs
        if len(chosen) > 1:
            # Calling them course tabs when none of them is inside a course
            # contradicts the line that follows it.
            kind = "course tabs" if in_course else "platform tabs outside any course"
            logs.get_logger().warning(
                "%d %s are open; working in %r. Close the others, or pass the item URL to choose.",
                len(chosen),
                kind,
                self._describe(chosen[0]),
            )
        chosen[0].bring_to_front()
        return chosen[0]

    def _platform_tabs(self):
        """Returns every open tab on the learning platform, in Chrome's order."""
        tabs = []
        for page in self.context.pages:
            try:
                if urls.PLATFORM_HOST in page.url:
                    tabs.append(page)
            except Exception as exc:
                log.debug("Could not inspect a tab: %s", exc)
        return tabs

    @staticmethod
    def _is_in_course(page):
        """Reports whether a tab is open on a course item rather than elsewhere."""
        try:
            return urls.is_course_url(page.url)
        except Exception as exc:
            log.debug("Could not read a tab URL: %s", exc)
            return False

    def _open(self, page, course_url):
        """Navigates ``page`` to a course link and returns it."""
        target = urls.absolute_url(course_url)
        logs.nav(f"opening {urls.normalize_path(target)}")
        page.goto(target, wait_until="domcontentloaded")
        interaction.arm_media_guard(page)
        page.bring_to_front()
        return page

    @staticmethod
    def _describe(page):
        """Returns a short identifier for a tab, for use in a warning."""
        try:
            return page.title() or urls.normalize_path(page.url)
        except Exception as exc:
            log.debug("Could not read a tab title: %s", exc)
            return "the first one"

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
