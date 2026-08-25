"""Debug-browser launcher contracts."""

import itertools
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from phantadex import chrome, schema


def _endpoint(payload=None, body=None):
    """Returns a context-manager stand-in for a urlopen response."""
    raw = body if body is not None else json.dumps(payload or {}).encode()
    response = mock.MagicMock()
    response.status = 200
    response.read.return_value = raw
    response.__enter__.return_value = response
    return response


class ProfileDirectoryTests(unittest.TestCase):
    def test_the_profile_lives_beside_the_other_state_not_in_the_package(self):
        # A wheel install makes the package directory read-only, and an upgrade
        # would discard the signed-in profile written into it.
        package_dir = Path(chrome.__file__).parent.resolve()
        profile = chrome.profile_dir().resolve()
        self.assertNotIn(package_dir, profile.parents)
        self.assertEqual(profile.parent, Path(schema.state_dir()).resolve())

    def test_the_profile_can_be_overridden(self):
        with mock.patch.dict(os.environ, {chrome.PROFILE_DIR_ENV: "custom-profile"}):
            resolved = chrome.profile_dir()
        self.assertTrue(resolved.is_absolute())
        self.assertEqual(resolved.name, "custom-profile")


class BinaryLookupTests(unittest.TestCase):
    def test_every_supported_platform_offers_candidates(self):
        for platform, expected in (("darwin", 1), ("win32", 1)):
            with mock.patch.object(chrome.sys, "platform", platform):
                with mock.patch.dict(os.environ, {"PROGRAMFILES": "C:\\Program Files"}):
                    self.assertGreaterEqual(len(chrome.chrome_candidates()), expected)

    def test_linux_is_searched_by_command_name(self):
        with mock.patch.object(chrome.sys, "platform", "linux"):
            with mock.patch.object(
                chrome.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"
            ):
                candidates = [path.name for path in chrome.chrome_candidates()]
        self.assertIn("google-chrome", candidates)
        self.assertIn("chromium", candidates)

    def test_the_configured_binary_wins(self):
        with mock.patch.dict(os.environ, {chrome.CHROME_BINARY_ENV: "/opt/chrome"}):
            with mock.patch.object(Path, "is_file", return_value=True):
                self.assertEqual(chrome.chrome_binary(), Path("/opt/chrome"))


class EndpointProbeTests(unittest.TestCase):
    def test_a_socket_that_does_not_speak_devtools_is_not_a_debug_browser(self):
        # The whole Windows failure in one line: an OEM helper holding 9222
        # answers a TCP connect, and calling that "Chrome is already running"
        # left the launcher printing success without starting a browser.
        with mock.patch.object(chrome.urllib.request, "urlopen", side_effect=OSError("refused")):
            self.assertFalse(chrome.debug_endpoint_ready(9222))

    def test_an_endpoint_without_a_debugger_socket_is_rejected(self):
        with mock.patch.object(
            chrome.urllib.request, "urlopen", return_value=_endpoint({"Browser": "Chrome/1"})
        ):
            self.assertFalse(chrome.debug_endpoint_ready(9222))

    def test_a_devtools_endpoint_is_accepted(self):
        payload = {"Browser": "Chrome/1", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools"}
        with mock.patch.object(chrome.urllib.request, "urlopen", return_value=_endpoint(payload)):
            self.assertTrue(chrome.debug_endpoint_ready(9222))

    def test_a_non_json_answer_is_rejected_rather_than_raised(self):
        with mock.patch.object(
            chrome.urllib.request, "urlopen", return_value=_endpoint(body=b"<html>hello</html>")
        ):
            self.assertFalse(chrome.debug_endpoint_ready(9222))

    def test_a_holder_that_does_not_speak_http_is_rejected_rather_than_raised(self):
        """The listener this probe exists to reject must not escape it.

        ``urllib`` wraps only the errors it raises itself; a socket that accepts
        the connection and then answers with something that is not HTTP raises
        past its error type, out of the launcher and into the top-level handler.
        """
        with mock.patch.object(
            chrome.urllib.request,
            "urlopen",
            side_effect=chrome.http.client.BadStatusLine("\x16\x03\x01"),
        ):
            self.assertFalse(chrome.debug_endpoint_ready(9222))


class SpawnOptionTests(unittest.TestCase):
    def test_a_posix_launch_detaches_by_starting_its_own_session(self):
        with mock.patch.object(chrome.sys, "platform", "darwin"):
            self.assertEqual(chrome._spawn_options(), {"start_new_session": True})

    def test_a_windows_launch_detaches_by_creation_flag(self):
        # Windows ignores start_new_session outright, so Chrome died with the
        # terminal that started it. Only a creation flag detaches it there.
        with mock.patch.object(chrome.sys, "platform", "win32"):
            options = chrome._spawn_options()
        self.assertNotIn("start_new_session", options)
        self.assertTrue(options["creationflags"] & chrome.DETACHED_PROCESS)
        self.assertTrue(options["creationflags"] & chrome.CREATE_BREAKAWAY_FROM_JOB)

    def test_a_refused_breakaway_still_starts_chrome(self):
        # A parent already inside a job object that forbids breakaway makes
        # Windows refuse the spawn, not the flag. An attached Chrome is worth
        # more than no Chrome.
        with mock.patch.object(chrome.sys, "platform", "win32"):
            with mock.patch.object(
                chrome.subprocess, "Popen", side_effect=[OSError("access denied"), "started"]
            ) as popen:
                started = chrome._launch(Path("/bin/chrome"), 9222, Path("/profile"))
        self.assertEqual(started, "started")
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(popen.call_args.kwargs["creationflags"], chrome.DETACHED_PROCESS)


class LaunchTests(unittest.TestCase):
    def test_a_live_debug_endpoint_is_reported_without_starting_chrome(self):
        with mock.patch.object(chrome, "debug_endpoint_ready", return_value=True):
            with mock.patch.object(chrome, "port_in_use", return_value=True):
                with mock.patch.object(chrome.subprocess, "Popen") as popen:
                    self.assertEqual(chrome.main([]), 0)
        popen.assert_not_called()

    def test_a_busy_debug_browser_is_given_time_to_answer_before_being_refused(self):
        """One missed probe must not turn the user's own browser into a stranger.

        A signed-in Chrome under load can be slower than a single probe timeout,
        and refusing it here sends the user to a second port with their session
        left on the first.
        """
        with mock.patch.object(chrome, "debug_endpoint_ready", side_effect=[False, False, True]):
            with mock.patch.object(chrome, "port_in_use", return_value=True):
                with mock.patch.object(chrome.time, "sleep"):
                    with mock.patch.object(chrome.subprocess, "Popen") as popen:
                        self.assertEqual(chrome.main([]), 0)
        popen.assert_not_called()

    def test_a_port_held_by_something_else_is_refused_rather_than_claimed(self):
        clock = mock.MagicMock()
        clock.monotonic.side_effect = itertools.count(0.0, 2.0)
        with mock.patch.object(chrome, "debug_endpoint_ready", return_value=False):
            with mock.patch.object(chrome, "port_in_use", return_value=True):
                with mock.patch.object(chrome, "time", clock):
                    with mock.patch.object(chrome.subprocess, "Popen") as popen:
                        self.assertEqual(chrome.main([]), 1)
        popen.assert_not_called()

    def test_a_launch_whose_endpoint_never_answers_fails(self):
        """The command exists to leave an attachable browser behind.

        Reporting success here let a caller chain the next command onto a port
        that never bound, which then read the attach failure as its own.
        """
        clock = mock.MagicMock()
        clock.monotonic.side_effect = itertools.count(0.0, 5.0)
        with mock.patch.object(chrome, "debug_endpoint_ready", return_value=False):
            with mock.patch.object(chrome, "port_in_use", return_value=False):
                with mock.patch.object(chrome, "chrome_binary", return_value=Path("/bin/chrome")):
                    with mock.patch.object(Path, "mkdir"):
                        with mock.patch.object(chrome, "time", clock):
                            with mock.patch.object(chrome.subprocess, "Popen"):
                                self.assertEqual(chrome.main([]), 1)

    def test_a_missing_browser_fails_without_creating_a_profile(self):
        with mock.patch.object(chrome, "debug_endpoint_ready", return_value=False):
            with mock.patch.object(chrome, "port_in_use", return_value=False):
                with mock.patch.object(chrome, "chrome_binary", return_value=None):
                    with mock.patch.object(Path, "mkdir") as mkdir:
                        self.assertEqual(chrome.main([]), 1)
        mkdir.assert_not_called()

    def test_the_port_comes_from_the_cdp_url(self):
        self.assertEqual(chrome.debug_port("http://localhost:9333"), 9333)
        self.assertEqual(chrome.debug_port("http://127.0.0.1:9222/json"), 9222)
        self.assertEqual(chrome.debug_port("http://localhost"), 9222)

    def test_chrome_is_started_on_the_requested_port_without_first_run_prompts(self):
        # Compared against the Path, not a POSIX literal: Windows renders
        # Path("/bin/chrome") as "\bin\chrome".
        executable = Path("/bin/chrome")
        with mock.patch.object(chrome, "debug_endpoint_ready", side_effect=[False, True]):
            with (
                mock.patch.object(chrome.time, "sleep"),
                mock.patch.object(chrome, "port_in_use", return_value=False),
            ):
                with mock.patch.object(chrome, "chrome_binary", return_value=executable):
                    with mock.patch.object(Path, "mkdir"):
                        with mock.patch.object(chrome.subprocess, "Popen") as popen:
                            self.assertEqual(chrome.main(["--cdp-url", "http://localhost:9444"]), 0)
        command = popen.call_args[0][0]
        self.assertEqual(command[0], str(executable))
        self.assertIn("--remote-debugging-port=9444", command)
        # A dedicated profile is a fresh one the first time, and Chrome greets
        # a fresh profile with two dialogs sitting in front of the course.
        self.assertIn("--no-first-run", command)
        self.assertIn("--no-default-browser-check", command)

    def test_a_slow_start_is_waited_out_rather_than_warned_about(self):
        with mock.patch.object(chrome, "debug_endpoint_ready", side_effect=[False, False, True]):
            with mock.patch.object(chrome, "port_in_use", return_value=False):
                with mock.patch.object(chrome, "chrome_binary", return_value=Path("/bin/chrome")):
                    with mock.patch.object(Path, "mkdir"):
                        with mock.patch.object(chrome.subprocess, "Popen"):
                            with mock.patch.object(chrome.time, "sleep") as slept:
                                self.assertEqual(chrome.main([]), 0)
        slept.assert_called()


if __name__ == "__main__":
    unittest.main()
