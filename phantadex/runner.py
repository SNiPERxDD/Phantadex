"""Main traversal loop.

What used to be a single 685-line ``while True`` is now: keep the tab alive,
keep the course map current, classify the page, hand it to a handler. Everything
type-specific lives in :mod:`phantadex.handlers`.
"""

import time

from . import (
    detection,
    handlers,
    interaction,
    logs,
    modals,
    navigation,
    page_ops,
    prompt,
    urls,
    video,
)
from .course_manager import CourseManager
from .discovery import get_completion_status, get_detailed_course_map, get_robust_course_name
from .session import BrowserSession

log = logs.get_logger("runner")

PROMPTED_TYPES = (
    detection.READING,
    detection.PLUGIN,
    detection.DISCUSSION,
    detection.DIALOGUE,
)

# Item types whose text is archived before the runner skips past them. Both
# extract through ``page_ops.extract_reading``; only the ledger label differs.
ARCHIVED_ON_SKIP = {
    detection.READING: handlers.ReadingHandler,
    detection.DISCUSSION: handlers.DiscussionHandler,
}


class Runner:
    """Drives one automation session against a live course tab."""

    def __init__(self, settings):
        self.settings = settings
        self.ctx = handlers.Context(settings=settings)
        self.last_course_key = ""
        self.last_context_line = ""
        self.last_url = None
        self.stuck_ticks = 0
        self.handler_failures = {}
        self.unhandled_ticks = {}

    HANDLER_FAILURE_LIMIT = 3
    # Passes an unclassified page is given before the run steps past it.
    UNHANDLED_WAIT_LIMIT = 3
    # Settling time after the one-off jump to the first unfinished item.
    RESUME_SETTLE_SECONDS = 3

    def run(self):
        """Connects and loops until the course ends or the user interrupts."""
        with BrowserSession(self.settings.cdp_url) as session:
            page = session.find_course_page(self.settings.course_url)
            if page is None:
                logs.get_logger().warning("No course tab found. Opening one...")
                session.open_course_home()
                logs.get_logger().info("Navigate to a course item, then re-run.")
                return

            logs.banner("Phantadex Watch", self.settings.describe())
            logs.step(f"attached to {page.title()}")
            logs.step("monitoring for content")

            while True:
                page = session.reclaim(page)
                if page is None:
                    logs.get_logger().warning("No course tab available. Waiting 5s...")
                    time.sleep(5)
                    continue
                if self._tick(page) == handlers.COURSE_COMPLETE:
                    logs.ok("course traversal complete")
                    return

    # ------------------------------------------------------------- loop body

    def _tick(self, page):
        """Runs one iteration of the traversal loop."""
        if self._detect_stuck(page):
            return handlers.CONTINUE

        # First thing on every tick, ahead of the map scan and the completion
        # prompt alike: neither of those reaches a handler, and a reading whose
        # narration autoplays used to be audible for the whole of both.
        interaction.silence_media(page)

        if self._sync_course_map(page) and self._resume_at_first_incomplete(page):
            return handlers.CONTINUE
        self._log_context(page)

        if page_ops.is_locked_item(page):
            logs.warn("item locked; returning to the required previous item")
            if navigation.retreat(page, self.ctx.manager, start_url=page.url) in (
                "AT_START",
                "FAILED",
            ):
                # Nowhere to retreat to; idle instead of retrying every tick.
                time.sleep(self.settings.idle_poll_seconds)
            return handlers.CONTINUE

        try:
            modals.dismiss_all(page)
            page_type = detection.classify(page)
        except Exception as exc:
            log.warning("Content detection failed: %s", exc)
            time.sleep(2)
            return handlers.CONTINUE

        handler = handlers.for_page_type(page_type)
        if handler is None:
            return self._step_past_unhandled(page, page_type)

        outcome = self._skip_completed(page, page_type)
        if outcome == "SKIPPED":
            return handlers.CONTINUE
        if outcome == handlers.COURSE_COMPLETE:
            return handlers.COURSE_COMPLETE

        try:
            outcome = handler.handle(page, self.ctx)
            self.handler_failures.pop(urls.normalize_path(page.url), None)
            return outcome
        except Exception as exc:
            item_key = urls.normalize_path(page.url)
            failures = self.handler_failures.get(item_key, 0) + 1
            self.handler_failures[item_key] = failures
            log.warning(
                "%s handler failed (%d/%d): %s",
                page_type,
                failures,
                self.HANDLER_FAILURE_LIMIT,
                exc,
            )
            if failures >= self.HANDLER_FAILURE_LIMIT:
                logs.warn("item failed repeatedly; advancing to prevent an infinite retry")
                self._record_failure(page, exc)
                return self._advance_failed_item(page, item_key)
            time.sleep(2)
            return handlers.CONTINUE

    def _step_past_unhandled(self, page, page_type):
        """Waits out a page no handler claims, then advances rather than idling.

        A page reads as ``UNKNOWN`` while it is still rendering, so the first
        passes only wait. Past that the type is genuinely unhandled -- ``LAB``
        has no handler registered at all -- and the previous behaviour idled on
        it forever, stalling the entire traversal on one item.
        """
        item_key = urls.normalize_path(page.url)
        waited = self.unhandled_ticks.get(item_key, 0) + 1
        self.unhandled_ticks[item_key] = waited

        if waited < self.UNHANDLED_WAIT_LIMIT:
            log.debug(
                "No handler for page type %s; waiting (%d/%d)",
                page_type,
                waited,
                self.UNHANDLED_WAIT_LIMIT,
            )
            time.sleep(self.settings.idle_poll_seconds)
            return handlers.CONTINUE

        logs.warn(f"no handler for {page_type}; advancing")
        if navigation.advance(page, self.ctx.manager, start_url=page.url) == "COURSE_COMPLETE":
            return handlers.COURSE_COMPLETE
        return handlers.CONTINUE

    def _record_failure(self, page, exc):
        """Marks the item as failed in the ledger so the skip is not silent."""
        if self.ctx.manager is None:
            return
        try:
            self.ctx.manager.mark_failed(page.url, exc)
        except Exception as mark_exc:
            log.debug("Could not record the failure in the ledger: %s", mark_exc)

    def _advance_failed_item(self, page, item_key):
        """Skips a persistently broken item and keeps unattended runs bounded."""
        result = navigation.advance(page, self.ctx.manager, start_url=page.url)
        if result == "COURSE_COMPLETE":
            return handlers.COURSE_COMPLETE
        if result in ("NAVIGATED", "ALREADY_MOVED"):
            self.handler_failures.pop(item_key, None)
            return handlers.CONTINUE

        # Broken *and* immovable. Clearing the counter here reset the item to
        # zero strikes, so "three failures then skip" became an unbounded
        # failure/advance cycle. The count stands and the loop idles instead.
        logs.warn("could not advance past the failed item; idling")
        time.sleep(self.settings.idle_poll_seconds)
        return handlers.CONTINUE

    def _detect_stuck(self, page):
        """Reloads the page when the URL has not moved for too many ticks."""
        try:
            current = urls.normalize_path(page.url)
            video_playing = self._video_playing(page)
        except Exception as exc:
            log.debug("Stuck check could not read page state: %s", exc)
            return False

        if current == self.last_url and not video_playing:
            self.stuck_ticks += 1
        else:
            self.stuck_ticks = 0
            self.last_url = current

        if self.stuck_ticks <= self.settings.stuck_iterations:
            return False

        logs.get_logger().warning("Stuck on one item; reloading to recover.")
        self.stuck_ticks = 0
        try:
            page.reload()
        except Exception as exc:
            log.debug("Reload failed: %s", exc)
        time.sleep(5)
        return True

    def _video_playing(self, page):
        """Reports whether a video is actively playing (suppresses stuck alarms)."""
        snapshot = video.state(page)
        return bool(snapshot and not snapshot["paused"] and not snapshot["ended"])

    def _sync_course_map(self, page):
        """Rebuilds the course map when the runner lands in a different course.

        Identity is the URL slug, not the rendered course name: two courses that
        resolve to the same display text would otherwise keep the first one's
        manager, archiving the second course into the first one's ledger.

        Returns True when a new map was loaded on this call.
        """
        try:
            course = get_robust_course_name(page)
            course_key = urls.course_slug(page.url) or course
        except Exception as exc:
            log.debug("Course name lookup failed: %s", exc)
            return False

        if not course or len(course) <= 3 or course_key == self.last_course_key:
            return False

        logs.banner(course)
        try:
            with logs.spinner("generating course map"):
                course_map = get_detailed_course_map(page)
        except Exception as exc:
            log.warning("Course map generation failed: %s", exc)
            return False

        if not course_map:
            logs.warn("Course map came back empty; navigation fallbacks disabled.")
            return False

        self.ctx.manager = CourseManager(course_map, course, root_dir=self.settings.transcript_dir)
        self.last_course_key = course_key
        logs.step(f"map loaded · ledger {self.ctx.manager.xml_path}")
        return True

    def _resume_at_first_incomplete(self, page):
        """Jumps straight to the first item the sidebar does not mark complete.

        Walking there item by item meant a skip prompt on every finished item in
        between, so a course resumed near its end spent minutes stepping through
        work already done. Only rows the sidebar explicitly reports as unfinished
        are targeted: an unreadable row says nothing about its state, and
        treating it as a target would send the run backwards.

        Returns True when the page was navigated.
        """
        if not self.settings.resume_at_incomplete or self.ctx.manager is None:
            return False

        try:
            status = get_completion_status(page)
        except Exception as exc:
            log.debug("Completion scan failed: %s", exc)
            return False
        if not status:
            return False

        target = self._first_incomplete_url(status)
        if not target:
            logs.step("no unfinished item in the sidebar; starting from here")
            return False
        if urls.same_item(page.url, target):
            logs.step("already at the first unfinished item")
            return False

        logs.nav(f"resuming at {urls.normalize_path(target)}")
        try:
            page.goto(urls.absolute_url(target))
        except Exception as exc:
            log.warning("Resume navigation to %s failed: %s", target, exc)
            return False
        time.sleep(self.RESUME_SETTLE_SECONDS)
        self.last_context_line = ""
        self.ctx.reset_announcements()
        return True

    def _first_incomplete_url(self, status):
        """Returns the first mapped item the sidebar reports as unfinished."""
        for lessons in self.ctx.manager.course_map.values():
            for lesson in lessons:
                href = lesson[2]
                if status.get(urls.normalize_path(href)) is False:
                    return href
        return None

    def _log_context(self, page):
        """Prints the course/module context line when the item changes."""
        try:
            context_line = page_ops.page_context(page)
        except Exception as exc:
            log.debug("Context line failed: %s", exc)
            return

        if context_line == self.last_context_line:
            return
        logs.item(*context_line)
        self.last_context_line = context_line
        self.ctx.reset_announcements()

        if self.ctx.manager is not None and self.ctx.manager.is_archived(page.url):
            logs.step("already archived in ledger")

    def _skip_completed(self, page, page_type):
        """Offers to skip an item the sidebar already marks complete."""
        if page_type not in PROMPTED_TYPES:
            return "PROCEED"
        try:
            if not page_ops.is_already_completed(page):
                return "PROCEED"
        except Exception as exc:
            log.debug("Completion check failed: %s", exc)
            return "PROCEED"

        if not self.ctx.first_time("completed_prompt"):
            # Asked once already for this item; a failed advance must not turn
            # into a prompt on every tick.
            return "PROCEED"

        if prompt.stay_on_item(self.settings.completion_prompt_timeout):
            return "PROCEED"

        self._archive_before_skip(page, page_type)
        logs.nav("skip")
        # The end of the course is reachable from here too; dropping this result
        # left the runner re-processing the final item forever.
        if navigation.advance(page, self.ctx.manager, start_url=page.url) == "COURSE_COMPLETE":
            return handlers.COURSE_COMPLETE
        return "SKIPPED"

    def _archive_before_skip(self, page, page_type):
        """Saves a completed item's text before moving past it.

        Skipping means "do not sit here re-reading it", not "throw the text
        away": the archive is the point of the run, so an item the sidebar
        already marks complete still gets its body into the ledger. Discussions
        belong here as much as readings -- skipping one used to leave a
        permanently empty <content> row behind.
        """
        handler_class = ARCHIVED_ON_SKIP.get(page_type)
        if handler_class is None or self.ctx.manager is None:
            return
        if self.ctx.manager.is_archived(page.url):
            return
        try:
            handler_class().archive(page, self.ctx, page_ops.extract_reading(page))
        except Exception as exc:
            log.debug("Pre-skip archive failed: %s", exc)


def run(settings):
    """Entry point used by ``phantadex_watch.py``."""
    logs.setup(settings.log_level)
    Runner(settings).run()
