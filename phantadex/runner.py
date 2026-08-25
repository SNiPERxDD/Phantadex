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
    jitter,
    limits,
    logs,
    modals,
    navigation,
    page_ops,
    progress,
    prompt,
    urls,
    video,
)
from .course_manager import CourseManager
from .discovery import get_completion_status, get_detailed_course_map, get_robust_course_name
from .session import BrowserSession

log = logs.get_logger("runner")

# Re-exported: the outcome is defined beside the others in :mod:`handlers`,
# because a handler's advance can reach it too.
STOPPED = handlers.STOPPED

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
        # How a handler finds out where to go once it is done with an item.
        self.ctx.next_work = self._advance_to_work
        # Items this run has worked and left still listed as unfinished. A
        # discussion prompt is read and archived but never posted to, so the
        # sidebar keeps offering it as the next thing to do; without this the
        # run walked to it, off it, and back to it for as long as it was left.
        self.attempted_items = []
        self.last_course_key = ""
        self.last_context_line = ""
        self.last_url = None
        # The last URL that was actually inside the course. Kept apart from
        # ``last_url``, which the stuck check moves to whatever page is open --
        # including the one the recovery is trying to steer away from.
        self.last_course_url = None
        self.stuck_ticks = 0
        self.handler_failures = {}
        self.unhandled_ticks = {}
        self.budget = limits.Budget(
            modules=settings.module_limit,
            items=settings.item_limit,
        )
        self.stall = limits.StallGuard(settings.stall_iterations)
        # Passes spent on a platform page that is not a course item.
        self.off_course_ticks = 0
        self.warned_off_course = False
        # ``_sync_course_map`` runs on every tick, so the notice that no ledger
        # could be opened has to be raised once rather than each time round.
        self.warned_unmapped = False

    HANDLER_FAILURE_LIMIT = 3
    # Passes an unclassified page is given before the run steps past it.
    UNHANDLED_WAIT_LIMIT = 3
    # Attempts at getting back into the course before the run gives up on it.
    OFF_COURSE_LIMIT = 3
    # Settling time after the one-off jump to the first unfinished item. Drawn
    # rather than fixed: a repeated exact wait is a cadence, not a pause.
    RESUME_SETTLE_RANGE = (2.0, 4.5)
    # Retry waits inside the loop body, each drawn from jitter.
    NO_TAB_WAIT_RANGE = (4.0, 7.0)
    DETECTION_RETRY_RANGE = (1.5, 3.0)
    HANDLER_RETRY_RANGE = (1.5, 3.0)
    RELOAD_SETTLE_RANGE = (4.0, 7.0)

    def run(self):
        """Connects and loops until the course ends or the user interrupts."""
        with BrowserSession(self.settings.cdp_url) as session:
            page = session.find_course_page(self.settings.course_url)
            if page is None:
                logs.get_logger().warning("No course tab found. Opening one...")
                session.open_course_home()
                logs.get_logger().info("Navigate to a course item, then re-run.")
                return
            if not self._inside_course(page):
                # The catalogue, the home page and the enrolment list are all on
                # the same host, and none of them carries the sidebar every part
                # of a run reads. There is nothing to attach to.
                logs.warn("the open tab is on the platform but not inside a course")
                logs.step("open a course item, or name one: pdex watch <url>")
                return

            logs.banner("Phantadex Watch", self.settings.describe())
            logs.step(f"attached to {page.title()}")
            logs.step("monitoring for content")

            while True:
                page = session.reclaim(page)
                if page is None:
                    logs.get_logger().warning("No course tab available. Waiting...")
                    time.sleep(jitter.duration(*self.NO_TAB_WAIT_RANGE))
                    continue
                outcome = self._tick(page)
                if outcome == handlers.COURSE_COMPLETE:
                    logs.ok("course traversal complete")
                    return
                if outcome == STOPPED:
                    # The tick that returned this already said why.
                    return

    # ------------------------------------------------------------- loop body

    def _tick(self, page):
        """Runs one iteration of the traversal loop."""
        if self._detect_stuck(page):
            return handlers.CONTINUE

        if not self._inside_course(page):
            return self._recover_off_course(page)
        self.last_course_url = urls.normalize_path(page.url)
        self.off_course_ticks = 0
        self.warned_off_course = False

        # First thing on every tick, ahead of the map scan and the completion
        # prompt alike: neither of those reaches a handler, and a reading whose
        # narration autoplays used to be audible for the whole of both.
        interaction.silence_media(page)

        if self._sync_course_map(page):
            planned = self._resume_or_finish(page)
            if planned is not None:
                return planned

        # Both bounds are read before the item is announced or touched, so a run
        # that stops here stops without having started on the item it refused.
        item_key = urls.normalize_path(page.url)
        if not self.stall.observe(item_key):
            return self._stop_stalled()
        # The budget counts what `CourseManager.item_count` counted when it
        # printed "the next N of M items": distinct items, not sidebar rows. A
        # peer assignment is two rows under one item id, and keying the budget
        # on the URL let it spend two of a bound the user was quoted as one.
        if not self.budget.admits(urls.item_id(page.url) or item_key, self._module_for(page.url)):
            logs.ok(f"limit reached -- watched {self.budget.summary()}")
            return STOPPED

        self._log_context(page)

        if page_ops.is_locked_item(page) and not self._at_course_end(page):
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
            time.sleep(jitter.duration(*self.DETECTION_RETRY_RANGE))
            return handlers.CONTINUE

        handler = handlers.for_page_type(page_type)
        if handler is None:
            return self._step_past_unhandled(page, page_type)

        outcome = self._skip_completed(page, page_type)
        if outcome == "SKIPPED":
            return handlers.CONTINUE
        if outcome != "PROCEED":
            # The course ended, or the run stopped, while leaving this item.
            return outcome

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
            time.sleep(jitter.duration(*self.HANDLER_RETRY_RANGE))
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
        result = navigation.advance(page, self.ctx.manager, start_url=page.url)
        if result == "COURSE_COMPLETE":
            return handlers.COURSE_COMPLETE
        if result == "FAILED":
            # Unclassifiable and immovable. Without this wait the pair -- warn,
            # fail to advance -- repeated as fast as the loop could turn.
            time.sleep(self.settings.idle_poll_seconds)
        return handlers.CONTINUE

    def _inside_course(self, page):
        """Reports whether the tab is on a course item rather than elsewhere."""
        try:
            return urls.is_course_url(page.url)
        except Exception as exc:
            log.debug("Could not read the page URL: %s", exc)
            return False

    def _recover_off_course(self, page):
        """Steers the tab back into the course, and stops if it will not go.

        A tab reaches the platform's other pages by an expired session
        redirecting to the sign-in page, by the course being left through its
        own navigation, or by the user simply clicking away. None of them is an
        item: there is no map to read, no handler to run and nowhere to advance
        to, so the previous behaviour was to classify the page as UNKNOWN and
        spin on it.
        """
        self.off_course_ticks += 1
        if not self.warned_off_course:
            self.warned_off_course = True
            logs.warn(f"the tab left the course: {urls.normalize_path(page.url)}")

        if self.off_course_ticks > self.OFF_COURSE_LIMIT:
            logs.warn("still outside the course; stopping rather than waiting on it")
            logs.step("open a course item and run again, or name one: pdex watch <url>")
            return STOPPED

        if self.last_course_url and self._return_to(page, self.last_course_url):
            return handlers.CONTINUE

        logs.step("waiting for a course item to open")
        time.sleep(self.settings.idle_poll_seconds)
        return handlers.CONTINUE

    def _return_to(self, page, item_path):
        """Navigates back to a course item. Returns whether it arrived."""
        logs.nav(f"returning to {item_path}")
        try:
            page.goto(urls.absolute_url(item_path))
        except Exception as exc:
            log.warning("Return to %s failed: %s", item_path, exc)
            return False
        time.sleep(jitter.duration(*self.RESUME_SETTLE_RANGE))
        return self._inside_course(page)

    def _at_course_end(self, page):
        """Reports whether the page is the last item rather than a locked one.

        A lock offers no way forward -- and neither does the final item of a
        course, which renders no Next control at all. Read as a lock, it sent
        the run backwards: retreat, get skipped forward again, arrive nowhere
        new, until the stall guard ended a run that had in fact reached the end.
        The map tells them apart. An item the map does not know is never called
        last, because a slow lazy-load produces the same silence; that is what
        :meth:`CourseManager.is_mapped` is for.
        """
        manager = self.ctx.manager
        if manager is None or not manager.is_mapped(page.url):
            return False
        return manager.get_next_url(page.url) is None

    def _stop_stalled(self):
        """Ends a run that has stopped reaching items it has not already been on."""
        logs.warn(f"no new item in {self.stall.idle_ticks} passes; the run is going in circles")
        reached = len(self.stall.visited)
        logs.step(f"stopping here -- {reached} {'item' if reached == 1 else 'items'} reached")
        return STOPPED

    def _module_for(self, current_url):
        """Returns the module holding ``current_url``, or ``""`` without a map."""
        if self.ctx.manager is None:
            return ""
        try:
            return self.ctx.manager.module_for(current_url)
        except Exception as exc:
            log.debug("Module lookup failed: %s", exc)
            return ""

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
        time.sleep(jitter.duration(*self.RELOAD_SETTLE_RANGE))
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
            self._warn_unmapped("the course name could not be read")
            return False

        if course_key == self.last_course_key:
            return False
        if not course or len(course) <= 3:
            self._warn_unmapped(f"the course name read as {course!r}")
            return False

        logs.banner(course)
        try:
            with logs.spinner("generating course map"):
                course_map = get_detailed_course_map(page)
        except Exception as exc:
            log.warning("Course map generation failed: %s", exc)
            self._warn_unmapped(f"the course map could not be built ({exc})")
            return False

        if not course_map:
            self._warn_unmapped("the course map came back empty")
            return False

        self.ctx.manager = CourseManager(course_map, course, root_dir=self.settings.transcript_dir)
        self.last_course_key = course_key
        self.warned_unmapped = False
        logs.step(f"map loaded · ledger {self.ctx.manager.xml_path}")
        self.budget.check_against(
            self.ctx.manager.module_count(),
            self.ctx.manager.item_count(),
        )
        return True

    def _warn_unmapped(self, reason):
        """Reports once that the run is proceeding without a ledger.

        Without a manager nothing is archived and no navigation fallback works,
        which used to look identical to a course that simply had nothing to
        save. Said once per failure, not once per tick.
        """
        if self.ctx.manager is not None or self.warned_unmapped:
            return
        self.warned_unmapped = True
        logs.warn(f"No ledger for this course: {reason}.")
        logs.step("items are still played, but nothing is archived this run")

    def _resume_or_finish(self, page):
        """Starts the run at the first item with work left, or ends it.

        Called once, when the map loads. Walking to that item one at a time
        meant a skip prompt on every finished item in between, so a course
        resumed near its end spent minutes stepping through work already done.
        Only rows the sidebar explicitly reports as unfinished are targeted: an
        unreadable row says nothing about its state, and treating it as a target
        would send the run backwards.

        Graded work is not work this run does -- it names it and steps past --
        so a course whose remaining items are all graded has nothing left for the
        run to do, and touring it to find that out is the walk this method exists
        to avoid. ``--pause-on-graded`` makes those items work again.

        Returns a tick outcome for the caller to hand back, or ``None`` to carry
        on with the item that is open.
        """
        status = self._completion_status(page)
        if status is None:
            return None
        target = self.ctx.manager.next_actionable(status, skip_labels=self._skipped_labels())
        if target is None:
            return self._finish_with_nothing_to_do(
                self.ctx.manager.unfinished_items(status), status
            )
        if urls.same_item(page.url, target[2]):
            logs.step("already at the first unfinished item")
            return None
        return self._go_to(page, target, "resuming at")

    def _advance_to_work(self, page, start_url):
        """Where the run goes once it is finished with an item.

        Handed to the handlers as :attr:`Context.next_work`. The item after the
        one just done is very often already finished, and walking into it costs
        a page load and a completion prompt to learn what the sidebar knew from
        here -- so the run asks the sidebar instead, and goes straight to the
        next thing it would actually do.

        Returns a tick outcome, or ``None`` when the sidebar cannot answer, in
        which case the caller falls back to the platform's Next button.
        """
        status = self._completion_status(page)
        if status is None:
            return None
        target = self.ctx.manager.next_actionable(
            status,
            current_url=start_url,
            skip_labels=self._skipped_labels(),
            exclude=self.attempted_items,
        )
        if target is not None:
            return self._go_to(page, target, "next")

        if self.ctx.manager.unfinished_items(
            status, skip_labels=self._skipped_labels(), exclude=self.attempted_items
        ):
            # The only work the sidebar still lists is the item just finished.
            # Usually it simply has not caught up yet, so the run steps past it
            # the ordinary way and reads the sidebar again next tick -- but the
            # item is remembered, because an item that is *never* going to be
            # marked complete would otherwise be chosen as the next work every
            # time the run passed it.
            self._remember_attempt(start_url)
            return None
        return self._finish_with_nothing_to_do(self.ctx.manager.unfinished_items(status), status)

    def _remember_attempt(self, item_url):
        """Records an item the run has worked and cannot get marked complete."""
        if not item_url or any(urls.same_item(item_url, seen) for seen in self.attempted_items):
            return
        self.attempted_items.append(item_url)

    def _completion_status(self, page):
        """Returns the sidebar's completion reading, or ``None`` if unusable."""
        if not self.settings.resume_at_incomplete or self.ctx.manager is None:
            return None
        try:
            status = get_completion_status(page)
        except Exception as exc:
            log.debug("Completion scan failed: %s", exc)
            return None
        return status or None

    def _skipped_labels(self):
        """Returns the row labels this run will not act on."""
        if self.settings.pause_on_graded:
            return progress.SKIPPED_LABELS - progress.GRADED_LABELS
        return progress.SKIPPED_LABELS

    def _go_to(self, page, row, verb):
        """Navigates to a mapped row. Returns a tick outcome, or ``None`` on failure."""
        title, label, href, _duration = row
        logs.nav(f"{verb} {title} · {logs.type_tag(label)}")
        try:
            page.goto(urls.absolute_url(href))
        except Exception as exc:
            log.warning("Navigation to %s failed: %s", href, exc)
            return None
        time.sleep(jitter.duration(*self.RESUME_SETTLE_RANGE))
        self.last_context_line = ""
        self.ctx.reset_announcements()
        return handlers.CONTINUE

    def _finish_with_nothing_to_do(self, unfinished, status=None):
        """Ends a run whose course holds no item it would act on."""
        awaiting = self.ctx.manager.awaiting_your_post(status) if status else []
        if not unfinished:
            counts = self._course_split(status)
            unknown = counts.unknown if counts else 0
            if unknown:
                # An empty unfinished list is not proof of a finished course.
                # The sidebar reading returns whatever it had collected when a
                # row went stale under it, and a reading cut short at the top
                # lists nothing unfinished -- which announced a barely-started
                # course complete, the one report a run must never make.
                rows = "row" if unknown == 1 else "rows"
                logs.ok(
                    f"nothing here reads as unfinished, but {unknown} {rows} "
                    f"could not be read -- reopen the course to see the rest"
                )
                logs.step(progress.summary(counts))
            else:
                logs.ok("every item in the course map is already complete")
            self._report_awaiting(awaiting)
            return STOPPED
        count = len(unfinished)
        subject = "item is" if count == 1 else "items are"
        kinds = sorted({logs.type_name(label) for _t, label, _h, _d in unfinished})
        logs.ok(
            f"nothing left to watch -- the {count} unfinished {subject} "
            f"not this run's work ({', '.join(kinds)})"
        )
        self._report_split(status)
        if any(label in progress.GRADED_LABELS for _t, label, _h, _d in unfinished):
            logs.tip(
                "--pause-on-graded stops the run at each graded item and waits "
                "for you to answer it, instead of walking past"
            )
        if self.attempted_items:
            count = len(self.attempted_items)
            subject = "item was" if count == 1 else "items were"
            logs.step(
                f"{count} {subject} worked and still listed as unfinished; "
                "completing those takes a submission of your own"
            )
        self._report_awaiting(awaiting)
        return STOPPED

    def _report_split(self, status):
        """Prints the whole course divided by who is left to act on it.

        The tally the run ends on counts only what it walked past, which reads
        as the course being nearly untouched when in fact the run had nothing
        left to do -- so the closing line says what is done, what would still
        be the run's work, and what is yours.
        """
        counts = self._course_split(status)
        if counts is not None:
            logs.step(progress.summary(counts))

    def _course_split(self, status):
        """Returns the course divided by who is left to act on it, or ``None``."""
        if not status or self.ctx.manager is None:
            return None
        rows = [row for lessons in self.ctx.manager.course_map.values() for row in lessons]
        return progress.split(rows, status, self.ctx.manager.is_archived)

    def _report_awaiting(self, awaiting):
        """Names the archived items only the user can finish, so none is silent."""
        for title, label, _href, _duration in awaiting:
            logs.step(f"{title} · {logs.type_tag(label)} is archived but waits on a post of yours")

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
        outcome = self._advance_to_work(page, page.url)
        if outcome is not None:
            return "SKIPPED" if outcome == handlers.CONTINUE else outcome

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
