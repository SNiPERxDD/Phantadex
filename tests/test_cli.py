"""Package CLI contracts for Dex, Watch, Archive, Skip, and module execution."""

import importlib
import importlib.util
import io
import logging
import os
import re
import subprocess
import sys
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import phantadex
from phantadex import cli, config, course_manager, handlers, logs, overview
from tests.fakes import capture_console

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI_SPEC = importlib.util.find_spec("phantadex.cli")


class FakeBrowserSession:
    """Context manager exposing one controlled page."""

    def __init__(self, page):
        self.page = page

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def find_course_page(self, course_url=""):
        self.course_url = course_url
        return self.page


class FakeVideoPage:
    """HTML video boundary fake that records the requested seek target."""

    url = "https://www.coursera.org/learn/demo/lecture/abc/video"

    def __init__(self, duration=200.0, current_time=10.0):
        self.duration = duration
        self.current_time = current_time
        self.seek_target = None

    def evaluate(self, script, *args):
        if "return {" in script:
            return {
                "duration": self.duration,
                "currentTime": self.current_time,
                "paused": False,
                "ended": False,
                "muted": False,
            }
        if "targetSeconds" in script:
            # The script is handed ``[selector, targetSeconds]``: one argument,
            # because that is all ``page.evaluate`` passes.
            self.seek_target = args[0][1]
            self.current_time = self.seek_target
            return True
        return None


class PackageCliTests(unittest.TestCase):
    def _load_cli(self):
        self.assertIsNotNone(CLI_SPEC, "phantadex.cli is missing")
        return importlib.import_module("phantadex.cli")

    def _run_dex(self, argv):
        cli = self._load_cli()
        course_map = {
            "Module 1": [("Introduction", "VIDEO", "/learn/demo/lecture/abc/video", "2 min")]
        }
        fake_session = FakeBrowserSession(object())
        self.fake_session = fake_session
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(cli, "BrowserSession", return_value=fake_session))
            stack.enter_context(
                mock.patch.object(
                    cli.discovery,
                    "get_robust_course_name",
                    return_value="Demo Course",
                )
            )
            stack.enter_context(
                mock.patch.object(
                    cli.discovery,
                    "get_detailed_course_map",
                    return_value=course_map,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    cli.discovery,
                    "get_completion_status",
                    return_value={"/learn/demo/lecture/abc/video": True},
                    create=True,
                )
            )
            with capture_console() as output:
                result = cli.main(argv)
        return result, output.getvalue()

    def test_no_command_defaults_to_dex_tree(self):
        result, output = self._run_dex([])
        self.assertEqual(result, 0)
        self.assertIn("Phantadex Dex", output)
        self.assertIn("✓ Introduction · video · 2 min", output)
        self.assertIn("Introduction", output)

    def test_explicit_dex_prints_course_tree(self):
        result, output = self._run_dex(["dex"])
        self.assertEqual(result, 0)
        self.assertIn("Demo Course", output)

    def test_a_bare_course_link_reads_that_course(self):
        # ``pdex <url>`` is the default command pointed at an item, so a link
        # can be pasted straight in without naming 'dex' first.
        link = "https://www.coursera.org/learn/demo/lecture/abc/video"
        result, _ = self._run_dex([link])
        self.assertEqual(result, 0)
        self.assertEqual(self.fake_session.course_url, link)

    def test_a_named_item_reaches_the_session_from_any_command(self):
        result, _ = self._run_dex(["dex", "/learn/demo/lecture/abc/video"])
        self.assertEqual(result, 0)
        self.assertEqual(self.fake_session.course_url, "/learn/demo/lecture/abc/video")

    def test_no_link_leaves_the_open_tab_alone(self):
        self._run_dex([])
        self.assertEqual(self.fake_session.course_url, "")

    def test_a_mistyped_command_is_not_mistaken_for_a_link(self):
        cli = self._load_cli()
        errors = io.StringIO()
        with redirect_stderr(errors):
            self.assertEqual(cli.main(["wach"]), 2)
        self.assertIn("unknown command", errors.getvalue())

    def test_default_archive_root_is_phantadex_branded(self):
        cli = self._load_cli()
        self.assertEqual(cli.config.Settings().transcript_dir, "phantadex_archive")

    def test_skip_seeks_once_inside_default_random_interval(self):
        cli = self._load_cli()
        page = FakeVideoPage()
        fake_session = FakeBrowserSession(page)

        with mock.patch.object(cli, "BrowserSession", return_value=fake_session):
            result = cli.main(["skip"])

        self.assertEqual(result, 0)
        self.assertGreaterEqual(page.seek_target, 195.0)
        self.assertLessEqual(page.seek_target, 197.0)

    def test_skip_accepts_custom_interval(self):
        cli = self._load_cli()
        page = FakeVideoPage()
        fake_session = FakeBrowserSession(page)

        with mock.patch.object(cli, "BrowserSession", return_value=fake_session):
            result = cli.main(["skip", "--video-skip-range", "90-95%"])

        self.assertEqual(result, 0)
        self.assertGreaterEqual(page.seek_target, 180.0)
        self.assertLessEqual(page.seek_target, 190.0)

    def _watch_settings(self, *argv):
        """Runs ``pdex watch`` with the runner stubbed and returns the settings."""
        cli = self._load_cli()
        captured = []

        with mock.patch.object(cli.watch.runner, "run", side_effect=captured.append):
            result = cli.main(["watch", *argv])

        self.assertEqual(result, 0)
        return captured[0]

    def test_watch_plays_videos_through_by_default(self):
        settings = self._watch_settings()

        self.assertEqual(settings.video_skip_range, "")
        self.assertEqual(settings.video_completion_threshold, (98.0, 100.0))
        self.assertEqual(settings.reading_default_minutes, (7, 12))

    def test_watch_accepts_a_threshold_range(self):
        settings = self._watch_settings("--video-threshold", "90-95")

        self.assertEqual(settings.video_completion_threshold, (90.0, 95.0))
        self.assertIn("threshold=90-95%", settings.describe())

    def test_watch_accepts_a_fixed_threshold(self):
        settings = self._watch_settings("--video-threshold", "95")

        self.assertEqual(settings.video_completion_threshold, (95.0, 95.0))
        self.assertIn("threshold=95%", settings.describe())

    def test_watch_skip_flag_turns_the_seek_on(self):
        settings = self._watch_settings("--skip")

        self.assertEqual(settings.video_skip_range, "97.5-98.5%")

    def test_naming_a_range_implies_the_skip(self):
        settings = self._watch_settings("--video-skip-range", "90-95%")

        self.assertEqual(settings.video_skip_range, "90-95%")

    def test_no_video_skip_overrides_the_skip_flag(self):
        settings = self._watch_settings("--skip", "--no-video-skip")

        self.assertEqual(settings.video_skip_range, "")

    def test_watch_accepts_custom_reading_minute_range(self):
        cli = self._load_cli()
        captured = []

        with mock.patch.object(cli.watch.runner, "run", side_effect=captured.append):
            try:
                result = cli.main(["watch", "--reading-minutes", "3-6"])
            except SystemExit as exc:
                self.fail(f"reading range was rejected with exit code {exc.code}")

        self.assertEqual(result, 0)
        self.assertEqual(captured[0].reading_default_minutes, (3, 6))
        self.assertIn("reading=3-6m", captured[0].describe())

    def test_watch_accepts_fixed_reading_minutes(self):
        cli = self._load_cli()
        captured = []

        with mock.patch.object(cli.watch.runner, "run", side_effect=captured.append):
            result = cli.main(["watch", "--reading-minutes", "5"])

        self.assertEqual(result, 0)
        self.assertEqual(captured[0].reading_default_minutes, (5, 5))

    def test_watch_rejects_reversed_reading_minute_range(self):
        cli = self._load_cli()

        with (
            mock.patch.object(cli.watch.runner, "run") as run,
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            cli.main(["watch", "--reading-minutes", "6-3"])

        self.assertEqual(raised.exception.code, 2)
        run.assert_not_called()

    def test_archive_forwards_force_to_bulk_archiver(self):
        cli = self._load_cli()
        captured = []

        def record_run(settings, force=False):
            captured.append((settings, force))
            return 0

        with mock.patch.object(cli.archive, "run", side_effect=record_run):
            result = cli.main(["archive", "--force"])

        self.assertEqual(result, 0)
        self.assertTrue(captured[0][1])

    def test_package_module_exposes_all_commands(self):
        result = subprocess.run(
            [sys.executable, "-m", "phantadex", "--help"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("dex", "skip", "watch", "archive", "discover", "chrome"):
            self.assertIn(command, result.stdout)

    def test_chrome_starts_the_debug_browser(self):
        cli = self._load_cli()
        with mock.patch.object(cli.chrome, "main", return_value=0) as launcher:
            self.assertEqual(cli.main(["chrome", "--cdp-url", "http://localhost:9444"]), 0)

        launcher.assert_called_once_with(["--cdp-url", "http://localhost:9444"])

    def test_the_version_flag_reports_the_package_version(self):
        # Returns rather than raising SystemExit: --version is handled before
        # any parser is built, so there is no argparse exit to propagate.
        cli = self._load_cli()
        stream = io.StringIO()
        with redirect_stdout(stream):
            result = cli.main(["--version"])

        self.assertEqual(result, 0)
        self.assertIn(cli.__version__, stream.getvalue())

    def test_discover_runs_the_selector_pass_against_the_given_endpoint(self):
        cli = self._load_cli()
        with mock.patch.object(cli.discovery, "start_dynamic_observation") as observe:
            result = cli.main(["discover", "--cdp-url", "http://localhost:9333"])

        self.assertEqual(result, 0)
        observe.assert_called_once_with("http://localhost:9333", "")

    def test_discover_honours_the_verbosity_flag(self):
        cli = self._load_cli()
        with (
            mock.patch.object(cli.discovery, "start_dynamic_observation"),
            mock.patch.object(cli.logs, "setup") as setup,
        ):
            cli.main(["discover", "-v"])

        setup.assert_called_once_with("DEBUG")

    def test_stop_terminates_every_running_process(self):
        cli = self._load_cli()
        with mock.patch.object(cli.processes, "stop_all", return_value=0) as stop_all:
            result = cli.main(["stop"])

        self.assertEqual(result, 0)
        stop_all.assert_called_once_with()

    def test_stop_list_only_reports(self):
        cli = self._load_cli()
        with (
            mock.patch.object(cli.processes, "stop_all", side_effect=AssertionError("stopped")),
            mock.patch.object(cli.processes, "list_runs", return_value=0) as list_runs,
        ):
            result = cli.main(["stop", "--list"])

        self.assertEqual(result, 0)
        list_runs.assert_called_once_with()

    def test_stop_accepts_the_global_options_every_other_command_takes(self):
        # Stopping reads nothing from the browser, but a hand-rolled parser
        # here made the one command reached for when a run has gone wrong the
        # one that rejected the endpoint flag the user had typed all session.
        cli = self._load_cli()
        with mock.patch.object(cli.processes, "stop_all", return_value=0) as stop_all:
            result = cli.main(["stop", "--cdp-url", "http://localhost:9333", "-q"])

        self.assertEqual(result, 0)
        stop_all.assert_called_once_with()

    def test_full_alias_names_itself_in_help(self):
        cli = self._load_cli()
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["phantadex"]), redirect_stdout(output):
            result = cli.main(["--help"])
        self.assertEqual(result, 0)
        self.assertIn("phantadex [command] [options]", output.getvalue())
        self.assertNotIn("pdex ", output.getvalue())

    def test_an_unknown_command_is_reported_without_a_traceback(self):
        cli = self._load_cli()
        errors = io.StringIO()
        with redirect_stderr(errors):
            result = cli.main(["bogus"])
        self.assertEqual(result, 2)
        self.assertIn("unknown command", errors.getvalue())
        self.assertIn("dex", errors.getvalue())


class HelpPageTests(unittest.TestCase):
    """The page ``pdex -h`` prints, and the sources it is derived from.

    The page is assembled from the command table and the registered handlers
    rather than written out by hand, so what these tests guard is that the
    derivation stays complete: a command that can be dispatched but is not
    described, or a handler with nothing to say about itself, leaves the page
    lying about what the tool does.
    """

    def test_every_dispatchable_command_is_described(self):
        self.assertEqual(set(cli.COMMANDS), set(overview.COMMAND_SUMMARIES))

    @staticmethod
    def _unwrapped():
        """Returns the page with its column wrapping flattened back out.

        The second column is wrapped to the page width, so a summary need not
        appear as one contiguous run of text. Collapsing the whitespace keeps
        these checks about whether the text is present at all.
        """
        return " ".join(overview.render().split())

    def test_every_command_appears_on_the_page(self):
        page = self._unwrapped()
        for command in cli.COMMANDS:
            self.assertIn(command, page, command)
            self.assertIn(overview.COMMAND_SUMMARIES[command], page, command)

    def test_every_handler_says_what_a_run_does_with_its_item(self):
        for page_type, handler in handlers.HANDLERS.items():
            self.assertTrue(handler.summary.strip(), page_type)

    def test_every_handled_item_type_appears_on_the_page(self):
        page = self._unwrapped()
        for page_type, handler in handlers.HANDLERS.items():
            self.assertIn(page_type.lower(), page, page_type)
            self.assertIn(handler.summary, page, page_type)

    def test_the_page_names_the_ledger_limit_it_documents(self):
        # Read from the module that enforces it, so changing the cap in one
        # place cannot leave the help page quoting the old number.
        self.assertIn(f"{course_manager.MAX_LEDGER_CONTENT_CHARS:,}", overview.render())

    def test_the_page_keeps_no_machine_specific_paths(self):
        # The state directory is per-machine; naming the variable is portable,
        # printing the expanded path is not.
        page = overview.render()
        self.assertIn("PHANTADEX_STATE_DIR", page)
        self.assertNotIn(str(Path.home()), page)

    def test_the_page_prints_only_ascii(self):
        # -h is printed before logging widens stdout to UTF-8, and a redirected
        # stream on Windows inherits the ANSI code page. A single box-drawing
        # rule would raise UnicodeEncodeError on the first line of help.
        self.assertTrue(overview.render().isascii())

    @staticmethod
    def _in_colour():
        """Renders the page with the palette forced on.

        ``overview`` reads ``logs.paint`` on each call rather than binding it at
        import, so replacing it here is enough to colour the page.
        """
        with mock.patch.object(logs, "paint", logs._Palette(True)):
            return overview.render()

    def test_no_line_runs_past_eighty_columns(self):
        for line in overview.render().splitlines():
            self.assertLessEqual(overview.visible_width(line), overview.MAX_PAGE_WIDTH, line)

    def test_colour_costs_the_page_no_columns(self):
        # An escape sequence occupies no columns but has a length, so measuring
        # the raw string would size every rule to the codes above it and wrap
        # the page at a width no terminal is showing.
        page = self._in_colour()
        self.assertIn("\x1b[", page)
        for line in page.splitlines():
            self.assertLessEqual(overview.visible_width(line), overview.MAX_PAGE_WIDTH, line)

    def test_colour_changes_nothing_but_the_colour(self):
        # The coloured and plain pages must be the same page: same text, same
        # columns, same alignment.
        stripped = re.sub(r"\x1b\[[0-9;]*m", "", self._in_colour())
        self.assertEqual(stripped, overview.render())

    def test_the_page_is_plain_when_the_stream_is_not_a_terminal(self):
        # Redirected help is read by pagers, files and agents; escape codes
        # would be printed literally into all three.
        with mock.patch.object(logs, "paint", logs._Palette(False)):
            self.assertNotIn("\x1b[", overview.render())

    def test_every_rule_spans_the_section_under_it(self):
        # A rule shorter than the lines beneath it reads as a broken table.
        for page in (overview.render(), self._in_colour()):
            lines = page.splitlines()
            plain = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in lines]
            rules = [line for line in plain if set(line) in ({"-"}, {"="})]
            self.assertTrue(rules)
            widest = max(overview.visible_width(line) for line in lines)
            for rule in rules:
                self.assertEqual(len(rule), widest)

    def test_the_page_links_the_guide_to_clearing_graded_work(self):
        # The section says the run leaves graded work alone; the link is how a
        # reader gets from that sentence to what to do about it.
        page = self._unwrapped()
        self.assertIn(overview.USAGE_DOC_PATH, page)
        self.assertIn(
            f"{phantadex.REPOSITORY_URL}/blob/main/{overview.USAGE_DOC_PATH}",
            page,
        )

    def test_the_linked_guide_exists_in_the_repository(self):
        # The link is to a path in this repository, so a moved or unstaged file
        # makes the help page point at a 404.
        self.assertTrue((PROJECT_ROOT / overview.USAGE_DOC_PATH).is_file())

    def test_the_page_names_where_the_project_lives(self):
        self.assertIn(phantadex.REPOSITORY_URL, overview.render())

    def test_the_repository_link_matches_the_published_metadata(self):
        # Two places name the project's home; only one of them is what pip and
        # PyPI show. They are kept identical rather than left to drift.
        pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
        homepage = re.search(r'Homepage = "([^"]+)"', pyproject)
        self.assertIsNotNone(homepage)
        self.assertEqual(homepage.group(1), phantadex.REPOSITORY_URL)

    def test_usage_lines_follow_the_name_the_tool_was_called_by(self):
        for name in ("pdex", "phantadex"):
            with mock.patch.object(sys, "argv", [name]):
                self.assertIn(f"{name} [command] [options]", overview.render())


class VerbosityFlagTests(unittest.TestCase):
    """The -v/-q shorthands and their precedence against --log-level."""

    @staticmethod
    def _settings(argv):
        parser = config.build_parser("test")
        return config.settings_from_args(parser.parse_args(argv))

    def test_no_flag_stays_at_info(self):
        self.assertEqual(self._settings([]).log_level, "INFO")

    def test_verbose_selects_debug(self):
        self.assertEqual(self._settings(["-v"]).log_level, "DEBUG")
        self.assertEqual(self._settings(["--verbose"]).log_level, "DEBUG")

    def test_quiet_selects_warning(self):
        self.assertEqual(self._settings(["-q"]).log_level, "WARNING")

    def test_explicit_level_outranks_the_shorthands(self):
        self.assertEqual(self._settings(["-v", "--log-level", "ERROR"]).log_level, "ERROR")

    def test_quiet_outranks_verbose(self):
        self.assertEqual(self._settings(["-v", "-q"]).log_level, "WARNING")

    def test_every_entry_point_accepts_the_flag(self):
        for build in (
            lambda: config.build_parser("dex"),
            lambda: config.add_automation_args(config.build_parser("watch")),
        ):
            with self.subTest(build=build):
                self.assertEqual(
                    config.settings_from_args(build().parse_args(["-v"])).log_level, "DEBUG"
                )


class RunScopeFlagTests(unittest.TestCase):
    """``--modules N`` / ``--items N``: how much of a course one run covers."""

    @staticmethod
    def _settings(argv):
        parser = config.add_automation_args(config.build_parser("watch"))
        return config.settings_from_args(parser.parse_args(argv))

    def test_the_default_is_the_whole_course(self):
        settings = self._settings([])
        self.assertIsNone(settings.module_limit)
        self.assertIsNone(settings.item_limit)
        self.assertIn("whole course", settings.describe())

    def test_a_count_reaches_the_settings(self):
        self.assertEqual(self._settings(["--modules", "2"]).module_limit, 2)
        self.assertEqual(self._settings(["--items", "7"]).item_limit, 7)

    def test_the_active_limit_is_named_in_the_settings_line(self):
        self.assertIn("scope=2 modules", self._settings(["--modules", "2"]).describe())
        self.assertIn("scope=7 items", self._settings(["--items", "7"]).describe())

    def test_a_count_below_one_is_refused_at_the_command_line(self):
        # A run asked for nothing would attach, map the course and stop without
        # touching an item, which reads as a failure rather than as obedience.
        for argv in (["--items", "0"], ["--modules", "-1"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                with redirect_stderr(io.StringIO()):
                    self._settings(argv)

    def test_a_count_that_is_not_a_number_is_refused(self):
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            self._settings(["--items", "some"])

    def test_the_two_limits_cannot_be_combined(self):
        # They bound the same run in two ways; honouring the smaller one
        # silently is worse than saying they conflict.
        stderr = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stderr(stderr):
            self._settings(["--modules", "2", "--items", "3"])
        self.assertIn("not allowed with", stderr.getvalue())

    def test_settings_built_by_hand_refuse_the_same_pair(self):
        with self.assertRaises(ValueError):
            config.Settings(module_limit=2, item_limit=3)


class RunLogFlagTests(unittest.TestCase):
    @staticmethod
    def _settings(argv):
        parser = config.add_automation_args(config.build_parser("watch"))
        return config.settings_from_args(parser.parse_args(argv))

    def test_a_run_log_is_written_by_default(self):
        self.assertTrue(self._settings([]).run_log)

    def test_the_flag_turns_it_off(self):
        self.assertFalse(self._settings(["--no-run-log"]).run_log)

    def test_the_suite_never_writes_a_run_log_where_a_person_keeps_theirs(self):
        # The watch entry point opens a real run log, so an unredirected suite
        # dropped its own output into the user's state directory.
        self.assertTrue(logs.run_log_dir().startswith(os.environ["PHANTADEX_STATE_DIR"]))

    def test_repeated_runs_leave_one_run_log_handler(self):
        cli_module = importlib.import_module("phantadex.cli")
        logger = logging.getLogger(logs.LOGGER_NAME)
        self.addCleanup(logs.close_run_log)

        with mock.patch.object(cli_module.watch.runner, "run"), capture_console():
            cli_module.main(["watch"])
            cli_module.main(["watch"])

        installed = [h for h in logger.handlers if isinstance(h, logs.RunLogHandler)]
        self.assertEqual(len(installed), 1)


if __name__ == "__main__":
    unittest.main()


class DebugEndpointDefaultTests(unittest.TestCase):
    """The endpoint has to be configurable once, not on every command line.

    Port 9222 is not always free -- on Windows it is regularly held by an OEM
    helper or a WebView2 host -- and moving off it used to mean passing
    ``--cdp-url`` to every command in every session.
    """

    def test_the_built_in_default_stands_when_nothing_is_set(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(config.CDP_URL_ENV, None)
            self.assertEqual(config.default_cdp_url(), config.CDP_URL)

    def test_the_environment_moves_every_command_at_once(self):
        moved = "http://localhost:9333"
        with mock.patch.dict(os.environ, {config.CDP_URL_ENV: moved}):
            self.assertEqual(config.default_cdp_url(), moved)
            self.assertEqual(config.Settings().cdp_url, moved)
            parser = config.build_parser("", "watch")
            self.assertEqual(config.settings_from_args(parser.parse_args([])).cdp_url, moved)

    def test_an_explicit_flag_still_wins_over_the_environment(self):
        with mock.patch.dict(os.environ, {config.CDP_URL_ENV: "http://localhost:9333"}):
            parser = config.build_parser("", "watch")
            settings = config.settings_from_args(parser.parse_args(["--cdp-url", "http://x:1"]))
        self.assertEqual(settings.cdp_url, "http://x:1")
