"""Package CLI contracts for Dex, Watch, Archive, Skip, and module execution."""

import importlib
import importlib.util
import io
import subprocess
import sys
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from phantadex import config
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

    def find_course_page(self):
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
            self.seek_target = args[0]
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

    def test_watch_preserves_random_seek_and_full_completion_defaults(self):
        cli = self._load_cli()
        captured = []

        with mock.patch.object(cli.watch.runner, "run", side_effect=captured.append):
            result = cli.main(["watch"])

        self.assertEqual(result, 0)
        self.assertEqual(captured[0].video_skip_range, "97.5-98.5%")
        self.assertEqual(captured[0].video_completion_threshold, 100.0)
        self.assertEqual(captured[0].reading_default_minutes, (7, 12))

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

        with mock.patch.object(cli.watch.runner, "run") as run, redirect_stderr(
            io.StringIO()
        ), self.assertRaises(SystemExit) as raised:
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
        for command in ("dex", "skip", "watch", "archive", "discover"):
            self.assertIn(command, result.stdout)

    def test_discover_runs_the_selector_pass_against_the_given_endpoint(self):
        cli = self._load_cli()
        with mock.patch.object(cli.discovery, "start_dynamic_observation") as observe:
            result = cli.main(["discover", "--cdp-url", "http://localhost:9333"])

        self.assertEqual(result, 0)
        observe.assert_called_once_with("http://localhost:9333")

    def test_discover_honours_the_verbosity_flag(self):
        cli = self._load_cli()
        with mock.patch.object(cli.discovery, "start_dynamic_observation"), mock.patch.object(
            cli.logs, "setup"
        ) as setup:
            cli.main(["discover", "-v"])

        setup.assert_called_once_with("DEBUG")

    def test_full_alias_names_itself_in_help(self):
        cli = self._load_cli()
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["phantadex"]), redirect_stdout(output):
            result = cli.main(["--help"])
        self.assertEqual(result, 0)
        self.assertIn("usage: phantadex", output.getvalue())


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


if __name__ == "__main__":
    unittest.main()
