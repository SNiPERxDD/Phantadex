"""Handler dispatch and the paths a live run rarely reaches."""

import contextlib
import unittest
from unittest import mock

from phantadex import config, detection, handlers
from tests.fakes import FakeLocator, FakePage


class FakeManager:
    def __init__(self, archived=False):
        self.saved = []
        self.archived = archived

    def is_archived(self, _url):
        return self.archived

    def save_content(self, url, text, content_type):
        self.saved.append((url, text, content_type))
        return "Item.txt", True


class DispatchTests(unittest.TestCase):
    """Every type classify() can return must reach its own handler."""

    def test_each_type_maps_to_its_handler(self):
        expected = {
            detection.VIDEO: handlers.VideoHandler,
            detection.READING: handlers.ReadingHandler,
            detection.QUIZ: handlers.QuizHandler,
            detection.PLUGIN: handlers.PluginHandler,
            detection.ASSIGNMENT: handlers.AssignmentHandler,
            detection.DISCUSSION: handlers.DiscussionHandler,
            detection.SURVEY: handlers.SurveyHandler,
            detection.DIALOGUE: handlers.DialogueHandler,
        }
        for page_type, cls in expected.items():
            self.assertIsInstance(handlers.for_page_type(page_type), cls, page_type)

    def test_unknown_has_no_handler(self):
        self.assertIsNone(handlers.for_page_type(detection.UNKNOWN))

    def test_assignment_is_not_the_plugin_instance(self):
        # AssignmentHandler subclasses PluginHandler; a registry keyed on the
        # class rather than page_type would collapse them into one entry.
        self.assertIsNot(
            handlers.for_page_type(detection.ASSIGNMENT),
            handlers.for_page_type(detection.PLUGIN),
        )


class HandlerBehaviourTests(unittest.TestCase):
    """These paths were unreachable while classify() called everything READING."""

    def setUp(self):
        self.ctx = handlers.Context(settings=config.Settings())
        self.ctx.manager = FakeManager()
        self.page = FakePage(url="https://www.coursera.org/learn/c/ungradedWidget/aaa/ex1")
        patcher = mock.patch.object(handlers.navigation, "advance", return_value="NAVIGATED")
        self.advance = patcher.start()
        self.addCleanup(patcher.stop)

    def test_plugin_with_a_completion_control_dwells_then_marks_complete(self):
        with (
            mock.patch.object(handlers.schema, "first_visible", return_value=FakeLocator(count=1)),
            mock.patch.object(handlers.interaction, "reading_session") as dwell,
            mock.patch.object(handlers.navigation, "mark_complete") as mark,
        ):
            result = handlers.PluginHandler().handle(self.page, self.ctx)
        dwell.assert_called_once()
        mark.assert_called_once()
        self.assertEqual(result, handlers.CONTINUE)

    def test_plugin_without_a_completion_control_just_advances(self):
        with (
            mock.patch.object(handlers.schema, "first_visible", return_value=None),
            mock.patch.object(handlers.navigation, "mark_complete") as mark,
        ):
            handlers.PluginHandler().handle(self.page, self.ctx)
        mark.assert_not_called()
        self.advance.assert_called_once()

    def test_survey_advances_without_answering_or_archiving_it(self):
        with (
            mock.patch.object(handlers.navigation, "mark_complete") as mark,
            mock.patch.object(handlers.interaction, "reading_session") as dwell,
        ):
            result = handlers.SurveyHandler().handle(self.page, self.ctx)
        mark.assert_not_called()
        dwell.assert_not_called()
        self.assertEqual(self.ctx.manager.saved, [])
        self.advance.assert_called_once()
        self.assertEqual(result, handlers.CONTINUE)

    def test_assignment_advances_without_submitting_anything(self):
        with mock.patch.object(handlers.navigation, "mark_complete") as mark:
            result = handlers.AssignmentHandler().handle(self.page, self.ctx)
        mark.assert_not_called()
        self.assertEqual(self.ctx.manager.saved, [])
        self.advance.assert_called_once()
        self.assertEqual(result, handlers.CONTINUE)

    def test_assignment_waits_when_the_pause_is_requested(self):
        self.ctx.settings = config.Settings(pause_on_graded=True)
        with (
            mock.patch.object(handlers.navigation, "mark_complete") as mark,
            mock.patch.object(handlers.urls, "same_item", side_effect=[True, False]),
            mock.patch.object(handlers.time, "sleep") as sleep,
        ):
            result = handlers.AssignmentHandler().handle(self.page, self.ctx)
        mark.assert_not_called()
        self.assertEqual(self.ctx.manager.saved, [])
        self.advance.assert_not_called()
        sleep.assert_called_once_with(2)
        self.assertEqual(result, handlers.CONTINUE)

    def test_a_graded_quiz_advances_without_being_answered(self):
        with mock.patch.object(handlers.detection, "is_graded", return_value=True):
            result = handlers.QuizHandler().handle(self.page, self.ctx)
        self.assertEqual(self.ctx.manager.saved, [])
        self.advance.assert_called_once()
        self.assertEqual(result, handlers.CONTINUE)

    def test_a_graded_quiz_waits_until_the_item_is_left(self):
        # Quiz markup that never matches is not evidence the attempt is over:
        # a graded quiz sits behind a start screen. Absence used to end the
        # pause on the second poll, so --pause-on-graded announced a wait and
        # advanced about four seconds later. The wait now ends on navigation,
        # which is what AssignmentHandler has always done.
        self.ctx.settings = config.Settings(pause_on_graded=True)
        with (
            mock.patch.object(handlers.detection, "is_graded", return_value=True),
            mock.patch.object(handlers.QuizHandler, "_quiz_present", return_value=False),
            mock.patch.object(handlers, "_notify"),
            mock.patch.object(handlers.urls, "same_item", side_effect=[True, True, False, False]),
            mock.patch.object(handlers.time, "sleep"),
        ):
            result = handlers.QuizHandler().handle(self.page, self.ctx)
        self.assertEqual(self.ctx.manager.saved, [])
        self.advance.assert_not_called()
        self.assertEqual(result, handlers.CONTINUE)

    def test_a_graded_quiz_resumes_once_the_attempt_is_gone(self):
        # Markup seen and then absent for the confirmation count is a finished
        # attempt on the same URL, so the run advances itself.
        self.ctx.settings = config.Settings(pause_on_graded=True)
        with (
            mock.patch.object(handlers.detection, "is_graded", return_value=True),
            mock.patch.object(
                handlers.QuizHandler, "_quiz_present", side_effect=[True, False, False]
            ),
            mock.patch.object(handlers, "_notify"),
            mock.patch.object(handlers.time, "sleep"),
        ):
            result = handlers.QuizHandler().handle(self.page, self.ctx)
        self.advance.assert_called_once()
        self.assertEqual(result, handlers.CONTINUE)

    def test_discussion_archives_the_prompt(self):
        with mock.patch.object(handlers.page_ops, "extract_reading", return_value="Prompt text."):
            handlers.DiscussionHandler().handle(self.page, self.ctx)
        self.assertEqual(len(self.ctx.manager.saved), 1)
        self.assertEqual(self.ctx.manager.saved[0][2], "Discussion")

    def test_discussion_never_posts_a_reply(self):
        # Completing a discussion means publishing under the user's name.
        with (
            mock.patch.object(handlers.page_ops, "extract_reading", return_value="Prompt text."),
            mock.patch.object(handlers.navigation, "mark_complete") as mark,
        ):
            handlers.DiscussionHandler().handle(self.page, self.ctx)
        mark.assert_not_called()

    def test_ungraded_quiz_steps_past_without_answering(self):
        with mock.patch.object(handlers.detection, "is_graded", return_value=False):
            result = handlers.QuizHandler().handle(self.page, self.ctx)
        self.advance.assert_called_once()
        self.assertEqual(result, handlers.CONTINUE)

    def test_missing_reading_body_never_starts_a_reading_session(self):
        self.page.url = "https://www.coursera.org/learn/c/supplement/aaa/reading"
        with (
            mock.patch.object(handlers.page_ops, "extract_reading", return_value=None),
            mock.patch.object(handlers.page_ops, "detect_reading_minutes") as duration,
            mock.patch.object(handlers.interaction, "reading_session") as reading_session,
        ):
            with self.assertRaisesRegex(RuntimeError, "content"):
                handlers.ReadingHandler().handle(self.page, self.ctx)

        duration.assert_not_called()
        reading_session.assert_not_called()

    def test_a_narrated_reading_is_timed_by_its_audio(self):
        # The handler does the read: `interaction` cannot import the media
        # reader without a cycle, so the length is handed down to it.
        self.page.url = "https://www.coursera.org/learn/c/supplement/aaa/reading"
        with (
            mock.patch.object(handlers.page_ops, "extract_reading", return_value="Reading body"),
            mock.patch.object(
                handlers.page_ops, "detect_reading_minutes", return_value=(10, "sidebar")
            ),
            mock.patch.object(handlers.video, "narration_seconds", return_value=467.7),
            mock.patch.object(
                handlers.interaction, "reading_session", return_value="COMPLETED"
            ) as session,
            mock.patch.object(handlers.navigation, "mark_complete"),
        ):
            handlers.ReadingHandler().handle(self.page, self.ctx)

        self.assertEqual(session.call_args.args[1:], (10, 467.7))

    def test_a_plain_reading_reports_no_narration(self):
        self.page.url = "https://www.coursera.org/learn/c/supplement/aaa/reading"
        with (
            mock.patch.object(handlers.page_ops, "extract_reading", return_value="Reading body"),
            mock.patch.object(
                handlers.page_ops, "detect_reading_minutes", return_value=(10, "sidebar")
            ),
            mock.patch.object(handlers.video, "narration_seconds", return_value=0.0),
            mock.patch.object(
                handlers.interaction, "reading_session", return_value="COMPLETED"
            ) as session,
            mock.patch.object(handlers.navigation, "mark_complete"),
        ):
            handlers.ReadingHandler().handle(self.page, self.ctx)

        self.assertEqual(session.call_args.args[1:], (10, 0.0))

    def test_reading_navigation_does_not_complete_or_advance_the_new_page(self):
        self.page.url = "https://www.coursera.org/learn/c/supplement/aaa/reading"
        with (
            mock.patch.object(handlers.page_ops, "extract_reading", return_value="Reading body"),
            mock.patch.object(
                handlers.page_ops, "detect_reading_minutes", return_value=(1, "sidebar")
            ),
            mock.patch.object(handlers.interaction, "reading_session", return_value="NAVIGATED"),
            mock.patch.object(handlers.navigation, "mark_complete") as mark,
        ):
            result = handlers.ReadingHandler().handle(self.page, self.ctx)

        mark.assert_not_called()
        self.advance.assert_not_called()
        self.assertEqual(result, handlers.CONTINUE)

    @staticmethod
    def _video_mocks(watch_side_effect=None):
        """Stands in for everything a video handle does besides pausing."""
        return (
            mock.patch.object(
                handlers.page_ops, "extract_transcript", return_value=("Transcript text.", "panel")
            ),
            mock.patch.object(handlers.video, "mute_and_play"),
            mock.patch.object(handlers.video, "seek_into_range"),
            mock.patch.object(handlers.VideoHandler, "_watch", side_effect=watch_side_effect),
        )

    def _run_video_handle(self, watch_side_effect=None):
        """Runs VideoHandler.handle with the player paused-path observable."""
        with contextlib.ExitStack() as stack:
            for patcher in self._video_mocks(watch_side_effect):
                stack.enter_context(patcher)
            pause = stack.enter_context(mock.patch.object(handlers.video, "pause_if_playing"))
            handlers.VideoHandler().handle(self.page, self.ctx)
        return pause

    def test_a_finished_video_is_paused_before_the_run_advances(self):
        # Advancing while the player keeps running reads as an abandoned
        # session; the run stops playback like a person leaving the item.
        self.page.url = "https://www.coursera.org/learn/c/lecture/aaa/v"
        pause = self._run_video_handle()
        pause.assert_called_once()

    def test_a_video_abandoned_mid_watch_is_not_chased_to_pause(self):
        self.page.url = "https://www.coursera.org/learn/c/lecture/aaa/v"

        def navigate_away(*_args, **_kwargs):
            self.page.url = "https://www.coursera.org/learn/c/quiz/bbb/q"

        pause = self._run_video_handle(watch_side_effect=navigate_away)
        pause.assert_not_called()


class DialogueHandlerTests(unittest.TestCase):
    """The start/end/confirm sequence a roleplay item needs to count."""

    def setUp(self):
        self.ctx = handlers.Context(settings=config.Settings())
        self.ctx.manager = FakeManager()
        self.page = FakePage(url="https://www.coursera.org/learn/c/coach/gMS8D/practice-a-mix")
        patcher = mock.patch.object(handlers.navigation, "advance", return_value="NAVIGATED")
        self.advance = patcher.start()
        self.addCleanup(patcher.stop)
        sleeper = mock.patch.object(handlers.time, "sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)

    @staticmethod
    def _controls(present, appears_after=None):
        """Returns a ``first_visible`` stand-in and the click log it records.

        ``present`` names the controls the item has at all; ``appears_after``
        maps one onto the click that reveals it, which is how the live page
        behaves -- ``End Dialogue`` replaces ``Start Dialogue`` only once the
        dialogue is open, and ``Try again`` appears only once it is confirmed
        closed. A fixture that showed them all at once would let a handler
        that skipped a step pass.
        """
        clicked = []
        appears_after = appears_after or {}

        def first_visible(_page, category, element):
            if category != "dialogue" or element not in present:
                return None
            precondition = appears_after.get(element)
            if precondition is not None and precondition not in clicked:
                return None
            control = FakeLocator(count=1)
            # The click now travels through the shared interaction path, which
            # passes position/timeout kwargs a bare click did not.
            control.click = mock.Mock(side_effect=lambda **kwargs: clicked.append(element))
            return control

        return first_visible, clicked

    # How the live page reveals each control, in sequence.
    SEQUENCE = {"end": "start", "confirm_end": "end", "finished": "confirm_end"}

    def test_a_fresh_dialogue_is_started_ended_and_confirmed(self):
        first_visible, clicked = self._controls(
            {"start", "end", "confirm_end", "finished"}, self.SEQUENCE
        )
        with mock.patch.object(handlers.schema, "first_visible", side_effect=first_visible):
            result = handlers.DialogueHandler().handle(self.page, self.ctx)
        self.assertEqual(clicked, ["start", "end", "confirm_end"])
        self.advance.assert_called_once()
        self.assertEqual(result, handlers.CONTINUE)

    def test_an_already_ended_dialogue_is_stepped_past_untouched(self):
        first_visible, clicked = self._controls({"finished"})
        with mock.patch.object(handlers.schema, "first_visible", side_effect=first_visible):
            handlers.DialogueHandler().handle(self.page, self.ctx)
        self.assertEqual(clicked, [])
        self.advance.assert_called_once()

    def test_a_dialogue_left_running_is_ended_without_being_restarted(self):
        # No ``Start`` control survives once a dialogue is open, and polling for
        # one there would stall the item for the length of the retry budget.
        first_visible, clicked = self._controls(
            {"end", "confirm_end", "finished"}, {"confirm_end": "end", "finished": "confirm_end"}
        )
        with mock.patch.object(handlers.schema, "first_visible", side_effect=first_visible):
            handlers.DialogueHandler().handle(self.page, self.ctx)
        self.assertEqual(clicked, ["end", "confirm_end"])

    def test_a_dialogue_that_never_confirms_still_advances(self):
        # Leaving the run parked on an item it cannot finish is worse than
        # moving on: the sidebar keeps the item, and the next pass retries it.
        first_visible, clicked = self._controls({"start", "end"}, {"end": "start"})
        with mock.patch.object(handlers.schema, "first_visible", side_effect=first_visible):
            result = handlers.DialogueHandler().handle(self.page, self.ctx)
        self.assertEqual(clicked, ["start", "end"])
        self.advance.assert_called_once()
        self.assertEqual(result, handlers.CONTINUE)

    def test_nothing_is_archived_from_a_dialogue(self):
        first_visible, _clicked = self._controls(
            {"start", "end", "confirm_end", "finished"}, self.SEQUENCE
        )
        with mock.patch.object(handlers.schema, "first_visible", side_effect=first_visible):
            handlers.DialogueHandler().handle(self.page, self.ctx)
        self.assertEqual(self.ctx.manager.saved, [])


if __name__ == "__main__":
    unittest.main()


class AlreadyArchivedTests(unittest.TestCase):
    """An item the archive already holds is not scraped again.

    Extraction is not free -- a transcript costs a tab click and a scrape -- and
    re-running it rewrites the same file with the same words. Being archived is
    not the same as being complete, though, so the item is still worked.
    """

    def setUp(self):
        self.ctx = handlers.Context(settings=config.Settings())
        self.ctx.manager = FakeManager(archived=True)
        self.page = FakePage(url="https://www.coursera.org/learn/c/lecture/aaa/intro")
        patcher = mock.patch.object(handlers.navigation, "advance", return_value="NAVIGATED")
        self.advance = patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_archived_transcript_is_not_scraped_again_but_the_video_is_watched(self):
        with (
            mock.patch.object(handlers.page_ops, "extract_transcript") as extract,
            mock.patch.object(handlers.video, "mute_and_play") as play,
            mock.patch.object(handlers.video, "seek_into_range"),
            mock.patch.object(handlers.VideoHandler, "_watch"),
            mock.patch.object(handlers.video, "pause_if_playing"),
        ):
            handlers.VideoHandler().handle(self.page, self.ctx)
        extract.assert_not_called()
        self.assertEqual(self.ctx.manager.saved, [])
        play.assert_called_once()

    def test_an_archived_reading_is_still_dwelled_on_and_marked_complete(self):
        with (
            mock.patch.object(handlers.page_ops, "extract_reading", return_value="Body."),
            mock.patch.object(handlers.page_ops, "detect_reading_minutes", return_value=(1, "map")),
            mock.patch.object(handlers.interaction, "reading_session", return_value="COMPLETED"),
            mock.patch.object(handlers.navigation, "mark_complete") as mark,
        ):
            handlers.ReadingHandler().handle(self.page, self.ctx)
        self.assertEqual(self.ctx.manager.saved, [])
        mark.assert_called_once()

    def test_an_archived_reading_that_will_not_render_still_fails(self):
        # Being in the ledger says the text was read once, not that the page in
        # front of the run has any. Without the guard the run dwelled the listed
        # minutes on an empty page and then marked it complete.
        with (
            mock.patch.object(handlers.page_ops, "extract_reading", return_value=None),
            mock.patch.object(handlers.interaction, "reading_session") as session,
        ):
            with self.assertRaises(RuntimeError):
                handlers.ReadingHandler().handle(self.page, self.ctx)
        session.assert_not_called()

    def test_an_archived_discussion_prompt_is_not_saved_twice(self):
        with mock.patch.object(handlers.page_ops, "extract_reading") as extract:
            handlers.DiscussionHandler().handle(self.page, self.ctx)
        extract.assert_not_called()
        self.assertEqual(self.ctx.manager.saved, [])

    def test_an_unreadable_ledger_is_treated_as_not_archived(self):
        self.ctx.manager = mock.Mock(is_archived=mock.Mock(side_effect=RuntimeError("locked")))
        self.assertFalse(handlers.BaseHandler().already_archived(self.page, self.ctx))


class NextWorkSeamTests(unittest.TestCase):
    """Where a handler goes when it is done: the runner answers, or the platform."""

    def setUp(self):
        self.ctx = handlers.Context(settings=config.Settings())
        self.page = FakePage()

    def test_the_runners_answer_is_used_when_it_has_one(self):
        self.ctx.next_work = mock.Mock(return_value=handlers.COURSE_COMPLETE)
        with mock.patch.object(handlers.navigation, "advance") as advance:
            outcome = handlers.BaseHandler().advance(self.page, self.ctx, self.page.url)
        self.assertEqual(outcome, handlers.COURSE_COMPLETE)
        advance.assert_not_called()

    def test_no_answer_falls_back_to_the_next_button(self):
        self.ctx.next_work = mock.Mock(return_value=None)
        with mock.patch.object(handlers.navigation, "advance", return_value="NAVIGATED"):
            outcome = handlers.BaseHandler().advance(self.page, self.ctx, self.page.url)
        self.assertEqual(outcome, handlers.CONTINUE)

    def test_a_context_without_a_runner_still_advances(self):
        with mock.patch.object(handlers.navigation, "advance", return_value="NAVIGATED") as adv:
            handlers.BaseHandler().advance(self.page, self.ctx, self.page.url)
        adv.assert_called_once()

    def test_a_page_that_already_moved_is_not_asked_where_to_go(self):
        # Coursera hands off to the next item itself once an item completes, and
        # the user may click a different chapter at any moment. The runner would
        # answer from the item the handler *started* on and navigate back off
        # whatever is now open.
        start_url = self.page.url
        self.page.url = "/learn/course/lecture/xyz/somewhere-else"
        self.ctx.next_work = mock.Mock(return_value=handlers.COURSE_COMPLETE)
        with mock.patch.object(handlers.navigation, "advance", return_value="ALREADY_MOVED"):
            outcome = handlers.BaseHandler().advance(self.page, self.ctx, start_url)
        self.assertEqual(outcome, handlers.CONTINUE)
        self.ctx.next_work.assert_not_called()

    def test_an_unreadable_url_defers_rather_than_guessing(self):
        page = mock.Mock()
        type(page).url = mock.PropertyMock(side_effect=RuntimeError("tab gone"))
        self.ctx.next_work = mock.Mock(return_value=handlers.COURSE_COMPLETE)
        with mock.patch.object(handlers.navigation, "advance", return_value="FAILED"):
            handlers.BaseHandler().advance(page, self.ctx, "/learn/c/lecture/a/b")
        self.ctx.next_work.assert_not_called()


class PlatformRewoundSeekTests(unittest.TestCase):
    """A seek the platform undoes must be reported, not left claimed in the log.

    Some courses unlock seeking only once an item is complete: the player takes
    the seek, plays from it briefly, then puts the position back. The run watches
    the video through either way, so the only visible symptom was a log line
    announcing a skip that had not happened.
    """

    ITEM = "https://www.coursera.org/learn/c/lecture/aaa/one"

    def setUp(self):
        self.ctx = handlers.Context(settings=config.Settings())
        self.ctx.settings.video_completion_threshold = (99.0, 99.0)
        self.page = FakePage(url=self.ITEM)

    @staticmethod
    def _snapshot(position):
        """Returns a player state at ``position`` in a two-minute video."""
        return {"currentTime": position, "duration": 120.0, "paused": False, "ended": False}

    def _watch_through(self, positions, seeked):
        """Runs the watch loop over ``positions`` and returns the warnings raised."""
        states = [self._snapshot(position) for position in positions]
        with (
            mock.patch.object(handlers.video, "state", side_effect=states),
            mock.patch.object(handlers.modals, "dismiss_all"),
            mock.patch.object(handlers.VideoHandler, "_idle_fidget"),
            mock.patch.object(handlers.time, "sleep"),
            mock.patch.object(handlers.logs, "warn") as warn,
        ):
            handlers.VideoHandler()._watch(self.page, self.ctx, self.ITEM, seeked=seeked)
        return [call.args[0] for call in warn.call_args_list]

    def test_a_seek_the_platform_undoes_is_reported(self):
        warnings = self._watch_through([110.0, 2.0, 119.0], seeked=True)
        self.assertEqual(len(warnings), 1)
        self.assertIn("rewound", warnings[0])

    def test_the_rewind_is_reported_once_however_often_the_platform_repeats_it(self):
        warnings = self._watch_through([110.0, 2.0, 100.0, 3.0, 119.0], seeked=True)
        self.assertEqual(len(warnings), 1)

    def test_a_video_that_was_never_seeked_reports_no_rewind(self):
        self.assertEqual(self._watch_through([110.0, 2.0, 119.0], seeked=False), [])

    def test_ordinary_rebuffering_is_not_mistaken_for_a_rewind(self):
        drift = handlers.SEEK_REWIND_SECONDS - 1.0
        self.assertEqual(self._watch_through([110.0, 110.0 - drift, 119.0], seeked=True), [])
