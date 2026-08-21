"""Debug-browser launcher contracts."""

import os
import unittest
from pathlib import Path
from unittest import mock

from phantadex import chrome, schema


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


class LaunchTests(unittest.TestCase):
    def test_a_listening_port_is_reported_without_starting_chrome(self):
        with mock.patch.object(chrome, "port_in_use", return_value=True):
            with mock.patch.object(chrome.subprocess, "Popen") as popen:
                self.assertEqual(chrome.main([]), 0)
        popen.assert_not_called()

    def test_a_missing_browser_fails_without_creating_a_profile(self):
        with mock.patch.object(chrome, "port_in_use", return_value=False):
            with mock.patch.object(chrome, "chrome_binary", return_value=None):
                with mock.patch.object(Path, "mkdir") as mkdir:
                    self.assertEqual(chrome.main([]), 1)
        mkdir.assert_not_called()

    def test_the_port_comes_from_the_cdp_url(self):
        self.assertEqual(chrome._debug_port("http://localhost:9333"), 9333)
        self.assertEqual(chrome._debug_port("http://127.0.0.1:9222/json"), 9222)
        self.assertEqual(chrome._debug_port("http://localhost"), 9222)

    def test_chrome_is_started_on_the_requested_port(self):
        with mock.patch.object(chrome, "port_in_use", side_effect=[False, True]):
            with mock.patch.object(chrome, "chrome_binary", return_value=Path("/bin/chrome")):
                with mock.patch.object(Path, "mkdir"):
                    with mock.patch.object(chrome.time, "sleep"):
                        with mock.patch.object(chrome.subprocess, "Popen") as popen:
                            self.assertEqual(chrome.main(["--cdp-url", "http://localhost:9444"]), 0)
        command = popen.call_args[0][0]
        self.assertEqual(command[0], "/bin/chrome")
        self.assertIn("--remote-debugging-port=9444", command)


if __name__ == "__main__":
    unittest.main()
