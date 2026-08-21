"""Choosing the tab a command works in."""

import unittest
from unittest import mock

from phantadex import config, session
from tests.fakes import capture_console


class FakeTab:
    """A browser tab that records what was asked of it."""

    def __init__(self, url="", title="Tab"):
        self.url = url
        self._title = title
        self.goto_calls = []
        self.raised = False

    def goto(self, url, **_kwargs):
        self.goto_calls.append(url)
        self.url = url

    def title(self):
        return self._title

    def bring_to_front(self):
        self.raised = True


class FakeContext:
    """A browser context holding a fixed set of tabs."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.opened = 0

    def new_page(self):
        self.opened += 1
        tab = FakeTab()
        self.pages.append(tab)
        return tab


COURSE = "https://www.coursera.org/learn/x/lecture/AbCd/intro"
OTHER = "https://www.coursera.org/learn/y/supplement/EfGh/notes"


class CourseTabTests(unittest.TestCase):
    def _session(self, pages):
        attached = session.BrowserSession(config.CDP_URL)
        attached.context = FakeContext(pages)
        return attached

    def setUp(self):
        # The guard is a browser-side script; the choice of tab is what is under
        # test, and arming it against a fake tab would test the fake.
        patched = mock.patch.object(session.interaction, "arm_media_guard")
        patched.start()
        self.addCleanup(patched.stop)

    def test_the_only_course_tab_is_used_as_it_is(self):
        tab = FakeTab(COURSE)
        attached = self._session([FakeTab("https://example.com/"), tab])
        with capture_console() as console:
            self.assertIs(attached.find_course_page(), tab)
        self.assertTrue(tab.raised)
        self.assertEqual(tab.goto_calls, [])
        self.assertNotIn("course tabs are open", console.getvalue())

    def test_several_course_tabs_are_reported_rather_than_chosen_between_silently(self):
        first, second = FakeTab(COURSE, "Intro"), FakeTab(OTHER, "Notes")
        attached = self._session([first, second])
        with capture_console() as console:
            self.assertIs(attached.find_course_page(), first)
        output = console.getvalue()
        self.assertIn("2 course tabs are open", output)
        self.assertIn("Intro", output)

    def test_no_course_tab_is_reported_as_none(self):
        attached = self._session([FakeTab("https://example.com/")])
        self.assertIsNone(attached.find_course_page())

    def test_a_named_item_is_opened_in_the_tab_that_is_already_there(self):
        tab = FakeTab(OTHER)
        attached = self._session([tab])
        with capture_console():
            self.assertIs(attached.find_course_page(COURSE), tab)
        self.assertEqual(tab.goto_calls, [COURSE])
        self.assertTrue(tab.raised)

    def test_a_named_item_opens_a_tab_when_none_is_on_the_platform(self):
        attached = self._session([FakeTab("https://example.com/")])
        with capture_console():
            page = attached.find_course_page(COURSE)
        self.assertEqual(attached.context.opened, 1)
        self.assertEqual(page.goto_calls, [COURSE])

    def test_a_path_is_opened_as_a_full_link(self):
        tab = FakeTab(OTHER)
        attached = self._session([tab])
        with capture_console():
            attached.find_course_page("/learn/x/lecture/AbCd/intro")
        self.assertEqual(tab.goto_calls, [COURSE])

    def test_a_named_item_settles_which_of_several_tabs_is_meant(self):
        # No warning: naming the item is the answer to the question the
        # warning would have asked.
        first, second = FakeTab(OTHER), FakeTab(OTHER)
        attached = self._session([first, second])
        with capture_console() as console:
            self.assertIs(attached.find_course_page(COURSE), first)
        self.assertNotIn("course tabs are open", console.getvalue())


if __name__ == "__main__":
    unittest.main()
