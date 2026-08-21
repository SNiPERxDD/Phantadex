"""Regressions for the defects found in the package-boundaries reviews.

Each case pins behaviour that was previously wrong in a way no existing test
caught, so a future change cannot quietly reintroduce it.
"""

import argparse
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from phantadex import (
    config,
    course_manager,
    detection,
    discovery,
    element_schema,
    handlers,
    interaction,
    modals,
    navigation,
    runner,
    schema,
    storage,
    urls,
    video,
)
from phantadex.course_manager import CourseManager
from phantadex.discovery import context, course_map, probing, rules
from phantadex.discovery.state import ObservationState
from tests.fakes import FakeLocator, FakePage, capture_console

ITEM_A = "https://www.coursera.org/learn/demo/lecture/aaa/one"
ITEM_B = "/learn/demo/lecture/bbb/two"


class ExactDeduplicationTests(unittest.TestCase):
    """The archive file and the ledger entry must describe the same content."""

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, "item.txt")

    def _read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_a_small_correction_is_saved_rather_than_discarded(self):
        # A one-sentence fix in a long transcript stays >95% similar, so the
        # old ratio-based check dropped the file while the ledger took the new
        # text -- the two then disagreed about what had been archived.
        original = "Sentence one. " * 200
        corrected = original + "One corrected sentence."
        storage.save_versioned(self.path, original)
        written = storage.save_versioned(self.path, corrected)
        self.assertNotEqual(written, self.path)
        self.assertEqual(self._read(written), corrected)

    def test_identical_content_is_not_rewritten(self):
        body = "Unchanged body text."
        storage.save_versioned(self.path, body)
        self.assertEqual(storage.save_versioned(self.path, body), self.path)

    def test_cosmetic_whitespace_is_not_a_new_version(self):
        # Page extraction is not byte-stable: the same reading yields different
        # indentation and blank-line runs depending on when the DOM settled.
        storage.save_versioned(self.path, "Line one\nLine two")
        again = storage.save_versioned(self.path, "Line one   \n\n\tLine  two\n\n")
        self.assertEqual(again, self.path)

    def test_a_single_changed_word_is_still_a_new_version(self):
        storage.save_versioned(self.path, "The revenue was eighty units.")
        written = storage.save_versioned(self.path, "The revenue was ninety units.")
        self.assertNotEqual(written, self.path)


class VideoThresholdTests(unittest.TestCase):
    def test_a_low_threshold_is_rejected_only_when_out_of_range(self):
        self.assertEqual(config.parse_video_threshold("50"), (50.0, 50.0))
        self.assertEqual(config.parse_video_threshold(0), (0.0, 0.0))
        self.assertEqual(config.parse_video_threshold(100), (100.0, 100.0))

    def test_a_range_is_kept_as_its_two_bounds(self):
        self.assertEqual(config.parse_video_threshold("98-100"), (98.0, 100.0))
        self.assertEqual(config.parse_video_threshold("97.5-99%"), (97.5, 99.0))

    def test_out_of_range_thresholds_are_rejected_at_parse_time(self):
        for value in ("-1", "101", "abc", "99-101", "100-98", "98-", "98-99-100"):
            with self.assertRaises(argparse.ArgumentTypeError, msg=value):
                config.parse_video_threshold(value)

    def test_the_target_is_sampled_from_the_configured_range(self):
        settings = config.Settings(video_completion_threshold=(98.0, 100.0))
        ctx = handlers.Context(settings=settings)
        page = FakePage(url=ITEM_A)
        snapshot = {"paused": False, "ended": False, "duration": 100.0, "currentTime": 99.0}
        with (
            mock.patch.object(handlers.video, "state", return_value=dict(snapshot)),
            mock.patch.object(handlers.modals, "dismiss_all"),
            mock.patch.object(handlers.time, "sleep"),
            mock.patch.object(handlers.random, "uniform", return_value=98.6) as uniform,
        ):
            handlers.VideoHandler()._watch(page, ctx, ITEM_A)

        # 99% clears a 98.6% target, so one snapshot is enough. The bounds
        # reaching `uniform` are what proves the range was the source.
        uniform.assert_any_call(98.0, 100.0)

    def test_a_scalar_threshold_is_held_as_a_range_of_zero_width(self):
        # Callers that construct Settings directly, and anyone passing a single
        # number on the command line, must still land on exactly that value.
        self.assertEqual(
            config.Settings(video_completion_threshold=95).video_completion_threshold, (95.0, 95.0)
        )

    def test_the_configured_threshold_is_the_watch_target(self):
        # The target used to be max(threshold, uniform(97, 100)), which made
        # every value below ~97 silently inoperative.
        settings = config.Settings(video_completion_threshold=50.0)
        ctx = handlers.Context(settings=settings)
        page = FakePage(url=ITEM_A)
        snapshots = [
            {"paused": False, "ended": False, "duration": 100.0, "currentTime": 20.0},
            {"paused": False, "ended": False, "duration": 100.0, "currentTime": 55.0},
            {"paused": False, "ended": False, "duration": 100.0, "currentTime": 99.0},
        ]
        state = mock.Mock(side_effect=snapshots)
        with mock.patch.object(handlers.video, "state", state):
            with mock.patch.object(handlers.modals, "dismiss_all"):
                with mock.patch.object(handlers.time, "sleep"):
                    handlers.VideoHandler()._watch(page, ctx, ITEM_A)
        # Stopping on the 55% snapshot proves the loop honoured 50, not a
        # 97-100 band -- the third snapshot is never read.
        self.assertEqual(state.call_count, 2)

    def test_frozen_playback_is_abandoned_rather_than_watched_forever(self):
        settings = config.Settings(video_completion_threshold=100.0)
        ctx = handlers.Context(settings=settings)
        page = FakePage(url=ITEM_A)
        stuck = {"paused": False, "ended": False, "duration": 100.0, "currentTime": 12.0}
        with mock.patch.object(handlers, "FROZEN_TICK_LIMIT", 3):
            with mock.patch.object(handlers.video, "state", return_value=dict(stuck)):
                with mock.patch.object(handlers.modals, "dismiss_all"):
                    with mock.patch.object(handlers.time, "sleep"):
                        handlers.VideoHandler()._watch(page, ctx, ITEM_A)


class PluginDwellTests(unittest.TestCase):
    def test_a_sub_minute_request_is_not_rounded_up_to_a_full_minute(self):
        content = mock.Mock()
        content.inner_text.return_value = "word " * 400
        content.bounding_box.return_value = {"x": 0, "y": 0, "width": 100, "height": 100}
        duration_sec, _x, _y = interaction._session_geometry(
            FakePage(), content, True, handlers.PLUGIN_DWELL_MINUTES
        )
        self.assertEqual(duration_sec, int(handlers.PLUGIN_DWELL_MINUTES * 60))

    def test_a_normal_reading_still_gets_the_one_minute_floor(self):
        content = mock.Mock()
        content.inner_text.return_value = "word"
        content.bounding_box.return_value = {"x": 0, "y": 0, "width": 100, "height": 100}
        duration_sec, _x, _y = interaction._session_geometry(FakePage(), content, True, 10)
        self.assertEqual(duration_sec, int(interaction.MINIMUM_DWELL_MINUTES * 60))


class InterruptedReadingTests(unittest.TestCase):
    def test_an_interrupted_session_reaches_the_runner_failure_counter(self):
        # Returning CONTINUE re-entered this handler forever: no exception ever
        # reached the runner, so the three-strike bail-out never fired.
        ctx = handlers.Context(settings=config.Settings())
        ctx.manager = None
        page = FakePage(url=ITEM_A)
        with mock.patch.object(handlers.page_ops, "extract_reading", return_value="Body."):
            with mock.patch.object(
                handlers.page_ops, "detect_reading_minutes", return_value=(5, "header")
            ):
                with mock.patch.object(
                    handlers.interaction, "reading_session", return_value="INTERRUPTED"
                ):
                    with self.assertRaises(RuntimeError):
                        handlers.ReadingHandler().handle(page, ctx)

    def test_a_navigated_session_returns_quietly(self):
        ctx = handlers.Context(settings=config.Settings())
        page = FakePage(url=ITEM_A)
        with mock.patch.object(handlers.page_ops, "extract_reading", return_value="Body."):
            with mock.patch.object(
                handlers.page_ops, "detect_reading_minutes", return_value=(5, "header")
            ):
                with mock.patch.object(
                    handlers.interaction, "reading_session", return_value="NAVIGATED"
                ):
                    self.assertEqual(handlers.ReadingHandler().handle(page, ctx), handlers.CONTINUE)


class LedgerStateTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.course_map = {
            "Module 1": [
                ("One", "VIDEO", "/learn/demo/lecture/aaa/one", "1 min"),
                ("Two", "VIDEO", ITEM_B, "1 min"),
            ]
        }
        self.manager = CourseManager(self.course_map, "Demo Course", root_dir=self.root)

    def _item(self, url):
        root = ET.parse(self.manager.xml_path).getroot()
        return root.find(f".//item[@url='{url}']")

    def test_an_unmapped_item_is_distinguishable_from_the_last_item(self):
        self.assertTrue(self.manager.is_mapped(ITEM_B))
        self.assertFalse(self.manager.is_mapped("/learn/demo/supplement/zzz/never-mapped"))
        self.assertIsNone(self.manager.get_next_url(ITEM_B))
        self.assertIsNone(self.manager.get_next_url("/learn/demo/supplement/zzz/never-mapped"))

    def test_a_persistent_failure_is_recorded(self):
        self.assertTrue(self.manager.mark_failed(ITEM_B, "detached DOM"))
        self.assertEqual(self._item(ITEM_B).get("status"), "failed")
        self.assertIn(urls.normalize_path(ITEM_B), self.manager.failed_paths())

    def test_a_later_success_clears_the_failure_flag(self):
        self.manager.mark_failed(ITEM_B, "detached DOM")
        self.manager.save_content(ITEM_B, "Recovered transcript.", "Transcript")
        self.assertIsNone(self._item(ITEM_B).get("status"))
        self.assertEqual(self.manager.failed_paths(), set())


class UnmappedNavigationTests(unittest.TestCase):
    """An item missing from the map must not be reported as course completion."""

    class _Manager:
        def __init__(self, mapped):
            self._mapped = mapped

        def is_mapped(self, _url):
            return self._mapped

        def get_next_url(self, _url):
            return None

    def test_an_unmapped_item_fails_instead_of_completing_the_course(self):
        result = navigation._ledger_fallback(FakePage(url=ITEM_A), self._Manager(False))
        self.assertEqual(result, "FAILED")

    def test_the_genuine_last_item_still_completes_the_course(self):
        result = navigation._ledger_fallback(FakePage(url=ITEM_A), self._Manager(True))
        self.assertEqual(result, "COURSE_COMPLETE")


class SidebarSelectorTests(unittest.TestCase):
    """The outline selector must reach real rows without matching article text."""

    def test_the_heading_fallback_is_confined_to_outline_containers(self):
        # Bare "h2, h3" also matched the section headings inside a reading's own
        # body, and those were walked as module headers.
        for part in course_map.SIDEBAR_ITEM_SELECTOR.split(", "):
            if part.strip() in ("h2", "h3"):
                self.fail(f"unscoped heading selector: {part!r}")

    def test_the_outline_specific_parts_are_left_unscoped(self):
        # Coursera renders the outline inside a CDS accordion panel, not a
        # <nav>; scoping these two parts emptied the course map entirely.
        parts = [part.strip() for part in course_map.SIDEBAR_ITEM_SELECTOR.split(", ")]
        self.assertIn("button.cds-AccordionHeader-button", parts)
        self.assertIn("a[aria-label][href*='/learn/']", parts)


class ItemIdentityTests(unittest.TestCase):
    def test_a_shared_slug_prefix_is_not_the_same_item(self):
        # Raw containment matched "intro" inside "intro-part-2" and attached the
        # wrong module name to the archived item.
        self.assertFalse(
            context._href_matches(
                "/learn/x/lecture/Ab/intro",
                "https://www.coursera.org/learn/x/lecture/Cd/intro-part-2",
            )
        )

    def test_the_matching_row_is_still_found(self):
        self.assertTrue(
            context._href_matches(
                "/learn/x/lecture/Ab/intro", "https://www.coursera.org/learn/x/lecture/Ab/intro"
            )
        )

    def test_a_relative_ledger_path_matches_its_absolute_url(self):
        # The segment-boundary fallback was unreachable: normalize_path always
        # prefixes "/", so the leading empty segment never matched.
        self.assertTrue(
            urls.same_item(
                "/lecture/AbCd/welcome", "https://www.coursera.org/x/lecture/AbCd/welcome"
            )
        )
        self.assertFalse(urls.same_item("/a/b/c", "/x/q/r/s"))


class QuizHandoffTests(unittest.TestCase):
    """A cleared graded quiz must be left behind, not re-entered every tick."""

    class _ClearingPage(FakePage):
        """A quiz page whose markup disappears after ``clears_after`` checks."""

        def __init__(self, clears_after=1, **kwargs):
            super().__init__(**kwargs)
            self.clears_after = clears_after
            self.checks = 0

        def locator(self, selector, has_text=None):
            if selector == detection.QUIZ_SELECTORS:
                self.checks += 1
                present = self.checks <= self.clears_after
                return FakeLocator(count=1 if present else 0)
            return super().locator(selector, has_text)

    def _handle(self, page):
        ctx = handlers.Context(settings=mock.Mock())
        with (
            mock.patch.object(handlers.time, "sleep"),
            mock.patch.object(handlers.detection, "is_graded", return_value=True),
            mock.patch.object(handlers.navigation, "advance", return_value="NAVIGATED") as advance,
        ):
            outcome = handlers.QuizHandler().handle(page, ctx)
        return outcome, advance

    def test_a_cleared_quiz_advances_instead_of_spinning(self):
        # Returning CONTINUE without advancing left the runner re-classifying
        # the same page forever, with no delay on that path.
        page = self._ClearingPage(url=ITEM_A.replace("/lecture/", "/quiz/"))
        outcome, advance = self._handle(page)
        self.assertEqual(outcome, handlers.CONTINUE)
        advance.assert_called_once()

    def test_absence_is_confirmed_before_the_quiz_counts_as_cleared(self):
        page = self._ClearingPage(clears_after=0, url=ITEM_A.replace("/lecture/", "/quiz/"))
        self._handle(page)
        self.assertGreaterEqual(page.checks, handlers.QUIZ_ABSENT_CONFIRMATIONS)

    def test_a_user_navigating_away_is_not_advanced_again(self):
        page = self._ClearingPage(url=ITEM_A.replace("/lecture/", "/quiz/"))
        original_locator = page.locator

        def _navigate_then_report(selector, has_text=None):
            page.url = ITEM_B
            return original_locator(selector, has_text)

        page.locator = _navigate_then_report
        outcome, advance = self._handle(page)
        self.assertEqual(outcome, handlers.CONTINUE)
        advance.assert_not_called()


class CorruptLedgerTests(unittest.TestCase):
    """An unreadable ledger must be kept, not rebuilt over."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.course_map = {"Module 1": [("One", "VIDEO", ITEM_A, "1 min")]}

    def test_a_truncated_ledger_is_set_aside_rather_than_overwritten(self):
        manager = CourseManager(self.course_map, "Demo Course", root_dir=self.root)
        manager.save_content(ITEM_A, "Archived transcript body.", "Transcript")
        with open(manager.xml_path, encoding="utf-8") as handle:
            good = handle.read()
        with open(manager.xml_path, "w", encoding="utf-8") as handle:
            # Truncated after the archived text but before the closing tags:
            # unparseable, yet still the only copy of that content.
            handle.write(good[: good.index("</content>")])

        CourseManager(self.course_map, "Demo Course", root_dir=self.root)

        kept = [name for name in os.listdir(manager.root_dir) if ".corrupt-" in name]
        self.assertEqual(len(kept), 1)
        with open(os.path.join(manager.root_dir, kept[0]), encoding="utf-8") as handle:
            self.assertIn("Archived transcript body.", handle.read())


class DiscussionArchiveTests(unittest.TestCase):
    """Discussions are archivable, so every archival path must know about them."""

    def test_a_discussion_file_on_disk_counts_as_archived(self):
        root = tempfile.mkdtemp()
        course_map = {"Module 1": [("Prompt", "DISCUSSION", ITEM_A, "10 min")]}
        manager = CourseManager(course_map, "Demo Course", root_dir=root)
        target = os.path.join(manager.root_dir, manager.filename_for(ITEM_A, "Discussion"))
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("The prompt body, long enough to count as content.")
        os.remove(manager.xml_path)
        self.assertTrue(manager.is_archived(ITEM_A))

    def test_a_skipped_discussion_is_archived_first(self):
        # PROMPTED_TYPES includes DISCUSSION, so skipping one used to leave a
        # permanently empty <content> row behind.
        self.assertIn(detection.DISCUSSION, runner.ARCHIVED_ON_SKIP)
        self.assertEqual(runner.ARCHIVED_ON_SKIP[detection.DISCUSSION].content_type, "Discussion")

    def test_every_archivable_type_has_a_content_suffix(self):
        self.assertEqual(
            len(course_manager.ARCHIVABLE_TYPES), len(course_manager.ARCHIVE_CONTENT_TYPES)
        )

    def test_the_discussion_segment_is_recognised_by_the_scanner(self):
        # Coursera's segment is "discussionPrompt"; "/discussion/" never matched
        # it, so every discussion scanned as UNKNOWN.
        page = FakePage(url="/learn/x/discussionPrompt/AbCd/prompt", title="Prompt")
        self.assertEqual(discovery.detect_page_type(page), "DISCUSSION")


class ScannerSegmentTableTests(unittest.TestCase):
    """The scanner and the classifier read the same segment table."""

    def test_every_classified_segment_is_recognised_by_the_scanner(self):
        known = {
            fragment.strip("/")
            for _label, fragments in rules.URL_SEGMENT_MARKERS
            for fragment in fragments
        }
        missing = {
            segment for segment in detection.URL_SEGMENT_LABELS if segment.lower() not in known
        }
        self.assertEqual(missing, set())

    def test_an_assignment_submission_is_not_scanned_as_unknown(self):
        page = FakePage(url="/learn/x/assignment-submission/AbCd/exercise", title="Exercise")
        self.assertEqual(discovery.detect_page_type(page), "ASSIGNMENT")

    def test_every_scanned_label_has_a_category_set(self):
        # An unrecognised label falls back to scanning every category.
        labels = {label for label, _ in rules.URL_SEGMENT_MARKERS if label != "WRAPUP"}
        self.assertTrue(labels <= set(probing.RELEVANT_CATEGORIES))


class FailedItemRetryTests(unittest.TestCase):
    """Three strikes must stay three strikes when the skip itself fails."""

    def _runner(self):
        settings = mock.Mock(idle_poll_seconds=0)
        instance = runner.Runner(settings)
        instance.handler_failures = {urls.normalize_path(ITEM_A): 3}
        return instance

    def test_a_failed_advance_leaves_the_counter_standing(self):
        instance = self._runner()
        with (
            mock.patch.object(runner.navigation, "advance", return_value="FAILED"),
            mock.patch.object(runner.time, "sleep"),
        ):
            instance._advance_failed_item(FakePage(url=ITEM_A), urls.normalize_path(ITEM_A))
        self.assertEqual(instance.handler_failures[urls.normalize_path(ITEM_A)], 3)

    def test_a_successful_advance_clears_the_counter(self):
        instance = self._runner()
        with mock.patch.object(runner.navigation, "advance", return_value="NAVIGATED"):
            instance._advance_failed_item(FakePage(url=ITEM_A), urls.normalize_path(ITEM_A))
        self.assertEqual(instance.handler_failures, {})


class ModalScopeTests(unittest.TestCase):
    """A page-scoped rule must not click a same-named button elsewhere."""

    def test_the_dismiss_button_is_looked_for_beside_its_heading(self):
        button = FakeLocator(count=1)
        container = FakeLocator(count=1, children={"button:has-text('Continue')": button})
        header = FakeLocator(count=1, children={"xpath=ancestor::*[.//button][1]": container})
        page = FakePage(locators={"h1, h2|Coursera Honor Code": header})

        with (
            mock.patch.object(modals.time, "sleep"),
            mock.patch("phantadex.interaction.click", return_value=True),
        ):
            handled = modals._dismiss_one(
                page, "Coursera Honor Code", "h1, h2", ("Continue",), "accepted"
            )

        self.assertTrue(handled)

    def test_a_heading_with_no_button_beside_it_dismisses_nothing(self):
        # A reading whose own body contains the word "Reflect" reaches this
        # path; without the container scope it clicked a page-wide "Continue".
        header = FakeLocator(count=1)
        page = FakePage(locators={"h1, h2, h3|Reflect": header})
        with (
            mock.patch.object(modals.time, "sleep"),
            mock.patch("phantadex.interaction.click", return_value=True) as click,
        ):
            handled = modals._dismiss_one(page, "Reflect", "h1, h2, h3", ("Continue",), "skipped")
        self.assertFalse(handled)
        click.assert_not_called()


class SelectorStateWriteTests(unittest.TestCase):
    """Discovery must persist what it learned, not the shipped defaults."""

    def test_the_state_view_excludes_the_packaged_defaults(self):
        directory = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {schema.STATE_DIR_ENV: directory}):
            schema.reload_verified_selectors()
            with open(schema.state_config_path(), "w", encoding="utf-8") as handle:
                handle.write("navigation:\n  next_item: button.learned\n")
            state = schema.state_selectors()
            merged = schema.verified_selectors()
            schema.reload_verified_selectors()

        self.assertEqual(state, {"navigation": {"next_item": "button.learned"}})
        self.assertGreater(len(merged), len(state))

    def test_a_probe_that_restates_a_default_is_not_written_to_state(self):
        # Seeding from the merged view copied every shipped default into user
        # state, where it then shadowed the next upgrade of the packaged config.
        existing = {
            category: {name: "button.default" for name in elements}
            for category, elements in element_schema.ELEMENTS_SCHEMA.items()
        }
        with (
            mock.patch.object(probing, "_probe_element", return_value=("button.default", "css")),
            mock.patch.object(probing, "_save_findings") as save,
            mock.patch.object(probing.rules, "detect_page_type", return_value="VIDEO"),
            mock.patch.object(probing.context, "get_page_metadata", return_value=("m", "i", "")),
            mock.patch("time.sleep"),
            mock.patch.object(schema, "state_selectors", return_value={}),
            capture_console(),
        ):
            probing.discover_selectors(FakePage(), ObservationState(selectors=existing))
        save.assert_not_called()


class ScrollNoOpTests(unittest.TestCase):
    def test_a_zero_delta_does_not_touch_the_page(self):
        # At the bottom of a reading, _next_delta returns 0; the fallback then
        # fired on every remaining tick with a pointless scrollTop write.
        scroller = mock.Mock()
        with mock.patch.object(interaction, "scroll_metrics", return_value=None) as metrics:
            self.assertIsNone(interaction.scroll_by(FakePage(), scroller, 0, 10, 10))
        metrics.assert_called_once_with(scroller)
        scroller.evaluate.assert_not_called()


class CrossCourseIdentityTests(unittest.TestCase):
    def test_two_courses_week_pages_are_not_the_same_item(self):
        # Segment 4 of /learn/<course>/home/week/1 is "week", which matched
        # every week page of every course.
        self.assertFalse(urls.same_item("/learn/one/home/week/1", "/learn/two/home/week/1"))
        self.assertEqual(urls.item_id("/learn/one/home/week/1"), "")

    def test_the_same_id_in_another_course_is_another_item(self):
        self.assertFalse(urls.same_item("/learn/one/lecture/AbCd/x", "/learn/two/lecture/AbCd/x"))

    def test_a_real_item_is_still_matched_across_url_forms(self):
        self.assertTrue(
            urls.same_item(
                "/learn/one/lecture/AbCd/intro",
                "https://www.coursera.org/learn/one/lecture/AbCd/intro?utm=1",
            )
        )


if __name__ == "__main__":
    unittest.main()


class SeekEventTests(unittest.TestCase):
    """The seek must not forge player events."""

    def test_the_seek_script_dispatches_no_synthetic_event(self):
        # Assigning currentTime makes the browser fire seeking, seeked and
        # timeupdate itself. The extra dispatched timeupdate was redundant, and
        # was the only event on the page with isTrusted false.
        self.assertNotIn("dispatchEvent", video._SEEK_JS)
        self.assertIn("video.currentTime = targetSeconds", video._SEEK_JS)
