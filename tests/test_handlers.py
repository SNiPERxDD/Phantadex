"""Handler dispatch and the paths a live run rarely reaches."""

import unittest
from unittest import mock

from phantadex import config, detection, handlers
from tests.fakes import FakeLocator, FakePage


class FakeManager:
    def __init__(self):
        self.saved = []

    def is_archived(self, _url):
        return False

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

    def test_a_graded_quiz_waits_when_the_pause_is_requested(self):
        self.ctx.settings = config.Settings(pause_on_graded=True)
        with (
            mock.patch.object(handlers.detection, "is_graded", return_value=True),
            mock.patch.object(handlers.QuizHandler, "_quiz_present", return_value=False),
            mock.patch.object(handlers, "_notify"),
            mock.patch.object(handlers.time, "sleep"),
        ):
            result = handlers.QuizHandler().handle(self.page, self.ctx)
        self.assertEqual(self.ctx.manager.saved, [])
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


if __name__ == "__main__":
    unittest.main()
