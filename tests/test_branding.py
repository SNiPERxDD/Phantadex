"""Executable contracts for the Phantadex product names and entry points."""

import subprocess
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from phantadex import archive, config, handlers, interaction, runner, session
from tests.fakes import FakePage

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeBrowserSession:
    """Minimal context manager shared by Watch and Archive tests."""

    def __init__(self, page):
        self.page = page

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def find_course_page(self):
        return self.page

    @staticmethod
    def reclaim(page):
        return page


class FakeCourseManager:
    """Archive state needed to reach the mode and ledger output."""

    root_dir = "phantadex_archive/Demo"
    xml_path = "phantadex_archive/Demo/demo.pdex.xml"

    @staticmethod
    def archived_paths():
        return set()


class FakeContext:
    """Browser context stub: enough surface for the media guard install."""

    def __init__(self, pages=()):
        self.pages = list(pages)
        self.init_scripts = []

    def add_init_script(self, script=None, path=None):
        self.init_scripts.append(script or path)


class FakePlaywright:
    """In-process CDP boundary fake for the Link label check."""

    def __init__(self):
        browser = type("Browser", (), {"contexts": [FakeContext()]})()
        self.chromium = type(
            "Chromium",
            (),
            {"connect_over_cdp": staticmethod(lambda _url: browser)},
        )()

    def start(self):
        return self

    def stop(self):
        return None


class BrowserSessionCleanupTests(unittest.TestCase):
    def test_no_context_connection_stops_playwright_before_raising(self):
        playwright = mock.Mock()
        playwright.chromium.connect_over_cdp.return_value = type("Browser", (), {"contexts": []})()
        with mock.patch.object(
            session, "sync_playwright", return_value=mock.Mock(start=lambda: playwright)
        ):
            with self.assertRaises(RuntimeError):
                session.BrowserSession("http://localhost:9222").__enter__()
        playwright.stop.assert_called_once_with()

    def test_connected_browser_disconnects_before_playwright_stops(self):
        events = []
        browser = type(
            "Browser",
            (),
            {
                "contexts": [FakeContext()],
                "close": lambda _self, **_kwargs: events.append("browser.close"),
            },
        )()
        playwright = mock.Mock()
        playwright.chromium.connect_over_cdp.return_value = browser
        playwright.stop.side_effect = lambda: events.append("playwright.stop")

        with mock.patch.object(
            session, "sync_playwright", return_value=mock.Mock(start=lambda: playwright)
        ):
            with session.BrowserSession("http://localhost:9222"):
                pass

        self.assertEqual(events, ["browser.close", "playwright.stop"])


class MediaGuardTests(unittest.TestCase):
    def _attach(self, context):
        browser = type("Browser", (), {"contexts": [context], "close": lambda _s, **_k: None})()
        playwright = mock.Mock()
        playwright.chromium.connect_over_cdp.return_value = browser
        with mock.patch.object(
            session, "sync_playwright", return_value=mock.Mock(start=lambda: playwright)
        ):
            with session.BrowserSession("http://localhost:9222"):
                pass

    def test_attaching_installs_the_guard_for_later_documents(self):
        context = FakeContext()
        self._attach(context)
        self.assertEqual(len(context.init_scripts), 1)
        self.assertIn("__phantadexMediaGuard", context.init_scripts[0])

    def test_only_platform_tabs_already_open_are_armed(self):
        course = mock.Mock(url="https://www.coursera.org/learn/demo/lecture/aaa/welcome")
        elsewhere = mock.Mock(url="https://example.com/watch")
        self._attach(FakeContext(pages=[course, elsewhere]))
        self.assertEqual(
            [call.args[0] for call in course.evaluate.call_args_list],
            [interaction._MEDIA_GUARD_JS, interaction._MEDIA_RELEASE_JS],
        )
        elsewhere.evaluate.assert_not_called()

    def test_leaving_the_session_hands_muting_back(self):
        # The tab outlives the run. A guard left in place would keep re-muting
        # anything the user unmutes until they reload the page.
        course = mock.Mock(url="https://www.coursera.org/learn/demo/lecture/aaa/welcome")
        self._attach(FakeContext(pages=[course]))
        self.assertEqual(course.evaluate.call_args.args[0], interaction._MEDIA_RELEASE_JS)


class EntryPointTests(unittest.TestCase):
    def _help(self, script_name):
        return subprocess.run(
            [sys.executable, script_name, "--help"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_watch_entry_point_runs_and_names_the_mode(self):
        result = self._help("phantadex_watch.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Phantadex Watch", result.stdout)

    def test_archive_entry_point_runs_and_names_the_mode(self):
        result = self._help("phantadex_archive.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Phantadex Archive", result.stdout)


class RuntimeVocabularyTests(unittest.TestCase):
    def test_watch_banner_names_the_mode(self):
        settings = config.Settings()
        page = FakePage()
        fake_session = FakeBrowserSession(page)

        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(runner, "BrowserSession", return_value=fake_session)
            )
            stack.enter_context(
                mock.patch.object(
                    runner.Runner,
                    "_tick",
                    return_value=handlers.COURSE_COMPLETE,
                )
            )
            banner = stack.enter_context(mock.patch.object(runner.logs, "banner"))
            runner.Runner(settings).run()

        banner.assert_called_once_with("Phantadex Watch", settings.describe())

    def test_archive_names_the_mode_and_dex(self):
        page = FakePage()
        fake_session = FakeBrowserSession(page)

        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(archive, "BrowserSession", return_value=fake_session)
            )
            stack.enter_context(
                mock.patch.object(archive, "get_robust_course_name", return_value="Demo Course")
            )
            stack.enter_context(
                mock.patch.object(
                    archive,
                    "get_detailed_course_map",
                    return_value={"Module 1": []},
                )
            )
            stack.enter_context(
                mock.patch.object(archive, "CourseManager", return_value=FakeCourseManager())
            )
            banner = stack.enter_context(mock.patch.object(archive.logs, "banner"))
            step = stack.enter_context(mock.patch.object(archive.logs, "step"))
            result = archive.run(config.Settings())

        self.assertEqual(result, 0)
        banner.assert_any_call("Phantadex Archive", "Demo Course")
        step.assert_any_call("Phantadex Dex · ledger phantadex_archive/Demo/demo.pdex.xml")

    def test_archive_waits_when_the_course_name_is_not_loaded(self):
        page = FakePage()
        fake_session = FakeBrowserSession(page)

        with (
            mock.patch.object(archive, "BrowserSession", return_value=fake_session),
            mock.patch.object(archive, "get_robust_course_name", return_value=None),
            mock.patch.object(archive, "get_detailed_course_map", return_value={"Module": []}),
            mock.patch.object(archive, "CourseManager") as manager,
        ):
            result = archive.run(config.Settings())

        self.assertEqual(result, 1)
        manager.assert_not_called()

    def test_link_names_the_connection_layer(self):
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(session, "sync_playwright", return_value=FakePlaywright())
            )
            step = stack.enter_context(mock.patch.object(session.logs, "step"))
            with session.BrowserSession("http://localhost:9222"):
                pass

        step.assert_called_once_with(
            "Phantadex Link · connecting to Chrome on http://localhost:9222"
        )


if __name__ == "__main__":
    unittest.main()
