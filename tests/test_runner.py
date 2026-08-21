"""Tests for the traversal loop's own decisions (not the per-type handlers)."""

import unittest
from unittest import mock

from phantadex import config, detection, runner
from tests.fakes import FakePage


class FakeManager:
    """A course manager that records what was saved and what it already holds."""

    def __init__(self, archived=False):
        self._archived = archived
        self.saved = []

    def is_archived(self, _url):
        return self._archived

    def save_content(self, url, text, content_type):
        self.saved.append((url, text, content_type))
        return "Item_Reading.txt", True


class ArchiveBeforeSkipTests(unittest.TestCase):
    """A completed reading is still worth archiving before moving past it."""

    def setUp(self):
        self.runner = runner.Runner(config.Settings())
        self.page = FakePage(url="https://www.coursera.org/learn/c/supplement/abc/read")

    def _skip(self, manager, text="Body text."):
        self.runner.ctx.manager = manager
        with mock.patch.object(runner.page_ops, "extract_reading", return_value=text):
            self.runner._archive_before_skip(self.page, detection.READING)

    def test_unarchived_reading_is_saved(self):
        manager = FakeManager(archived=False)
        self._skip(manager)
        self.assertEqual(len(manager.saved), 1)
        self.assertEqual(manager.saved[0][1], "Body text.")
        self.assertEqual(manager.saved[0][2], "Reading")

    def test_reading_already_in_the_ledger_is_not_saved_twice(self):
        manager = FakeManager(archived=True)
        self._skip(manager)
        self.assertEqual(manager.saved, [])

    def test_non_reading_types_are_left_alone(self):
        manager = FakeManager(archived=False)
        self.runner.ctx.manager = manager
        with mock.patch.object(runner.page_ops, "extract_reading", return_value="x"):
            self.runner._archive_before_skip(self.page, detection.PLUGIN)
        self.assertEqual(manager.saved, [])

    def test_persistent_handler_failure_advances_after_three_attempts(self):
        current = FakePage(url="https://www.coursera.org/learn/c/lecture/abc/item")
        watch = runner.Runner(config.Settings())
        broken = mock.Mock()
        broken.handle.side_effect = RuntimeError("detached DOM")
        with (
            mock.patch.object(watch, "_detect_stuck", return_value=False),
            mock.patch.object(watch, "_sync_course_map"),
            mock.patch.object(watch, "_log_context"),
            mock.patch.object(runner.modals, "dismiss_all"),
            mock.patch.object(runner.detection, "classify", return_value=detection.VIDEO),
            mock.patch.object(runner.handlers, "for_page_type", return_value=broken),
            mock.patch.object(watch, "_skip_completed", return_value="PROCEED"),
            mock.patch.object(runner.navigation, "advance", return_value="NAVIGATED") as advance,
            mock.patch.object(runner.time, "sleep"),
        ):
            for _ in range(3):
                result = watch._tick(current)
        self.assertEqual(result, runner.handlers.CONTINUE)
        advance.assert_called_once()

    def test_locked_item_retreats_before_classification(self):
        watch = runner.Runner(config.Settings())
        watch.ctx.manager = FakeManager()
        page = FakePage(url="https://www.coursera.org/learn/c/supplement/abc/locked")
        with (
            mock.patch.object(watch, "_detect_stuck", return_value=False),
            mock.patch.object(watch, "_sync_course_map"),
            mock.patch.object(watch, "_log_context"),
            mock.patch.object(runner.page_ops, "is_locked_item", return_value=True),
            mock.patch.object(runner.navigation, "retreat", return_value="NAVIGATED") as retreat,
            mock.patch.object(runner.detection, "classify") as classify,
        ):
            result = watch._tick(page)

        self.assertEqual(result, runner.handlers.CONTINUE)
        retreat.assert_called_once_with(page, watch.ctx.manager, start_url=page.url)
        classify.assert_not_called()

    def test_empty_body_is_not_written(self):
        manager = FakeManager(archived=False)
        self._skip(manager, text="")
        self.assertEqual(manager.saved, [])

    def test_extraction_failure_does_not_block_the_skip(self):
        manager = FakeManager(archived=False)
        self.runner.ctx.manager = manager
        with mock.patch.object(
            runner.page_ops, "extract_reading", side_effect=RuntimeError("detached")
        ):
            self.runner._archive_before_skip(self.page, detection.READING)
        self.assertEqual(manager.saved, [])

    def test_no_course_map_is_a_no_op(self):
        self.runner.ctx.manager = None
        with mock.patch.object(runner.page_ops, "extract_reading") as extract:
            self.runner._archive_before_skip(self.page, detection.READING)
        extract.assert_not_called()


if __name__ == "__main__":
    unittest.main()
