"""Next-item navigation and ledger-fallback tests."""

import unittest
from unittest import mock

from phantadex import navigation
from tests.fakes import FakeLocator, FakePage

ITEM_A = "/learn/demo/lecture/aaa/one"
ITEM_B = "https://www.coursera.org/learn/demo/lecture/bbb/two"
ITEM_PREVIOUS = "https://www.coursera.org/learn/demo/lecture/zzz/previous"


class FakeManager:
    """Stands in for CourseManager with a fixed next-URL answer."""

    def __init__(self, next_url=ITEM_B, previous_url=ITEM_PREVIOUS, mapped=True):
        self._next_url = next_url
        self._previous_url = previous_url
        self._mapped = mapped
        self.calls = []

    def is_mapped(self, _current_url):
        return self._mapped

    def get_next_url(self, current_url):
        self.calls.append(current_url)
        return self._next_url

    def get_previous_url(self, current_url):
        self.calls.append(current_url)
        return self._previous_url


class AdvanceTests(unittest.TestCase):
    def setUp(self):
        for target in (navigation.time,):
            patcher = mock.patch.object(target, "sleep")
            patcher.start()
            self.addCleanup(patcher.stop)
        # Keep the "did the click actually move us?" wait from dominating runtime.
        for name, value in (("MOVE_TIMEOUT_SECONDS", 0.05), ("MOVE_POLL_SECONDS", 0.01)):
            patcher = mock.patch.object(navigation, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def _moves(page, destination=ITEM_B):
        """Click side effect that actually navigates, as a real Next button would."""

        def _click(*args, **kwargs):
            page.url = destination
            return True

        return _click

    def _patch_button(self, locator):
        patcher = mock.patch.object(navigation.schema, "first_visible", return_value=locator)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_clicks_next_button_when_available(self):
        self._patch_button(FakeLocator(count=1))
        page = FakePage(url=ITEM_A)
        with mock.patch.object(
            navigation.interaction, "click", side_effect=self._moves(page)
        ) as click:
            result = navigation.advance(page, FakeManager(), start_url=ITEM_A)
        self.assertEqual(result, "NAVIGATED")
        click.assert_called_once()
        self.assertEqual(page.goto_calls, [])  # the button did the work

    def test_next_click_that_does_not_move_falls_back_to_ledger(self):
        # Regression: a visible, enabled Next button is not proof of navigation.
        # Reporting NAVIGATED here made the runner re-process the same item on
        # every tick until the stuck detector eventually rescued it.
        self._patch_button(FakeLocator(count=1))
        page = FakePage(url=ITEM_A)
        manager = FakeManager()
        with mock.patch.object(navigation.interaction, "click", return_value=True):
            result = navigation.advance(page, manager, start_url=ITEM_A)
        self.assertEqual(result, "NAVIGATED")
        self.assertEqual(page.goto_calls, [ITEM_B])

    def test_falls_back_to_ledger_when_click_fails(self):
        self._patch_button(FakeLocator(count=1))
        page = FakePage(url=ITEM_A)
        manager = FakeManager()
        with mock.patch.object(navigation.interaction, "click", return_value=False):
            result = navigation.advance(page, manager, start_url=ITEM_A)
        self.assertEqual(result, "NAVIGATED")
        self.assertEqual(page.goto_calls, [ITEM_B])

    def test_falls_back_when_no_button_exists(self):
        self._patch_button(None)
        page = FakePage(url=ITEM_A)
        result = navigation.advance(page, FakeManager(), start_url=ITEM_A)
        self.assertEqual(result, "NAVIGATED")
        self.assertEqual(page.goto_calls, [ITEM_B])

    def test_reports_course_complete_at_the_end(self):
        self._patch_button(None)
        result = navigation.advance(
            FakePage(url=ITEM_A), FakeManager(next_url=None), start_url=ITEM_A
        )
        self.assertEqual(result, "COURSE_COMPLETE")

    def test_fails_cleanly_without_a_manager(self):
        self._patch_button(None)
        result = navigation.advance(FakePage(url=ITEM_A), None, start_url=ITEM_A)
        self.assertEqual(result, "FAILED")

    def test_does_not_double_navigate(self):
        # The page already moved on its own; advancing again would skip an item.
        self._patch_button(FakeLocator(count=1))
        page = FakePage(url="/learn/demo/lecture/ccc/three")
        with mock.patch.object(navigation.interaction, "click") as click:
            result = navigation.advance(page, FakeManager(), start_url=ITEM_A)
        self.assertEqual(result, "ALREADY_MOVED")
        click.assert_not_called()

    def test_start_url_tolerates_query_parameters(self):
        self._patch_button(FakeLocator(count=1))
        page = FakePage(url=f"https://www.coursera.org{ITEM_A}?utm=1")
        with mock.patch.object(navigation.interaction, "click", side_effect=self._moves(page)):
            result = navigation.advance(page, FakeManager(), start_url=ITEM_A)
        self.assertEqual(result, "NAVIGATED")
        self.assertEqual(page.goto_calls, [])


class NextButtonTests(unittest.TestCase):
    def test_returns_none_without_selectors(self):
        with mock.patch.object(navigation.schema, "first_visible", return_value=None):
            self.assertIsNone(navigation.next_button(FakePage()))

    def test_asks_for_the_navigation_next_item_element(self):
        # Regression: this used to comma-join the selectors into one locator,
        # which matches in DOM order and so discarded the verified-first
        # priority that schema.selectors_for establishes.
        button = FakeLocator(count=1)
        with mock.patch.object(
            navigation.schema, "first_visible", return_value=button
        ) as first_visible:
            self.assertIs(navigation.next_button(FakePage()), button)
        first_visible.assert_called_once_with(mock.ANY, "navigation", "next_item")


class RetreatTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("MOVE_TIMEOUT_SECONDS", 0.02), ("MOVE_POLL_SECONDS", 0.01)):
            patcher = mock.patch.object(navigation, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(navigation.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_previous_click_that_does_not_move_falls_back_to_map(self):
        page = FakePage(url=ITEM_A)
        manager = FakeManager()
        with (
            mock.patch.object(
                navigation.schema, "first_visible", return_value=FakeLocator(count=1)
            ),
            mock.patch.object(navigation.interaction, "click", return_value=True),
        ):
            result = navigation.retreat(page, manager, start_url=ITEM_A)

        self.assertEqual(result, "NAVIGATED")
        self.assertEqual(page.goto_calls, [ITEM_PREVIOUS])

    def test_missing_button_uses_previous_mapped_url(self):
        page = FakePage(url=ITEM_A)
        with mock.patch.object(navigation.schema, "first_visible", return_value=None):
            result = navigation.retreat(page, FakeManager(), start_url=ITEM_A)
        self.assertEqual(result, "NAVIGATED")
        self.assertEqual(page.goto_calls, [ITEM_PREVIOUS])


class MarkCompleteTests(unittest.TestCase):
    def test_clicks_the_visible_completion_button(self):
        button = FakeLocator(count=1)
        with (
            mock.patch.object(navigation.schema, "first_visible", return_value=button),
            mock.patch.object(navigation.interaction, "click", return_value=True) as click,
        ):
            self.assertTrue(navigation.mark_complete(FakePage()))
        click.assert_called_once()

    def test_returns_false_when_the_completion_button_is_absent(self):
        with mock.patch.object(navigation.schema, "first_visible", return_value=None):
            self.assertFalse(navigation.mark_complete(FakePage()))

    def test_skips_invisible_candidates(self):
        # Visibility filtering now lives in schema.first_visible; see
        # tests/test_schema.py::FirstVisibleTests.
        with mock.patch.object(navigation.schema, "first_visible", return_value=None):
            self.assertIsNone(navigation.next_button(FakePage()))


if __name__ == "__main__":
    unittest.main()
