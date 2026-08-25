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
    jitter,
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
# The run ended for a reason that is not the course running out of items -- a
# limit was reached, or there was nothing left it would act on. Kept apart from
# COURSE_COMPLETE so neither is reported as the other. It lives here rather than
# in the runner because a handler's advance can now reach it too.
STOPPED = "STOPPED"

# A player can report playing while its position never moves; pause recovery
# does not cover that state, so the watch loop bounds it explicitly.
POSITION_EPSILON = 0.05
FROZEN_TICK_LIMIT = 90
# Pause after a video meets its target, before the run advances.
POST_TARGET_DWELL_RANGE = (2.0, 4.0)
# Sub-minute dwell for an external resource that only needs its box ticked.
PLUGIN_DWELL_MINUTES = 0.5
# A roleplay item is a multi-turn conversation for a person, and the platform
# records how long one stayed open. Opening it and closing it seconds apart
# reports a session no person had, so the run holds it open for a plausible
# stretch instead. Nothing is sent -- composing turns is the user's alone.
DIALOGUE_DWELL_RANGE = (45.0, 120.0)
# The open stretch is spent in short slices with occasional cursor drift, the
# way a person sits with a page they are conversing in.
DIALOGUE_DWELL_SLICE_RANGE = (6.0, 14.0)
# Pause between dialogue control retries while a control renders.
DIALOGUE_RETRY_SECONDS_RANGE = (0.8, 1.5)
# Each dialogue control renders in response to the previous click rather than
# with the page, so a control is polled for rather than expected to be present.
DIALOGUE_CONTROL_ATTEMPTS = 10
# A graded quiz counts as cleared only after this many consecutive absent
# readings, so markup that has not rendered on the first check is not mistaken
# for a finished attempt.
QUIZ_ABSENT_CONFIRMATIONS = 2
# How often the graded-quiz wait is polled, and how often it reminds the screen
# that it is still waiting rather than hung.
QUIZ_POLL_SECONDS = 2
QUIZ_REMINDER_SECONDS = 60


@dataclass
class Context:
    """Per-run state threaded through every handler."""

    settings: object
    manager: object = None
    announced: set = field(default_factory=set)
    # ``callable(page, start_url) -> outcome | None``, supplied by the runner:
    # where the run goes when it is finished with an item. ``None`` means it
    # could not tell, and the caller falls back to the platform's Next button.
    next_work: object = None

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
    # One line on what a run does with this kind of item, printed by ``pdex -h``.
    # Held here rather than in the help text so a handler cannot be added and
    # then go undocumented; ``tests/test_cli.py`` fails when one has no summary.
    summary = ""

    def handle(self, page, ctx):
        raise NotImplementedError

    def already_archived(self, page, ctx):
        """Reports whether the archive already holds this item's text.

        Extraction is not free -- a transcript costs a tab click and a scrape --
        and re-running it on an item the ledger already holds rewrites the same
        file with the same words. The run says what it did once; it does not
        keep doing it.
        """
        if ctx.manager is None:
            return False
        try:
            return ctx.manager.is_archived(page.url)
        except Exception as exc:
            log.debug("Archive check failed: %s", exc)
            return False

    @staticmethod
    def _still_here(page, start_url):
        """Reports whether the tab is still on the item the handler started on."""
        try:
            return urls.same_item(page.url, start_url)
        except Exception as exc:
            log.debug("URL read before advancing failed: %s", exc)
            return False

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
        """Moves to the next item the run has work on, and maps it onto an outcome.

        Adjacency is not the same as work. The item after this one is very often
        already finished, and walking into it costs a page load and a completion
        prompt to learn what the sidebar could have said from here. When the
        runner can answer "where is the next thing to do", that answer is used;
        the platform's Next button is the fallback for when it cannot.

        Both routes defer to a page that has already moved. Only the fallback
        used to: ``navigation.advance`` reports ``ALREADY_MOVED`` and does
        nothing, while the work-based route asked where to go *from the item
        this handler started on* and went there -- overriding the platform's own
        hand-off, and overriding the user when they picked a different item
        themselves. Whoever moved the tab is right about where it should be.
        """
        if ctx.next_work is not None and self._still_here(page, start_url):
            outcome = ctx.next_work(page, start_url)
            if outcome is not None:
                return outcome

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
    summary = "Archives the transcript, then plays through to the completion target."

    def handle(self, page, ctx):
        ctx.announce("video", logs.type_tag(detection.VIDEO))
        start_url = page.url

        # A transcript the archive already holds is not scraped again; the item
        # is still watched, because being archived says nothing about whether
        # the platform counts it as viewed.
        if not self.already_archived(page, ctx):
            text, method = page_ops.extract_transcript(page)
            if text:
                self.archive(page, ctx, text, method)
            else:
                logs.warn("Transcript extraction failed.")

        video.mute_and_play(page)
        video.seek_into_range(page, ctx.settings.video_skip_range)
        self._watch(page, ctx, start_url)
        if urls.same_item(page.url, start_url):
            # The run leaves the item once the target is met; a player still
            # running at that moment reads as an abandoned session.
            video.pause_if_playing(page)
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
                    # Sampled once per video, from the configured range. The
                    # target used to be the floor of a fixed random 97-100%
                    # band, which made every configured value below ~97
                    # silently inoperative; the band is now the setting itself,
                    # and a single number narrows it to exactly that value.
                    low, high = ctx.settings.video_completion_threshold
                    target = round(min(100.0, random.uniform(low, high)), 1)
                percent = (snapshot["currentTime"] / duration) * 100
                logs.bar(
                    percent / 100,
                    f"{timing.format_seconds(snapshot['currentTime'])}"
                    f" / {timing.format_seconds(duration)} · target {target}%",
                )
                # Both names are only bound once the player reports a duration,
                # so the comparison stays inside this block. A player still
                # loading metadata reports 0, which is the state of every video
                # on its first tick.
                if percent >= target:
                    logs.bar_done()
                    logs.ok(f"reached {target}% target")
                    time.sleep(jitter.duration(*POST_TARGET_DWELL_RANGE))
                    return

            self._idle_fidget(page)
            time.sleep(jitter.duration(0.8, 1.4))

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
    summary = "Archives the body, dwells for the listed duration while scrolling, marks complete."

    def handle(self, page, ctx):
        ctx.announce("reading", logs.type_tag(detection.READING))
        start_url = page.url
        archived = self.already_archived(page, ctx)
        # Extracted whether or not the ledger holds it: a body that will not
        # render is a page to fail on, not one to dwell ten minutes on and mark
        # complete, and for a reading the read costs one locator lookup. What
        # being archived saves is the *save* -- the file is not rewritten.
        text = page_ops.extract_reading(page)
        if not text:
            raise RuntimeError("Reading content was not found")

        minutes, source = page_ops.detect_reading_minutes(
            page, ctx.settings.reading_default_minutes
        )
        logs.step(f"listed duration {minutes} min (source: {source})")

        # Read here rather than inside the session: ``interaction`` cannot import
        # the media reader without a cycle. A narrated reading is really an audio
        # item wearing a page, and its player carries the only honest length.
        narration = video.narration_seconds(page)
        if narration:
            logs.step(f"narrated, {timing.format_seconds(narration)}")

        if not archived:
            self.archive(page, ctx, text)

        outcome = interaction.reading_session(page, minutes, narration)
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
    summary = "Never answered. Graded ones are named and stepped past, or waited on."

    def handle(self, page, ctx):
        if detection.is_graded(page):
            if ctx.settings.pause_on_graded:
                return self._await_human(page, ctx)
            ctx.announce(
                "quiz_graded", f"graded {logs.type_tag(detection.QUIZ)} -- not auto-answered"
            )
            logs.step("advancing past it; --pause-on-graded waits for you instead")
            return self.advance(page, ctx, page.url)

        ctx.announce(
            "quiz_skip", f"ungraded {logs.type_tag(detection.QUIZ)} (practice/orientation)"
        )
        logs.step("stepping past without answering")
        return self.advance(page, ctx, page.url)

    def _await_human(self, page, ctx):
        """Pauses the run until the grader page is cleared, then moves on."""
        if ctx.announce(
            "quiz_graded", f"graded {logs.type_tag(detection.QUIZ)} -- not auto-answered"
        ):
            logs.step("paused -- complete this yourself; the run resumes after")
            _notify("Phantadex Watch", "Graded quiz detected - manual input needed.")

        start_url = page.url
        absent = 0
        # Absence only means "finished" once the quiz has actually been seen.
        # A graded quiz behind a start screen matches nothing, so counting
        # absence from the first poll ended the pause after about four seconds
        # -- announcing a wait for the user and then advancing without one.
        seen = False
        waited = 0
        next_reminder = QUIZ_REMINDER_SECONDS
        while urls.same_item(page.url, start_url):
            if self._quiz_present(page):
                seen = True
                absent = 0
            elif seen:
                absent += 1
                if absent >= QUIZ_ABSENT_CONFIRMATIONS:
                    break
            time.sleep(QUIZ_POLL_SECONDS)
            waited += QUIZ_POLL_SECONDS
            if waited >= next_reminder:
                logs.pending(f"still waiting on this quiz ({waited // 60}m)")
                next_reminder += QUIZ_REMINDER_SECONDS

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
    summary = "Ungraded plugin, external tool or lab. Ticked off if it offers a control."

    def handle(self, page, ctx):
        ctx.announce("plugin", f"external resource / {logs.type_tag(detection.UNGRADED_PLUGIN)}")
        start_url = page.url

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
    summary = "Peer and honors work is left to you. Nothing is drafted or submitted."

    def handle(self, page, ctx):
        ctx.announce("assignment", f"peer or honors {logs.type_tag(detection.ASSIGNMENT)}")
        start_url = page.url
        if not ctx.settings.pause_on_graded:
            logs.step("advancing past it; --pause-on-graded waits for you instead")
            return self.advance(page, ctx, start_url)

        logs.step("paused -- complete or leave this item; Watch resumes after navigation")
        while urls.same_item(page.url, start_url):
            time.sleep(2)
        return CONTINUE


class SurveyHandler(BaseHandler):
    """Steps past a questionnaire about the learner without answering it."""

    page_type = detection.SURVEY
    content_type = None
    summary = "Stepped past unanswered: it asks about you, not about the course."

    def handle(self, page, ctx):
        ctx.announce("survey", f"{logs.type_tag(detection.SURVEY)} -- not answered")
        logs.step("stepping past it; it asks about you, not the course")
        return self.advance(page, ctx, page.url)


class DiscussionHandler(BaseHandler):
    """Archives the prompt and advances. Does not compose or submit a reply."""

    page_type = detection.DISCUSSION
    content_type = "Discussion"
    summary = "Archives the prompt. No reply is composed or posted."

    def handle(self, page, ctx):
        ctx.announce("discussion", f"{logs.type_tag(detection.DISCUSSION)} prompt")
        start_url = page.url
        if not self.already_archived(page, ctx):
            self.archive(page, ctx, page_ops.extract_reading(page))
        return self.advance(page, ctx, start_url)


class DialogueHandler(BaseHandler):
    """Completes an AI roleplay practice item.

    Coursera marks one of these complete only once a dialogue has been opened
    and then explicitly closed: ``Start Dialogue``, ``End Dialogue``, and the
    ``Yes, end the Dialogue`` confirmation in the prompt that follows. A
    ``Try again`` control appearing in place of the transcript is the page's
    own signal that the sequence landed. Nothing is archived -- the content is
    a conversation the run does not hold up its end of.
    """

    page_type = detection.DIALOGUE
    content_type = None
    summary = "Opened, closed, and the closing confirmed, which is what completes it."

    def handle(self, page, ctx):
        ctx.announce("dialogue", f"roleplay {logs.type_tag(detection.DIALOGUE)}")
        start_url = page.url

        if self._control(page, "finished") is not None:
            logs.step("already ended; stepping past it")
            return self.advance(page, ctx, start_url)

        # A dialogue an earlier run left open has no ``Start`` control any more;
        # the end sequence below picks it up from wherever it already is.
        if self._control(page, "end") is None and self._click(page, "start"):
            logs.step("dialogue started")
            self._dwell(page, start_url)

        if self._end(page):
            logs.ok("dialogue ended")
        else:
            logs.warn("Dialogue did not end; leaving it as it is.")
        return self.advance(page, ctx, start_url)

    def _dwell(self, page, start_url):
        """Holds the dialogue open for a plausible session length.

        The platform records how long a roleplay session stayed open; closing
        one seconds after opening it reports a conversation nobody had. The
        stretch is spent in slices, with the cursor drifting near the middle of
        the window now and then, and no message is ever composed or sent.

        The dwell runs for minutes, which is long enough for the item to be
        left from the browser. Each slice re-checks that, and the cursor move
        is guarded like the viewport read beside it: both talk to a page that
        may be navigating, and neither is worth failing the item over.
        """
        remaining = jitter.duration(*DIALOGUE_DWELL_RANGE)
        while remaining > 0:
            slice_seconds = min(remaining, jitter.duration(*DIALOGUE_DWELL_SLICE_RANGE))
            time.sleep(slice_seconds)
            remaining -= slice_seconds
            if not urls.same_item(page.url, start_url):
                logs.step("dialogue left; ending the dwell")
                return
            if random.random() < 0.5:
                try:
                    width, height = page.evaluate("() => [window.innerWidth, window.innerHeight]")
                    interaction.move(
                        page,
                        width / 2 + random.randint(-120, 120),
                        height / 2 + random.randint(-80, 80),
                    )
                except Exception as exc:
                    log.debug("Cursor drift during dialogue dwell failed: %s", exc)

    def _end(self, page):
        """Ends a running dialogue and confirms it. Reports whether it finished."""
        if not self._click(page, "end"):
            return False
        if not self._click(page, "confirm_end"):
            return False
        for _ in range(DIALOGUE_CONTROL_ATTEMPTS):
            if self._control(page, "finished") is not None:
                return True
            time.sleep(jitter.duration(*DIALOGUE_RETRY_SECONDS_RANGE))
        return False

    @staticmethod
    def _control(page, element):
        """Returns the named dialogue control if it is on screen, else ``None``."""
        return schema.first_visible(page, "dialogue", element)

    @classmethod
    def _click(cls, page, element):
        """Clicks a dialogue control once it appears. Reports whether it was clicked.

        The click goes through the shared interaction path -- approach move,
        reaction dwell, jittered point, bounded wait -- like every other click
        the run makes. A bare locator click would teleport the cursor to the
        element centre and could sit out Playwright's 30-second default on a
        control that never becomes clickable.
        """
        for attempt in range(DIALOGUE_CONTROL_ATTEMPTS):
            control = cls._control(page, element)
            if control is not None:
                if interaction.click(page, control, reaction_range=(0.4, 0.9)):
                    return True
                log.debug("Dialogue control %r was not clickable this pass", element)
            if attempt + 1 < DIALOGUE_CONTROL_ATTEMPTS:
                time.sleep(jitter.duration(*DIALOGUE_RETRY_SECONDS_RANGE))
        return False


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
        SurveyHandler(),
        DialogueHandler(),
    )
}


def for_page_type(page_type):
    """Returns the handler registered for a page type, or ``None``."""
    return HANDLERS.get(page_type)
