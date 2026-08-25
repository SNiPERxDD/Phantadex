"""Selector schema resolution tests."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phantadex import schema
from tests.fakes import capture_console

MISSING = "/nonexistent/config.yaml"


class VerifiedSelectorTests(unittest.TestCase):
    def setUp(self):
        schema.reload_verified_selectors()
        self.addCleanup(schema.reload_verified_selectors)
        # Isolate every case from whatever the developer's own machine holds.
        packaged = mock.patch.object(schema, "PACKAGED_CONFIG_PATH", MISSING)
        packaged.start()
        self.addCleanup(packaged.stop)
        state = mock.patch.object(schema, "state_config_path", lambda: MISSING)
        state.start()
        self.addCleanup(state.stop)

    def _yaml_file(self, body):
        handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        handle.write(body)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def _with_state(self, body):
        path = self._yaml_file(body)
        return mock.patch.object(schema, "state_config_path", lambda: path)

    def _with_packaged(self, body):
        return mock.patch.object(schema, "PACKAGED_CONFIG_PATH", self._yaml_file(body))

    def test_checked_in_selector_config_is_packaged_with_the_module(self):
        config_path = Path(schema.__file__).with_name("config.yaml")
        self.assertTrue(config_path.is_file())

    def test_learned_state_lives_outside_the_installed_package(self):
        # Package directories can be read-only in a wheel install, and an
        # upgrade would discard repairs written into them.
        package_dir = Path(schema.__file__).parent.resolve()
        state_path = Path(schema.state_dir()).resolve()
        self.assertNotEqual(state_path, package_dir)
        self.assertNotIn(package_dir, state_path.parents)

    def test_state_dir_honours_the_environment_override(self):
        # A POSIX literal was asserted here verbatim, which fails on Windows:
        # ``os.path.abspath`` resolves a rooted path against the current drive,
        # so "/tmp/pdex-state" comes back as "D:\tmp\pdex-state".
        override = os.path.join(tempfile.gettempdir(), "pdex-state")
        with mock.patch.dict(os.environ, {schema.STATE_DIR_ENV: override}):
            self.assertEqual(schema.state_dir(), os.path.abspath(override))

    def test_a_relative_override_is_made_absolute(self):
        with mock.patch.dict(os.environ, {schema.STATE_DIR_ENV: "pdex-state"}):
            resolved = schema.state_dir()
        self.assertTrue(os.path.isabs(resolved))
        self.assertEqual(os.path.basename(resolved), "pdex-state")

    def test_verified_selector_is_tried_before_the_builtin_defaults(self):
        # Regression: discovery wrote its findings and nothing read them back, so
        # a selector it repaired after a markup change never reached the runner.
        defaults = {"content": {"reading_body": {"selectors": ["div.rc-CML"]}}}
        with self._with_state("content:\n  reading_body: div.brand-new\n"):
            with mock.patch.object(schema, "_schema", return_value=defaults):
                self.assertEqual(
                    schema.selectors_for("content", "reading_body"),
                    ["div.brand-new", "div.rc-CML"],
                )

    def test_learned_state_overrides_the_packaged_default(self):
        defaults = {"content": {"reading_body": {"selectors": ["div.rc-CML"]}}}
        with self._with_packaged("content:\n  reading_body: div.shipped\n"):
            with self._with_state("content:\n  reading_body: div.learned\n"):
                with mock.patch.object(schema, "_schema", return_value=defaults):
                    self.assertEqual(
                        schema.selectors_for("content", "reading_body"),
                        ["div.learned", "div.rc-CML"],
                    )

    def test_packaged_categories_survive_an_unrelated_state_entry(self):
        defaults = {}
        with self._with_packaged("content:\n  reading_body: div.shipped\n"):
            with self._with_state("navigation:\n  next_item: button.learned\n"):
                with mock.patch.object(schema, "_schema", return_value=defaults):
                    self.assertEqual(
                        schema.selectors_for("content", "reading_body"), ["div.shipped"]
                    )
                    self.assertEqual(
                        schema.selectors_for("navigation", "next_item"), ["button.learned"]
                    )

    def test_defaults_survive_a_missing_config(self):
        defaults = {"content": {"reading_body": {"selectors": ["div.rc-CML"]}}}
        with mock.patch.object(schema, "_schema", return_value=defaults):
            self.assertEqual(schema.selectors_for("content", "reading_body"), ["div.rc-CML"])

    def test_malformed_config_degrades_to_defaults(self):
        defaults = {"content": {"reading_body": {"selectors": ["div.rc-CML"]}}}
        with self._with_state("content: [not, a, mapping]\n"):
            with mock.patch.object(schema, "_schema", return_value=defaults):
                self.assertEqual(schema.selectors_for("content", "reading_body"), ["div.rc-CML"])

    def test_a_verified_selector_is_not_duplicated(self):
        defaults = {"content": {"reading_body": {"selectors": ["div.rc-CML", "div.other"]}}}
        with self._with_state("content:\n  reading_body: div.rc-CML\n"):
            with mock.patch.object(schema, "_schema", return_value=defaults):
                self.assertEqual(
                    schema.selectors_for("content", "reading_body"), ["div.rc-CML", "div.other"]
                )

    def test_a_list_of_verified_selectors_is_accepted(self):
        defaults = {"nav": {"next": {"selectors": ["button.fallback"]}}}
        with self._with_state("nav:\n  next:\n    - button.a\n    - button.b\n"):
            with mock.patch.object(schema, "_schema", return_value=defaults):
                self.assertEqual(
                    schema.selectors_for("nav", "next"),
                    ["button.a", "button.b", "button.fallback"],
                )

    def test_unknown_element_returns_empty(self):
        with mock.patch.object(schema, "_schema", return_value={}):
            self.assertEqual(schema.selectors_for("nope", "nope"), [])


class FirstVisibleTests(unittest.TestCase):
    """Visibility filtering is shared by every caller, so it lives here."""

    class _Matches:
        def __init__(self, visibility):
            self._visibility = visibility
            self.checked = []

        def count(self):
            return len(self._visibility)

        def nth(self, index):
            self.checked.append(index)
            return _Candidate(self._visibility[index])

    def _page(self, matches):
        page = mock.Mock()
        page.locator.return_value = matches
        return page

    def test_a_hidden_first_match_does_not_hide_a_visible_later_one(self):
        matches = self._Matches([False, True])
        with mock.patch.object(schema, "selectors_for", return_value=["button.next"]):
            found = schema.first_visible(self._page(matches), "navigation", "next_item")
        self.assertIsNotNone(found)
        self.assertEqual(matches.checked, [0, 1])

    def test_all_hidden_matches_return_none(self):
        matches = self._Matches([False, False])
        with mock.patch.object(schema, "selectors_for", return_value=["button.next"]):
            self.assertIsNone(schema.first_visible(self._page(matches), "nav", "next"))

    def test_the_scan_is_bounded(self):
        matches = self._Matches([False] * (schema.MAX_VISIBILITY_SCAN + 5))
        with mock.patch.object(schema, "selectors_for", return_value=["button.next"]):
            schema.first_visible(self._page(matches), "nav", "next")
        self.assertEqual(len(matches.checked), schema.MAX_VISIBILITY_SCAN)


class _Candidate:
    """A single located node with a fixed visibility."""

    def __init__(self, visible):
        self._visible = visible

    def is_visible(self):
        return self._visible


class StaleSelectorHintTests(unittest.TestCase):
    """Saying the markup moved, once, and pointing at the pass that repairs it."""

    def setUp(self):
        schema.reload_verified_selectors()

    def tearDown(self):
        schema.reload_verified_selectors()

    def test_the_hint_names_the_element_and_the_command(self):
        with capture_console() as output:
            schema.report_stale("the video transcript")
        printed = output.getvalue()
        self.assertIn("the video transcript", printed)
        self.assertIn("pdex discover", printed)

    def test_it_is_said_once_however_many_items_hit_it(self):
        with capture_console() as output:
            for _ in range(40):
                schema.report_stale("the reading body")
        self.assertEqual(output.getvalue().count("pdex discover"), 1)

    def test_a_repair_lets_it_be_said_again(self):
        schema.report_stale("the reading body")
        schema.reload_verified_selectors()
        with capture_console() as output:
            schema.report_stale("the reading body")
        self.assertIn("pdex discover", output.getvalue())


if __name__ == "__main__":
    unittest.main()
