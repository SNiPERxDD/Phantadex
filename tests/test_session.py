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

    def test_a_tab_inside_a_course_beats_one_merely_on_the_platform(self):
        # The catalogue and the enrolment list are the same host but carry no
        # course to read; picking one leaves the run with nothing to attach to.
        elsewhere = FakeTab("https://www.coursera.org/my-learning", "My Learning")
        inside = FakeTab(COURSE, "Intro")
        attached = self._session([elsewhere, inside])
        with capture_console() as console:
            self.assertIs(attached.find_course_page(), inside)
        self.assertNotIn("course tabs are open", console.getvalue())

    def test_a_platform_tab_outside_a_course_is_still_returned_when_it_is_all_there_is(self):
        # Returned rather than reported as no tab at all, so the caller can say
        # what it found and point at the way out.
        elsewhere = FakeTab("https://www.coursera.org/my-learning", "My Learning")
        attached = self._session([elsewhere])
        with capture_console():
            self.assertIs(attached.find_course_page(), elsewhere)

    def test_several_off_course_tabs_are_not_described_as_course_tabs(self):
        # The next line the run prints says the tab is not inside a course, so
        # naming them course tabs contradicts it.
        first = FakeTab("https://www.coursera.org/my-learning", "My Learning")
        second = FakeTab("https://www.coursera.org/", "Coursera")
        attached = self._session([first, second])
        with capture_console() as console:
            self.assertIs(attached.find_course_page(), first)
        output = console.getvalue()
        self.assertIn("2 platform tabs outside any course are open", output)

    def test_a_named_item_settles_which_of_several_tabs_is_meant(self):
        # No warning: naming the item is the answer to the question the
        # warning would have asked.
        first, second = FakeTab(OTHER), FakeTab(OTHER)
        attached = self._session([first, second])
        with capture_console() as console:
            self.assertIs(attached.find_course_page(COURSE), first)
        self.assertNotIn("course tabs are open", console.getvalue())


class AttachFailureDiagnosisTests(unittest.TestCase):
    """The message shown when Chrome cannot be attached to."""

    def _diagnose(self, cdp_url, detail):
        return session.BrowserSession(cdp_url)._diagnose(RuntimeError(detail))

    def test_a_refused_connection_names_the_launcher_that_ships(self):
        # `scripts/` is not in the wheel, so an index install has no such path.
        message = self._diagnose(config.CDP_URL, "connect ECONNREFUSED 127.0.0.1:9222")
        self.assertIn("pdex chrome", message)
        self.assertNotIn("scripts/", message)

    def test_the_port_check_follows_the_endpoint_that_was_configured(self):
        # A hard-coded 9222 sends the user to look at a port they are not using.
        message = self._diagnose(
            "http://127.0.0.1:9333", "Browser context management is not supported"
        )
        self.assertIn("9333", message)
        self.assertNotIn("9222", message)

    def test_the_port_check_uses_a_command_the_platform_has(self):
        attached = session.BrowserSession("http://127.0.0.1:9333")
        with mock.patch.object(session.os, "name", "nt"):
            self.assertIn("netstat", attached._port_check_hint())
        with mock.patch.object(session.os, "name", "posix"):
            self.assertIn("lsof", attached._port_check_hint())


if __name__ == "__main__":
    unittest.main()
