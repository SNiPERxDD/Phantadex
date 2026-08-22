"""Reading-session scrolling and navigation boundaries."""

import unittest
from unittest import mock

from phantadex import interaction, urls
from tests.fakes import FakeLocator, FakePage

CONTENT_SELECTOR = "div.rc-CML, main, div[role='main']"


class _ElementHandle:
    def __init__(self, element):
        self.element = element

    def as_element(self):
        return self.element


class _Probe:
    def __init__(self, element):
        self.element = element

    def evaluate_handle(self, _script):
        return _ElementHandle(self.element)


class _ViewportPage(FakePage):
    def evaluate(self, script, *_args):
        # Dispatch on the script: returning the viewport pair for every
        # evaluation made the mute pass look as though it had silenced two
        # elements, and its log line then consumed the patched clock.
        if "querySelectorAll('audio, video')" in script:
            return 0
        return [1200, 800]


class ClickContractTests(unittest.TestCase):
    """The click point, the actionability default, and the wait bound."""

    def setUp(self):
        patcher = mock.patch.object(interaction.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _click(self, **kwargs):
        page = FakePage()
        button = FakeLocator(count=1, events=page.pointer_events)
        self.assertTrue(interaction.click(page, button, **kwargs))
        return page, button

    def test_the_jitter_is_passed_as_a_click_position(self):
        # Regression: the offset was only used for the cursor move, and the
        # click itself still landed on the element's exact centre.
        page, button = self._click()
        position = button.click_kwargs["position"]
        box = button.bounding_box()
        self.assertTrue(0 < position["x"] < box["width"])
        self.assertTrue(0 < position["y"] < box["height"])
        self.assertEqual(
            page.mouse.moves[-1],
            (box["x"] + position["x"], box["y"] + position["y"]),
        )

    def test_an_explicit_position_pins_the_click_point(self):
        # A seek through the player's progress bar must land at the chosen
        # fraction of the control, not at its centre.
        page = FakePage()
        bar = FakeLocator(count=1, events=page.pointer_events)
        self.assertTrue(interaction.click(page, bar, position={"x": 90, "y": 10}))
        clicked = bar.click_kwargs["position"]
        box = bar.bounding_box()
        self.assertGreater(clicked["x"], 80)
        self.assertLess(clicked["x"], 100)
        self.assertLess(clicked["y"], 15)
        # The cursor approach ends exactly where the click will land.
        self.assertEqual(
            page.mouse.moves[-1],
            (box["x"] + clicked["x"], box["y"] + clicked["y"]),
        )

    def test_the_click_point_is_not_always_the_centre(self):
        seen = set()
        for _ in range(20):
            _page, button = self._click()
            seen.add((button.click_kwargs["position"]["x"], button.click_kwargs["position"]["y"]))
        self.assertGreater(len(seen), 1)

    def test_no_hover_precedes_the_click(self):
        # Playwright's click re-resolves the element and moves to the position
        # itself; an explicit hover only repeated identical mouse-move events
        # at one coordinate before the press.
        page, button = self._click()
        self.assertEqual(button.hover_kwargs, {})

    def test_actionability_checks_are_on_by_default(self):
        # `force` skips the "element receives pointer events" check, so a click
        # swallowed by an overlay would be reported as a success.
        _page, button = self._click()
        self.assertFalse(button.click_kwargs["force"])

    def test_a_caller_can_still_force_a_click(self):
        _page, button = self._click(force=True)
        self.assertTrue(button.click_kwargs["force"])

    def test_the_wait_is_bounded(self):
        # Unforced clicks wait for actionability; Playwright's own default is
        # 30s, long enough for one stuck control to stall the run.
        _page, button = self._click()
        self.assertEqual(button.click_kwargs["timeout"], interaction.CLICK_TIMEOUT_MS)

    def test_the_jitter_stays_inside_a_small_control(self):
        page = FakePage()
        button = FakeLocator(count=1, events=page.pointer_events)
        button.bounding_box = lambda: {"x": 10, "y": 10, "width": 4, "height": 4}
        self.assertTrue(interaction.click(page, button))
        position = button.click_kwargs["position"]
        self.assertTrue(0 < position["x"] < 4)
        self.assertTrue(0 < position["y"] < 4)


class ActionLoggingTests(unittest.TestCase):
    """Every click and scroll must be visible at DEBUG, and silent above it."""

    def setUp(self):
        patcher = mock.patch.object(interaction.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_click_names_the_control_it_hit(self):
        page = FakePage()
        button = FakeLocator(count=1, text="Continue")
        with self.assertLogs("phantadex.interaction", level="DEBUG") as captured:
            self.assertTrue(interaction.click(page, button))
        self.assertTrue(any("clicked Continue" in line for line in captured.output))

    def test_an_unlabelled_control_still_reports_the_click(self):
        page = FakePage()
        button = FakeLocator(count=1, attributes={"aria-label": "Skip video"})
        with self.assertLogs("phantadex.interaction", level="DEBUG") as captured:
            interaction.click(page, button)
        self.assertTrue(any("Skip video" in line for line in captured.output))

    def test_a_forced_click_says_so(self):
        page = FakePage()
        button = FakeLocator(count=1, text="Continue")
        with self.assertLogs("phantadex.interaction", level="DEBUG") as captured:
            interaction.click(page, button, force=True)
        self.assertTrue(any("(forced)" in line for line in captured.output))

    def test_the_label_is_not_read_when_debug_is_off(self):
        page = FakePage()
        button = FakeLocator(count=1, text="Continue")
        with mock.patch.object(interaction, "describe") as describe:
            with mock.patch.object(interaction.log, "isEnabledFor", return_value=False):
                interaction.click(page, button)
        describe.assert_not_called()

    def test_describe_survives_a_detached_element(self):
        class _Gone(FakeLocator):
            def inner_text(self):
                raise RuntimeError("element is not attached")

        self.assertEqual(interaction.describe(_Gone(count=1)), "unreadable element")

    def test_a_scroll_reports_the_distance_travelled(self):
        scroller = mock.Mock()
        scroller.evaluate.side_effect = [
            {"top": 0, "max": 900, "x": 400, "y": 300},
            {"top": 200, "max": 900, "x": 400, "y": 300},
        ]
        page = _ViewportPage()
        with self.assertLogs("phantadex.interaction", level="DEBUG") as captured:
            interaction.scroll_by(page, scroller, 200, 400, 300)
        self.assertTrue(any("scrolled +200px: 0 -> 200" in line for line in captured.output))


class ScrollerSelectionTests(unittest.TestCase):
    def test_scroller_is_resolved_from_the_reading_not_the_page(self):
        sidebar = object()
        reading_scroller = object()
        page = _Probe(sidebar)
        content = _Probe(reading_scroller)

        selected = interaction.find_scroller(page, content)
        self.assertIs(selected, reading_scroller)


class ScrollDirectionTests(unittest.TestCase):
    def test_reading_scroll_never_moves_backward(self):
        with (
            mock.patch.object(interaction.random, "randint", return_value=200),
            mock.patch.object(interaction.random, "random", return_value=0),
        ):
            middle = interaction._next_delta({"top": 300, "max": 1000})
            bottom = interaction._next_delta({"top": 990, "max": 1000})

        self.assertGreater(middle, 0)
        self.assertEqual(bottom, 0)


class ReadingSessionTests(unittest.TestCase):
    def _page(self):
        return _ViewportPage(
            url="https://www.coursera.org/learn/demo/supplement/aaa/reading",
            title="Short reading | Coursera",
            locators={CONTENT_SELECTOR: FakeLocator(count=1, text="Short reading")},
        )

    def test_short_reading_without_a_scroller_does_not_emit_wheel_events(self):
        page = self._page()
        with (
            mock.patch.object(interaction, "find_scroller", return_value=None),
            mock.patch.object(interaction, "_session_geometry", return_value=(60, 500, 300)),
            mock.patch.object(interaction.modals, "dismiss_all"),
            mock.patch.object(interaction.time, "time", side_effect=[0, 1, 61, 62]),
            mock.patch.object(interaction.time, "sleep"),
            mock.patch.object(interaction.logs, "warn"),
            mock.patch.object(interaction.logs, "bar"),
            mock.patch.object(interaction.logs, "bar_done"),
            mock.patch.object(interaction.logs, "ok"),
        ):
            result = interaction.reading_session(page, 1)

        self.assertEqual(result, "COMPLETED")
        self.assertEqual(page.pointer_events, [])

    def test_navigation_ends_the_old_reading_session(self):
        page = self._page()
        metrics = {"top": 100, "max": 1000, "x": 500, "y": 300}

        def navigate_during_scroll(*_args):
            page.url = "https://www.coursera.org/learn/demo/lecture/bbb/video-title"
            return metrics

        with (
            mock.patch.object(interaction, "find_scroller", return_value=object()),
            mock.patch.object(interaction, "_session_geometry", return_value=(60, 500, 300)),
            mock.patch.object(interaction, "scroll_metrics", return_value=metrics),
            mock.patch.object(interaction, "scroll_by", side_effect=navigate_during_scroll),
            mock.patch.object(interaction.modals, "dismiss_all"),
            mock.patch.object(interaction.time, "time", side_effect=[0, 1, 61, 62]),
            mock.patch.object(interaction.time, "sleep"),
            mock.patch.object(interaction.logs, "bar"),
            mock.patch.object(interaction.logs, "bar_done"),
            mock.patch.object(interaction.logs, "ok"),
        ):
            result = interaction.reading_session(page, 1)

        self.assertEqual(result, "NAVIGATED")


if __name__ == "__main__":
    unittest.main()


class SilenceMediaTests(unittest.TestCase):
    """Covers the mute applied to autoplaying readings and narration players."""

    def test_audible_elements_are_reported(self):
        page = FakePage(evaluate_result=2)
        self.assertEqual(interaction.silence_media(page), 2)

    def test_a_silent_page_reports_nothing(self):
        page = FakePage(evaluate_result=0)
        self.assertEqual(interaction.silence_media(page), 0)

    def test_an_evaluation_failure_is_not_fatal(self):
        page = FakePage(evaluate_error=RuntimeError("execution context destroyed"))
        self.assertEqual(interaction.silence_media(page), 0)

    def test_the_script_targets_audio_as_well_as_video(self):
        self.assertIn("audio", interaction._SILENCE_JS)
        self.assertIn("video", interaction._SILENCE_JS)
        self.assertIn("volume = 0", interaction._SILENCE_JS)


class MediaGuardTests(unittest.TestCase):
    def test_the_guard_only_applies_to_the_platform(self):
        # Exact host match only: a bare suffix check would also arm inside
        # lookalike hosts such as "notcoursera.org".
        self.assertIn("location.hostname", interaction._MEDIA_GUARD_JS)
        self.assertIn(f"host !== '{urls.PLATFORM_HOST}'", interaction._MEDIA_GUARD_JS)
        self.assertIn(f".endsWith('.{urls.PLATFORM_HOST}')", interaction._MEDIA_GUARD_JS)

    def test_the_guard_mutes_before_playback_rather_than_after(self):
        # 'loadstart' is the earliest media event; catching only 'playing'
        # would mean muting a player that is already audible.
        self.assertIn("loadstart", interaction._MEDIA_GUARD_JS)

    def test_the_guard_tampers_with_no_prototype(self):
        # A wrapped play() leaves irregularities a page script can find;
        # the capture-phase listeners alone reach every element in time.
        self.assertNotIn(".play =", interaction._MEDIA_GUARD_JS)
        self.assertNotIn("prototype.play", interaction._MEDIA_GUARD_JS)

    def test_the_guard_leaves_no_named_trace_on_window(self):
        # The state lives behind a symbol keyed by a per-process token: never
        # in Object.keys, and unreadable without the token.
        self.assertIn("Symbol.for(", interaction._MEDIA_GUARD_JS)
        self.assertNotIn("__phantadexMediaGuard", interaction._MEDIA_GUARD_JS)
        self.assertNotIn("__phantadexMediaGuard", interaction._MEDIA_RELEASE_JS)

    def test_the_guard_can_be_taken_back_off(self):
        self.assertIn("AbortController", interaction._MEDIA_GUARD_JS)
        self.assertIn(".abort()", interaction._MEDIA_RELEASE_JS)
        page = mock.Mock()
        page.evaluate.return_value = True
        self.assertTrue(interaction.release_media_guard(page))
        page.evaluate.assert_called_once_with(interaction._MEDIA_RELEASE_JS)

    def test_releasing_a_page_without_the_guard_is_not_an_error(self):
        page = mock.Mock()
        page.evaluate.side_effect = RuntimeError("closed")
        self.assertFalse(interaction.release_media_guard(page))

    def test_arming_reports_whether_this_call_installed_it(self):
        page = mock.Mock()
        page.evaluate.return_value = True
        self.assertTrue(interaction.arm_media_guard(page))
        page.evaluate.return_value = False
        self.assertFalse(interaction.arm_media_guard(page))

    def test_a_failed_install_is_not_fatal(self):
        context = mock.Mock()
        context.add_init_script.side_effect = RuntimeError("detached")
        self.assertFalse(interaction.install_media_guard(context))
        page = mock.Mock()
        page.evaluate.side_effect = RuntimeError("navigating")
        self.assertFalse(interaction.arm_media_guard(page))
