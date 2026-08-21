"""One handler per content type, replacing the monolithic dispatch loop.

Each handler owns the full lifecycle for its item type -- archive, engage,
advance -- and returns an :class:`Outcome` telling the runner what to do next.
"""

import random
import time
from dataclasses import dataclass, field

from . import (
    detection,
    interaction,
    logs,
    modals,
    navigation,
    page_ops,
    schema,
    timing,
    urls,
    video,
)

log = logs.get_logger("handlers")

# Outcomes returned to the runner.
CONTINUE = "CONTINUE"
COURSE_COMPLETE = "COURSE_COMPLETE"

# A player can report playing while its position never moves; pause recovery
# does not cover that state, so the watch loop bounds it explicitly.
POSITION_EPSILON = 0.05
FROZEN_TICK_LIMIT = 90
# Sub-minute dwell for an external resource that only needs its box ticked.
PLUGIN_DWELL_MINUTES = 0.5
# A graded quiz counts as cleared only after this many consecutive absent
# readings, so markup that has not rendered on the first check is not mistaken
# for a finished attempt.
QUIZ_ABSENT_CONFIRMATIONS = 2


@dataclass
class Context:
    """Per-run state threaded through every handler."""

    settings: object
    manager: object = None
    announced: set = field(default_factory=set)

    def reset_announcements(self):
        """Clears the per-item announcement dedup set."""
        self.announced.clear()

    def first_time(self, key):
        """Returns True the first time ``key`` is seen for the current item."""
        if key in self.announced:
            return False
        self.announced.add(key)
        return True

    def announce(self, key, message):
        """Logs ``message`` once per item; repeat calls are no-ops."""
        if key in self.announced:
            return False
        self.announced.add(key)
        logs.step(message)
        return True


class BaseHandler:
    """Shared archival plumbing for the content handlers."""

    page_type = None
    content_type = None

    def handle(self, page, ctx):
        raise NotImplementedError

    def archive(self, page, ctx, text, method=""):
        """Persists extracted text through the course manager."""
        if not text:
            return False
        if ctx.manager is None:
            logs.warn("No course map loaded; skipping archive.")
            return False
        try:
            filename, ledger_ok = ctx.manager.save_content(page.url, text, self.content_type)
            suffix = f" via {method}" if method else ""
            logs.ok(f"archived{suffix} · {filename} · ledger {'ok' if ledger_ok else 'unmatched'}")
            return True
        except Exception as exc:
            log.warning("Archive failed: %s", exc)
            return False

    def advance(self, page, ctx, start_url):
        """Moves to the next item and maps the result onto an outcome."""
        result = navigation.advance(page, ctx.manager, start_url=start_url)
        if result == "COURSE_COMPLETE":
            return COURSE_COMPLETE
        if result == "FAILED":
            logs.warn("Navigation failed; the stuck detector will retry.")
        return CONTINUE


class VideoHandler(BaseHandler):
    """Archives the transcript, plays the video, then advances."""

    page_type = detection.VIDEO
    content_type = "Transcript"

    def handle(self, page, ctx):
        ctx.announce("video", "video")
        start_url = page.url

        text, method = page_ops.extract_transcript(page)
        if text:
            self.archive(page, ctx, text, method)
        else:
            logs.warn("Transcript extraction failed.")

        video.mute_and_play(page)
        video.seek_into_range(page, ctx.settings.video_skip_range)
        self._watch(page, ctx, start_url)
        return self.advance(page, ctx, start_url)

    def _watch(self, page, ctx, start_url):
        """Blocks until the video reaches its completion target or ends."""
        logs.step("watching")
        paused_ticks = 0
        frozen_ticks = 0
        last_position = None
        target = None

        while True:
            if not urls.same_item(page.url, start_url):
                logs.bar_done()
                logs.warn("Navigated away; abandoning this video.")
                return

            modals.dismiss_all(page)
            snapshot = video.state(page)
            if snapshot is None:
                logs.bar_done()
                logs.warn("Player disappeared.")
                return

            if snapshot["ended"]:
                logs.bar_done()
                logs.ok("video ended")
                return

            paused_ticks = self._handle_pause(page, ctx, snapshot, paused_ticks)

            position = snapshot["currentTime"]
            if last_position is not None and abs(position - last_position) < POSITION_EPSILON:
                frozen_ticks += 1
            else:
                frozen_ticks = 0
            last_position = position
            if frozen_ticks >= FROZEN_TICK_LIMIT:
                logs.bar_done()
                logs.warn("Playback position stopped advancing; abandoning this video.")
                return

            duration = snapshot["duration"]
            if duration > 0:
                if target is None:
                    # The configured threshold is the target. It used to be the
                    # floor of a random 97-100% band, which made every value
                    # below ~97 silently inoperative. Playback variation comes
                    # from --video-skip-range, which is randomised by design.
                    target = min(100.0, float(ctx.settings.video_completion_threshold))
                percent = (snapshot["currentTime"] / duration) * 100
                logs.bar(
                    percent / 100,
                    f"{timing.format_seconds(snapshot['currentTime'])}"
                    f" / {timing.format_seconds(duration)} · target {target}%",
                )
                if percent >= target:
                    logs.bar_done()
                    logs.ok(f"reached {target}% target")
                    time.sleep(random.uniform(2, 4))
                    return

            self._idle_fidget(page)
            time.sleep(1)

    def _handle_pause(self, page, ctx, snapshot, paused_ticks):
        """Resumes playback after a sustained unexpected pause."""
        if not snapshot["paused"]:
            return 0
        paused_ticks += 1
        if paused_ticks > ctx.settings.paused_iterations_before_resume:
            logs.bar_done()
            logs.warn("Player stalled; resuming.")
            video.resume_if_paused(page)
            return 0
        return paused_ticks

    def _idle_fidget(self, page):
        """Small cursor movement during playback (carried over unchanged)."""
        if random.random() >= 0.3:
            return
        try:
            target_x = random.randint(100, 700)
            target_y = random.randint(100, 500)
            log.debug("Idle cursor move to (%d, %d)", target_x, target_y)
            page.mouse.move(target_x, target_y, steps=random.randint(5, 12))
        except Exception as exc:
            log.debug("Fidget move failed: %s", exc)


class ReadingHandler(BaseHandler):
    """Archives the reading body, dwells for the declared duration, advances."""

    page_type = detection.READING
    content_type = "Reading"

    def handle(self, page, ctx):
        ctx.announce("reading", "reading")
        start_url = page.url
        interaction.silence_media(page)
        text = page_ops.extract_reading(page)
        if not text:
            raise RuntimeError("Reading content was not found")

        minutes, source = page_ops.detect_reading_minutes(
            page, ctx.settings.reading_default_minutes
        )
        logs.step(f"listed duration {minutes} min (source: {source})")

        self.archive(page, ctx, text)

        outcome = interaction.reading_session(page, minutes)
        if outcome == "INTERRUPTED":
            # Returning CONTINUE here re-entered this handler forever: no
            # exception reached the runner, so its failure counter never moved.
            raise RuntimeError("Reading session was interrupted")
        if outcome != "COMPLETED":
            return CONTINUE

        navigation.mark_complete(page)
        return self.advance(page, ctx, start_url)


class QuizHandler(BaseHandler):
    """Hands graded quizzes to the user; steps past ungraded practice items."""

    page_type = detection.QUIZ
    content_type = None

    def handle(self, page, ctx):
        if detection.is_graded(page):
            if ctx.settings.pause_on_graded:
                return self._await_human(page, ctx)
            ctx.announce("quiz_graded", "graded quiz -- not auto-answered")
            logs.step("advancing past it; --pause-on-graded waits for you instead")
            return self.advance(page, ctx, page.url)

        ctx.announce("quiz_skip", "ungraded quiz (practice/orientation)")
        logs.step("stepping past without answering")
        return self.advance(page, ctx, page.url)

    def _await_human(self, page, ctx):
        """Pauses the run until the grader page is cleared, then moves on."""
        if ctx.announce("quiz_graded", "graded quiz -- not auto-answered"):
            logs.step("paused -- complete this yourself; the run resumes after")
            _notify("Phantadex Watch", "Graded quiz detected - manual input needed.")

        start_url = page.url
        absent = 0
        while urls.same_item(page.url, start_url):
            if self._quiz_present(page):
                absent = 0
            else:
                absent += 1
                if absent >= QUIZ_ABSENT_CONFIRMATIONS:
                    break
            time.sleep(2)

        if not urls.same_item(page.url, start_url):
            # The user navigated away themselves; nothing left to advance past.
            return CONTINUE

        logs.ok("quiz cleared, resuming")
        # Returning CONTINUE without advancing re-entered this method on every
        # tick: the quiz was gone, so the wait broke immediately and the runner
        # spun on the same item without ever leaving it.
        return self.advance(page, ctx, start_url)

    @staticmethod
    def _quiz_present(page):
        """Reports whether quiz markup is still on screen."""
        try:
            return page.locator(detection.QUIZ_SELECTORS).count() > 0
        except Exception as exc:
            log.debug("Quiz presence check failed: %s", exc)
            return False


class PluginHandler(BaseHandler):
    """Handles ungraded LTI plugins, external tools and peer assignments."""

    page_type = detection.PLUGIN
    content_type = None

    def handle(self, page, ctx):
        ctx.announce("plugin", "external resource / ungraded plugin")
        start_url = page.url
        interaction.silence_media(page)

        if schema.first_visible(page, "navigation", "mark_complete") is not None:
            logs.step("resource has a completion control; dwelling briefly")
            interaction.reading_session(page, PLUGIN_DWELL_MINUTES)
            navigation.mark_complete(page)
        else:
            logs.step("skipping external tool/survey")

        return self.advance(page, ctx, start_url)


class AssignmentHandler(PluginHandler):
    """Leaves graded assignments for the user and resumes after navigation."""

    page_type = detection.ASSIGNMENT

    def handle(self, page, ctx):
        ctx.announce("assignment", "peer or honors assignment")
        start_url = page.url
        if not ctx.settings.pause_on_graded:
            logs.step("advancing past it; --pause-on-graded waits for you instead")
            return self.advance(page, ctx, start_url)

        logs.step("paused -- complete or leave this item; Watch resumes after navigation")
        while urls.same_item(page.url, start_url):
            time.sleep(2)
        return CONTINUE


class DiscussionHandler(BaseHandler):
    """Archives the prompt and advances. Does not compose or submit a reply."""

    page_type = detection.DISCUSSION
    content_type = "Discussion"

    def handle(self, page, ctx):
        ctx.announce("discussion", "discussion prompt")
        start_url = page.url
        interaction.silence_media(page)
        self.archive(page, ctx, page_ops.extract_reading(page))
        return self.advance(page, ctx, start_url)


def _notify(title, message):
    """Best-effort desktop notification; never fatal."""
    try:
        from plyer import notification

        notification.notify(title=title, message=message)
    except Exception as exc:
        log.debug("Desktop notification failed: %s", exc)


HANDLERS = {
    handler.page_type: handler
    for handler in (
        VideoHandler(),
        ReadingHandler(),
        QuizHandler(),
        PluginHandler(),
        AssignmentHandler(),
        DiscussionHandler(),
    )
}


def for_page_type(page_type):
    """Returns the handler registered for a page type, or ``None``."""
    return HANDLERS.get(page_type)
