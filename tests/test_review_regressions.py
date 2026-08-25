"""Regressions for the defects found in the package-boundaries reviews.

Each case pins behaviour that was previously wrong in a way no existing test
caught, so a future change cannot quietly reintroduce it.
"""

import argparse
import contextlib
import io
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from phantadex import (
    cli,
    config,
    course_manager,
    detection,
    discovery,
    element_schema,
    handlers,
    interaction,
    limits,
    modals,
    navigation,
    page_ops,
    progress,
    runner,
    schema,
    storage,
    urls,
    video,
)
from phantadex.course_manager import CourseManager
from phantadex.discovery import context, course_map, observation, probing, rules
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
        page = self._ClearingPage(clears_after=1, url=ITEM_A.replace("/lecture/", "/quiz/"))
        self._handle(page)
        self.assertGreaterEqual(page.checks, handlers.QUIZ_ABSENT_CONFIRMATIONS)

    def test_markup_that_never_rendered_does_not_end_the_pause(self):
        # A graded quiz sits behind a start screen, so its markup is absent
        # before the attempt as well as after one. Counting absence from the
        # first poll ended --pause-on-graded about four seconds after it
        # announced a wait. Only absence *after* the quiz was actually seen
        # means the attempt is over; otherwise the wait runs until the user
        # leaves the item.
        page = self._ClearingPage(clears_after=0, url=ITEM_A.replace("/lecture/", "/quiz/"))
        original_locator = page.locator

        def _navigate_after_several_checks(selector, has_text=None):
            if page.checks > handlers.QUIZ_ABSENT_CONFIRMATIONS + 2:
                page.url = ITEM_B
            return original_locator(selector, has_text)

        page.locator = _navigate_after_several_checks
        outcome, advance = self._handle(page)
        self.assertGreater(page.checks, handlers.QUIZ_ABSENT_CONFIRMATIONS)
        advance.assert_not_called()
        self.assertEqual(outcome, handlers.CONTINUE)

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


class MetadataLoadingTests(unittest.TestCase):
    """The watch loop survives the state every video starts in."""

    def test_a_player_still_loading_metadata_does_not_end_the_item(self):
        """A player reports duration 0 until its metadata arrives.

        The completion check was written outside the block that computes the
        percentage, so the first tick of every video read a name that had never
        been bound. The runner caught the error, counted the item as failed
        three times over and skipped it -- which silently stopped `pdex watch`
        watching anything at all.
        """
        loading = {
            "duration": 0.0,
            "currentTime": 0.0,
            "paused": False,
            "ended": False,
            "muted": True,
        }
        watching = {
            "duration": 100.0,
            "currentTime": 99.0,
            "paused": False,
            "ended": False,
            "muted": True,
        }
        page = FakePage(url="https://www.coursera.org/learn/c/lecture/aaa/v")
        ctx = mock.Mock()
        ctx.settings.video_completion_threshold = (98.0, 100.0)
        ctx.settings.paused_iterations_before_resume = 3

        with (
            mock.patch.object(handlers.video, "state", side_effect=[loading, watching]),
            mock.patch.object(handlers.modals, "dismiss_all"),
            mock.patch.object(handlers.time, "sleep"),
            mock.patch.object(handlers.jitter, "duration", return_value=0),
            mock.patch.object(handlers.VideoHandler, "_idle_fidget"),
            mock.patch.object(handlers.random, "uniform", return_value=98.0),
            capture_console() as console,
        ):
            handlers.VideoHandler()._watch(page, ctx, page.url)

        self.assertIn("reached 98.0% target", console.getvalue())


class SeekEventTests(unittest.TestCase):
    """The seek must not forge player events."""

    def test_the_seek_script_dispatches_no_synthetic_event(self):
        # Assigning currentTime makes the browser fire seeking, seeked and
        # timeupdate itself. The extra dispatched timeupdate was redundant, and
        # was the only event on the page with isTrusted false.
        self.assertNotIn("dispatchEvent", video._SEEK_JS)
        self.assertIn("node.currentTime = targetSeconds", video._SEEK_JS)


# The first selector the schema lists for the timeline. Taking it from the
# schema rather than repeating the string keeps these fakes matching the
# selector the code will really look for: when the schema still said
# "div.video-player-progress-bar" -- a selector that matches nothing on the
# live player, which is a span -- the fakes agreed with it and the tests passed.
_BAR_SELECTOR = element_schema.ELEMENTS_SCHEMA["video_controls"]["progress_bar"]["selectors"][0]


class _SeekingPage(FakePage):
    """A page whose player reports a scripted sequence of positions."""

    def __init__(self, positions, with_bar=True):
        locators = {_BAR_SELECTOR: FakeLocator(count=1)} if with_bar else {}
        super().__init__(url="https://www.coursera.org/learn/c/lecture/aaa/v", locators=locators)
        self._positions = list(positions)

    def evaluate(self, script, *_args):
        self.evaluated.append(script)
        if "currentTime" not in script:
            return None
        position = self._positions.pop(0) if self._positions else 99.0
        if isinstance(position, dict):
            return position
        return {
            "duration": 100.0,
            "currentTime": position,
            "paused": False,
            "ended": False,
            "muted": True,
        }


class SeekControlTests(unittest.TestCase):
    """The seek prefers the player's own timeline control over a direct jump."""

    def setUp(self):
        patcher = mock.patch.object(video.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        range_patcher = mock.patch.object(video.random, "uniform", return_value=96.0)
        range_patcher.start()
        self.addCleanup(range_patcher.stop)

    def test_a_working_progress_bar_click_seeks_without_a_direct_jump(self):
        page = _SeekingPage([100.0, 10.0, 10.0])
        self.assertTrue(video.seek_into_range(page, "95-97%"))
        self.assertNotIn(video._SEEK_JS, page.evaluated)

    def test_a_bar_that_does_not_seek_falls_back_to_the_direct_route(self):
        # The bar matched but the click moved nothing; reporting success would
        # leave the run watching from the old position.
        page = _SeekingPage([100.0, 10.0], with_bar=True)
        self.assertTrue(video.seek_into_range(page, "95-97%"))
        self.assertIn(video._SEEK_JS, page.evaluated)

    def test_a_bar_that_clicks_but_does_not_seek_is_not_mistaken_for_one(self):
        """Playing on is not seeking.

        ``mute_and_play`` runs first, so the position advances about a second
        per second whatever the click did. Accepting "the position moved" as
        proof of a seek reported success for a bar that matched and did
        nothing, and the run then watched from the old position while the log
        claimed it was near the end.
        """

        class _PlayingOn(FakePage):
            def __init__(self):
                super().__init__(
                    url="https://www.coursera.org/learn/c/lecture/aaa/v",
                    locators={_BAR_SELECTOR: FakeLocator(count=1)},
                )
                self.position = 10.0

            def evaluate(self, script, *_args):
                self.evaluated.append(script)
                if "currentTime" not in script:
                    return None
                self.position += 0.5
                return {
                    "duration": 600.0,
                    "currentTime": self.position,
                    "paused": False,
                    "ended": False,
                    "muted": True,
                }

        page = _PlayingOn()
        self.assertFalse(video.seek_via_ui(page, 0.96))

    def test_a_click_that_lands_near_the_point_clicked_counts_as_a_seek(self):
        """A real seek arrives; the check tolerates the pixel it landed on."""

        class _Seeks(FakePage):
            def __init__(self):
                super().__init__(
                    url="https://www.coursera.org/learn/c/lecture/aaa/v",
                    locators={_BAR_SELECTOR: FakeLocator(count=1)},
                )
                self.position = 10.0
                self.clicked = False

            def evaluate(self, script, *_args):
                self.evaluated.append(script)
                if "currentTime" not in script:
                    return None
                if self.clicked:
                    # Landed a few seconds short of 576, as a pixel-wide click
                    # on a ten-minute video does.
                    self.position = 570.0
                self.clicked = True
                return {
                    "duration": 600.0,
                    "currentTime": self.position,
                    "paused": False,
                    "ended": False,
                    "muted": True,
                }

        page = _Seeks()
        self.assertTrue(video.seek_via_ui(page, 0.96))
        self.assertNotIn(video._SEEK_JS, page.evaluated)

    def test_without_a_progress_bar_the_direct_route_still_seeks(self):
        playing = {
            "duration": 100.0,
            "currentTime": 10.0,
            "paused": False,
            "ended": False,
            "muted": True,
        }
        page = _SeekingPage([playing, dict(playing)], with_bar=False)
        self.assertTrue(video.seek_into_range(page, "95-97%"))
        self.assertIn(video._SEEK_JS, page.evaluated)

    def test_a_drag_handle_is_not_clicked_as_though_it_were_the_timeline(self):
        """The slider role belongs to the handle, not the bar it rides on.

        Both are visible and both sit in the control bar, so only the width
        separates them. A click at a fraction of a ten-pixel handle lands in
        the handle, and the position it seeks to is arbitrary.
        """

        class _Sized(FakeLocator):
            def __init__(self, width, **kwargs):
                super().__init__(**kwargs)
                self._width = width

            def bounding_box(self):
                return {"x": 0, "y": 0, "width": self._width, "height": 10}

        class _HandleOnly(FakePage):
            def __init__(self):
                super().__init__(
                    url="https://www.coursera.org/learn/c/lecture/aaa/v",
                    locators={
                        # A player the width of a real one, and a candidate the
                        # width of the handle that rides on its timeline.
                        "video": _Sized(1239, count=1),
                        _BAR_SELECTOR: _Sized(10, count=1),
                    },
                )

            def evaluate(self, script, *_args):
                self.evaluated.append(script)
                if "currentTime" not in script:
                    return None
                return {
                    "duration": 600.0,
                    "currentTime": 10.0,
                    "paused": False,
                    "ended": False,
                    "muted": True,
                }

        page = _HandleOnly()
        self.assertFalse(video.seek_via_ui(page, 0.96))
        self.assertEqual(page.locator(_BAR_SELECTOR).clicked, 0)


class TimelineSchemaTests(unittest.TestCase):
    """The video controls name the elements they claim to name.

    Checked against the live player: the timeline is a span carrying
    ``data-testid="video-progress-bar"``, and the only element with a slider
    role is the ten-pixel drag handle inside it. The schema had named that
    handle as the timeline, as the current time and, through a last-child
    fallback, as the duration.
    """

    def _selectors(self, name):
        return element_schema.ELEMENTS_SCHEMA["video_controls"][name]["selectors"]

    def test_no_control_is_addressed_by_the_drag_handle(self):
        handle = ["span[aria-label='Video Progress']", "div[role='slider']"]
        for name in ("progress_bar", "current_time", "duration"):
            for selector in self._selectors(name):
                self.assertNotIn(selector, handle, f"{name} names the drag handle")
                self.assertNotIn("role='slider'", selector, f"{name} names a slider")

    def test_no_control_is_shared_between_two_categories(self):
        seen = {}
        for name, block in element_schema.ELEMENTS_SCHEMA["video_controls"].items():
            for selector in block["selectors"]:
                # Play and pause are the same button in two states, so their
                # selectors are allowed to be distinct but adjacent; anything
                # else appearing twice means one of the two mappings is wrong.
                if selector in seen:
                    self.fail(f"{selector!r} maps to both {seen[selector]} and {name}")
                seen[selector] = name

    def test_the_timeline_is_not_addressed_as_a_div(self):
        # The live element is a span; the div form matched nothing at all, so
        # the first selector tried was dead on every page.
        for selector in self._selectors("progress_bar"):
            self.assertFalse(selector.startswith("div."))


class PauseControlTests(unittest.TestCase):
    """Playback is stopped through the control, never by a direct write."""

    def setUp(self):
        patcher = mock.patch.object(interaction.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_playing_video_is_paused_through_its_control(self):
        page = FakePage(
            url="https://www.coursera.org/learn/c/lecture/aaa/v",
            evaluate_result={
                "duration": 100.0,
                "currentTime": 50.0,
                "paused": False,
                "ended": False,
                "muted": True,
            },
            locators={"button[aria-label='Pause']": FakeLocator(count=1)},
        )
        self.assertTrue(video.pause_if_playing(page))
        button = page.locator("button[aria-label='Pause']")
        self.assertEqual(button.clicked, 1)
        self.assertFalse(button.click_kwargs["force"])

    def test_a_paused_or_ended_player_is_left_alone(self):
        page = FakePage(
            url="https://www.coursera.org/learn/c/lecture/aaa/v",
            evaluate_result={
                "duration": 100.0,
                "currentTime": 50.0,
                "paused": True,
                "ended": False,
                "muted": True,
            },
        )
        self.assertFalse(video.pause_if_playing(page))


class ReadOnlyLedgerTests(unittest.TestCase):
    """`pdex dex` reads the ledger; it must not bring one into existence."""

    def test_a_read_only_manager_creates_nothing_on_disk(self):
        course_map_data = {"Week 1": [("Intro", "VIDEO", ITEM_A, "5 min")]}
        with tempfile.TemporaryDirectory() as root:
            manager = CourseManager(course_map_data, "Demo", root_dir=root, read_only=True)
            self.assertFalse(os.path.exists(manager.root_dir))
            self.assertFalse(os.path.exists(manager.xml_path))
            # The question it exists to answer is still answerable.
            self.assertFalse(manager.is_archived(ITEM_A))
            self.assertEqual(manager.archived_paths(), set())
            self.assertEqual(os.listdir(root), [])

    def test_a_writing_manager_still_prepares_the_ledger(self):
        course_map_data = {"Week 1": [("Intro", "VIDEO", ITEM_A, "5 min")]}
        with tempfile.TemporaryDirectory() as root:
            manager = CourseManager(course_map_data, "Demo", root_dir=root)
            self.assertTrue(os.path.exists(manager.xml_path))

    def test_a_read_only_manager_reads_a_ledger_another_run_wrote(self):
        course_map_data = {"Week 1": [("Intro", "VIDEO", ITEM_A, "5 min")]}
        with tempfile.TemporaryDirectory() as root:
            writer = CourseManager(course_map_data, "Demo", root_dir=root)
            writer.save_content(ITEM_A, "A transcript long enough to keep.", "transcript")
            reader = CourseManager(course_map_data, "Demo", root_dir=root, read_only=True)
            self.assertTrue(reader.is_archived(ITEM_A))


class PeerReviewIsGradedTests(unittest.TestCase):
    """Both halves of a peer assignment are the user's work, not one of them."""

    def test_the_reviewing_half_is_named_as_graded(self):
        self.assertIn("REVIEW_PEERS", progress.GRADED_LABELS)
        self.assertLessEqual(progress.GRADED_LABELS, progress.SKIPPED_LABELS)

    def test_pausing_on_graded_waits_on_the_review_as_well_as_the_submission(self):
        watch = runner.Runner(config.Settings(pause_on_graded=True))
        remaining = watch._skipped_labels()
        self.assertNotIn("REVIEW_PEERS", remaining)
        self.assertNotIn(detection.PEER_REVIEW, remaining)


class EmptyReadingBodyTests(unittest.TestCase):
    """A body that rendered nothing is a failure, whatever it links to."""

    def test_links_do_not_carry_an_empty_body_past_the_length_check(self):
        body = FakeLocator(text="   \n  ", links=[("Slides", "https://example.test/deck.pptx")])
        with mock.patch.object(schema, "first_visible", return_value=body):
            self.assertIsNone(page_ops.extract_reading(FakePage(url=ITEM_A)))

    def test_a_download_button_is_still_kept_by_its_link(self):
        body = FakeLocator(text="Download", links=[("Slides", "https://example.test/deck.pptx")])
        with mock.patch.object(schema, "first_visible", return_value=body):
            text = page_ops.extract_reading(FakePage(url=ITEM_A))
        self.assertIn("https://example.test/deck.pptx", text)


class BudgetUnitTests(unittest.TestCase):
    """The item budget spends the unit the run quoted when it started."""

    def test_two_rows_of_one_peer_assignment_spend_one_item(self):
        budget = limits.Budget(items=1)
        submit = "/learn/demo/peer/AbCd/case-study"
        review = "/learn/demo/peer/AbCd/review-classmates"
        self.assertTrue(budget.admits(urls.item_id(submit), ""))
        # Same item id, different row: it is the item already being paid for.
        self.assertTrue(budget.admits(urls.item_id(review), ""))
        self.assertFalse(budget.admits(urls.item_id("/learn/demo/lecture/Zz/next"), ""))

    def test_an_unmapped_item_does_not_spend_a_module(self):
        budget = limits.Budget(modules=1)
        self.assertTrue(budget.admits("a", "Week 1"))
        # No module name means the map has not caught up with this row. Ending
        # the run on it would stop a bound that still had a module to give.
        self.assertTrue(budget.admits("b", ""))
        self.assertFalse(budget.admits("c", "Week 2"))


class FlagFirstDispatchTests(unittest.TestCase):
    """A global flag typed before the command must not swallow the command.

    ``pdex -v watch`` used to reach the default command, whose optional URL then
    took ``watch`` -- and that command opens whatever URL it is handed, so the
    user's own course tab was navigated to ``/watch`` and their place was gone.
    """

    def _dispatch(self, argv):
        seen = {}

        def record(name):
            def run(args):
                seen["command"] = name
                seen["args"] = list(args)
                return 0

            return run

        with (
            mock.patch.object(cli, "dex_main", record("dex")),
            mock.patch.object(cli.watch, "main", record("watch")),
            mock.patch.object(cli.archive, "main", record("archive")),
        ):
            code = cli.main(argv)
        return code, seen

    def test_a_flag_before_the_command_still_reaches_the_command(self):
        code, seen = self._dispatch(["-v", "watch"])
        self.assertEqual(code, 0)
        self.assertEqual(seen["command"], "watch")
        self.assertEqual(seen["args"], ["-v"])

    def test_a_flag_that_takes_a_value_is_stepped_over(self):
        _code, seen = self._dispatch(["--log-level", "DEBUG", "archive"])
        self.assertEqual(seen["command"], "archive")
        self.assertEqual(seen["args"], ["--log-level", "DEBUG"])

    def test_a_directory_named_like_a_command_is_still_a_directory(self):
        _code, seen = self._dispatch(["--transcript-dir", "watch"])
        self.assertEqual(seen["command"], "dex")
        self.assertEqual(seen["args"], ["--transcript-dir", "watch"])

    def test_an_unknown_word_after_a_flag_is_refused_not_opened(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            code, seen = self._dispatch(["-v", "bogus"])
        self.assertEqual(code, 2)
        self.assertEqual(seen, {})
        self.assertIn("bogus", stderr.getvalue())


class LedgerCharacterTests(unittest.TestCase):
    """The writer must not be able to produce a file the reader will quarantine.

    ElementTree passes XML-1.0-illegal characters straight through; the next
    read raises ParseError, and an unreadable ledger is quarantined and rebuilt
    empty -- so one vertical tab in a sidebar title costs every archived entry.
    """

    def _manager(self, course_map_rows):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return CourseManager(course_map_rows, "Course", root_dir=directory.name)

    def test_a_control_character_in_a_title_leaves_the_ledger_readable(self):
        manager = self._manager({"Week 1": [("Bad\x0bTitle", "READING", ITEM_B, "5 min")]})
        manager.save_content(ITEM_B, "Body text.", "Reading")
        item = ET.parse(manager.xml_path).getroot().find(".//item")
        self.assertEqual(item.get("title"), "BadTitle")

    def test_a_failure_reason_is_stripped_the_same_way(self):
        manager = self._manager({"Week 1": [("Title", "READING", ITEM_B, "5 min")]})
        self.assertTrue(manager.mark_failed(ITEM_B, "broke\x0chere"))
        item = ET.parse(manager.xml_path).getroot().find(".//item")
        self.assertEqual(item.get("failure_reason"), "brokehere")

    def test_body_text_is_cleaned_on_the_way_into_the_ledger(self):
        manager = self._manager({"Week 1": [("Title", "READING", ITEM_B, "5 min")]})
        manager.save_content(ITEM_B, "before\x0bafter", "Reading")
        content = ET.parse(manager.xml_path).getroot().find(".//content")
        self.assertEqual(content.text, "beforeafter")

    def test_a_lone_surrogate_is_stripped_too(self):
        # Not reachable from scraped text -- a surrogate cannot survive the
        # UTF-8 write of the .txt file that precedes the ledger entry -- but an
        # exception message reaches `mark_failed` without passing through one.
        manager = self._manager({"Week 1": [("Title", "READING", ITEM_B, "5 min")]})
        self.assertTrue(manager.mark_failed(ITEM_B, "broke\ud800here"))
        item = ET.parse(manager.xml_path).getroot().find(".//item")
        self.assertEqual(item.get("failure_reason"), "brokehere")


class FailureIsRecordedForEveryTypeTests(unittest.TestCase):
    """Giving up must not look like success, whatever the item's type.

    The ledger only lists the archivable types, so a lab or a plugin that failed
    three times matched no entry and left a debug line as its only trace.
    """

    def _manager(self, item_type):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        rows = {"Week 1": [("Lab Thing", item_type, ITEM_B, "")]}
        return CourseManager(rows, "Course", root_dir=directory.name)

    def test_an_unarchivable_item_is_adopted_so_the_failure_is_kept(self):
        manager = self._manager("PLUGIN")
        self.assertTrue(manager.mark_failed(ITEM_B, "three strikes"))
        self.assertEqual(manager.failed_paths(), {urls.normalize_path(ITEM_B)})
        item = ET.parse(manager.xml_path).getroot().find(".//item")
        self.assertEqual(item.get("type"), "PLUGIN")
        self.assertEqual(item.get("title"), "Lab Thing")

    def test_an_unmapped_item_is_filed_under_the_catch_all_module(self):
        manager = self._manager("PLUGIN")
        stranger = "/learn/demo/ungradedLab/zzz/stray"
        self.assertTrue(manager.mark_failed(stranger, "gave up"))
        self.assertIn(urls.normalize_path(stranger), manager.failed_paths())

    def test_archiving_the_item_later_clears_the_failure(self):
        manager = self._manager("READING")
        manager.mark_failed(ITEM_B, "gave up")
        manager.save_content(ITEM_B, "Body text.", "Reading")
        self.assertEqual(manager.failed_paths(), set())


class LastItemIsNotALockTests(unittest.TestCase):
    """The final item of a course renders no Next control, and neither does a lock.

    Read as a lock it sent the run backwards: retreat, get skipped forward,
    arrive nowhere new, until the stall guard ended a run that had finished.
    """

    class _Manager:
        def __init__(self, mapped, next_url):
            self._mapped = mapped
            self._next_url = next_url

        def is_mapped(self, _url):
            return self._mapped

        def get_next_url(self, _url):
            return self._next_url

    def _retreated(self, manager):
        watcher = runner.Runner(config.Settings())
        watcher.ctx.manager = manager
        with (
            mock.patch.object(runner.page_ops, "is_locked_item", return_value=True),
            mock.patch.object(runner, "navigation") as nav,
            mock.patch.object(runner, "modals"),
            mock.patch.object(runner.detection, "classify", return_value=detection.UNKNOWN),
            mock.patch.object(watcher, "_log_context"),
        ):
            watcher._tick(FakePage(url=ITEM_A))
            return nav.retreat.called

    def test_a_mapped_last_item_is_not_retreated_from(self):
        self.assertFalse(self._retreated(self._Manager(mapped=True, next_url=None)))

    def test_a_genuine_lock_still_retreats(self):
        self.assertTrue(self._retreated(self._Manager(mapped=True, next_url=ITEM_B)))

    def test_an_unmapped_item_is_never_called_the_last_one(self):
        # A slow lazy-load produces the same silence as the end of the course.
        self.assertTrue(self._retreated(self._Manager(mapped=False, next_url=None)))


class DiscoveryDoesNotSpinOrLieTests(unittest.TestCase):
    """A pass that cannot move must say so, and an empty read is not success."""

    class _Context:
        def __init__(self, page):
            self.pages = [page]

    def test_an_empty_sidebar_does_not_empty_the_exit_targets(self):
        state = ObservationState()
        state.mapped = True
        state.required_types = {"VIDEO", "READING"}
        with (
            mock.patch.object(course_map, "get_detailed_course_map", return_value={}),
            mock.patch.object(context, "get_robust_course_name", return_value="Course"),
        ):
            observation.get_sidebar_targets(FakePage(url=ITEM_A), state)
        self.assertEqual(state.required_types, {"VIDEO", "READING"})

    def test_a_hop_that_never_moves_closes_the_pass_instead_of_polling_forever(self):
        state = ObservationState()
        state.mapped = True
        state.required_types = {"VIDEO"}
        page = FakePage(url=ITEM_A)
        with (
            mock.patch.object(observation, "STILL_TICKS", 3),
            mock.patch.object(observation, "time"),
            mock.patch.object(probing, "discover_selectors", return_value={}),
            mock.patch.object(observation, "auto_hop_smart", return_value=True),
            capture_console() as console,
        ):
            observation._observe(self._Context(page), state)
        self.assertIn("not advancing", console.getvalue())


class DiscoveryClassifiesLikeTheRunTests(unittest.TestCase):
    """Discovery must give the same answer the run will give on the same page.

    A survey is served under a supplement segment, so reading the segment first
    scanned every survey as a reading and probed the wrong selector categories.
    """

    def test_a_survey_served_as_a_supplement_is_not_scanned_as_a_reading(self):
        page = FakePage(
            url="https://www.coursera.org/learn/demo/supplement/xyz/exit-survey",
            title="Exit Survey | Coursera",
        )
        self.assertEqual(rules.detect_page_type(page), "FILLER")
        self.assertEqual(detection.classify(page), detection.SURVEY)

    def test_an_ordinary_supplement_is_still_a_reading(self):
        page = FakePage(
            url="https://www.coursera.org/learn/demo/supplement/xyz/glossary",
            title="Glossary | Coursera",
        )
        self.assertEqual(rules.detect_page_type(page), "READING")


class ReadOnlyMovesNothingTests(unittest.TestCase):
    """Printing a course tree must not relocate the user's archive.

    The legacy-directory migration ran above the read-only guard, so ``pdex
    dex`` -- which asks the ledger only which items are archived -- created a
    directory and moved another one to answer the question.
    """

    MAP = {"Week 1": [("Intro", "VIDEO", ITEM_A, "5 min")]}

    def _legacy_tree(self, stack):
        root = stack.enter_context(tempfile.TemporaryDirectory())
        new_root = os.path.join(root, "phantadex")
        legacy_root = os.path.join(root, "legacy")
        legacy_course = os.path.join(legacy_root, "Demo_Course")
        os.makedirs(legacy_course)
        open(os.path.join(legacy_course, "kept.txt"), "w").close()
        stack.enter_context(mock.patch.object(course_manager.config, "TRANSCRIPT_DIR", new_root))
        stack.enter_context(mock.patch.object(course_manager, "LEGACY_TRANSCRIPT_DIR", legacy_root))
        return new_root, legacy_course

    def test_a_reader_leaves_a_legacy_archive_where_it_found_it(self):
        with contextlib.ExitStack() as stack:
            new_root, legacy_course = self._legacy_tree(stack)
            CourseManager(self.MAP, "Demo Course", root_dir=new_root, read_only=True)
            self.assertTrue(os.path.exists(legacy_course))
            self.assertFalse(os.path.exists(new_root))

    def test_a_run_still_migrates_it(self):
        with contextlib.ExitStack() as stack:
            new_root, legacy_course = self._legacy_tree(stack)
            CourseManager(self.MAP, "Demo Course", root_dir=new_root)
            self.assertFalse(os.path.exists(legacy_course))
            self.assertTrue(os.path.exists(os.path.join(new_root, "Demo_Course", "kept.txt")))


class _MapOnlyManager:
    """A manager exposing only what the closing report reads."""

    def __init__(self, course_map):
        self.course_map = course_map
        self._flat_paths = CourseManager._flatten_map(self)

    def is_archived(self, _href):
        return False

    unfinished_items = CourseManager.unfinished_items
    next_actionable = CourseManager.next_actionable
    awaiting_your_post = CourseManager.awaiting_your_post
    _archived_quietly = CourseManager._archived_quietly
    _match_indices = CourseManager._match_indices


class HalfReadSidebarIsNotACompleteCourseTests(unittest.TestCase):
    """A reading cut short must not be announced as a finished course.

    ``get_completion_status`` returns whatever it had collected when a row went
    stale under it, and a dict cut off at the top lists nothing unfinished --
    which ended a barely-started run on "already complete".
    """

    COURSE = "https://www.coursera.org/learn/c"
    ROWS = [
        ("Intro", "VIDEO", f"{COURSE}/lecture/aaa/intro", "5 min"),
        ("Notes", "READING", f"{COURSE}/supplement/bbb/notes", "10 min"),
        ("Wrap", "VIDEO", f"{COURSE}/lecture/ccc/wrap", "4 min"),
    ]

    def _finish(self, status):
        run = runner.Runner(config.Settings())
        run.ctx.manager = _MapOnlyManager({"Week 1": list(self.ROWS)})
        page = FakePage(url=self.ROWS[0][2])
        with (
            mock.patch.object(runner, "get_completion_status", return_value=status),
            mock.patch.object(runner.time, "sleep"),
            capture_console() as console,
        ):
            outcome = run._resume_or_finish(page)
        return outcome, console.getvalue()

    def test_rows_the_reading_never_reached_are_reported_rather_than_assumed(self):
        status = {urls.normalize_path(self.ROWS[0][2]): True}
        outcome, console = self._finish(status)
        self.assertEqual(outcome, runner.STOPPED)
        self.assertIn("2 rows could not be read", console)
        self.assertNotIn("already complete", console)

    def test_a_whole_reading_still_ends_on_the_plain_verdict(self):
        status = {urls.normalize_path(href): True for _t, _l, href, _d in self.ROWS}
        outcome, console = self._finish(status)
        self.assertEqual(outcome, runner.STOPPED)
        self.assertIn("already complete", console)

    def test_one_unread_row_is_counted_in_the_singular(self):
        status = {urls.normalize_path(href): True for _t, _l, href, _d in self.ROWS[:2]}
        _outcome, console = self._finish(status)
        self.assertIn("1 row could not be read", console)


class TheLedgerIsReadOnlyWhereItDecidesTests(unittest.TestCase):
    """The split must not open the ledger for rows the ledger cannot settle.

    ``owner`` consults the archive only for the self-submitted labels, but the
    check ran for every unfinished row -- and the tree counts each row twice,
    once for its module and once for the course.
    """

    def _split(self, label):
        href = f"https://www.coursera.org/learn/c/quiz/aaa/{label.lower()}"
        rows = [("Item", label, href, "")]
        asked = []

        def is_archived(url):
            asked.append(url)
            return True

        counts = progress.split(rows, {urls.normalize_path(href): False}, is_archived)
        return counts, asked

    def test_a_graded_row_is_settled_without_the_ledger(self):
        counts, asked = self._split("QUIZ")
        self.assertEqual(asked, [])
        self.assertEqual(counts.yours, 1)

    def test_a_discussion_still_asks(self):
        counts, asked = self._split("DISCUSSION")
        self.assertEqual(len(asked), 1)
        self.assertEqual(counts.yours, 1)


class OptionsMeantForACommandTests(unittest.TestCase):
    """A command's own flag typed early must be named, not stepped over.

    ``pdex --items 5 watch`` walked past the unknown option, landed on its
    value, and reported the unknown command ``'5'`` -- true, and no help at all.
    """

    def _run(self, argv):
        stderr = io.StringIO()
        with mock.patch.object(cli.sys, "stderr", stderr):
            code = cli.main(argv)
        return code, stderr.getvalue()

    def test_an_unknown_option_is_reported_as_an_option(self):
        code, error = self._run(["--items", "5", "watch"])
        self.assertEqual(code, 2)
        self.assertIn("unknown option '--items' before the command", error)
        self.assertIn("options go after its name", error)
        self.assertNotIn("unknown command", error)

    def test_a_global_flag_written_with_an_equals_sign_is_not_double_counted(self):
        flags = config.global_flags()
        self.assertEqual(cli._resolve_flag("--transcript-dir=/x", flags), (True, False))
        self.assertEqual(cli._resolve_flag("--transcript-dir", flags), (True, True))

    def test_an_unambiguous_prefix_is_accepted_the_way_argparse_accepts_it(self):
        flags = config.global_flags()
        self.assertEqual(cli._resolve_flag("--transcript-di", flags), (True, True))
        self.assertEqual(cli._resolve_flag("--nonsense", flags), (False, False))


class SilenceIsNotADriftedSelectorTests(unittest.TestCase):
    """A video with no transcript must not spend the run's one stale warning.

    ``report_stale`` speaks once per element per run, so a video that simply
    offers no transcript silenced the next video whose markup really had drifted.
    """

    def _extract(self, container):
        page = FakePage(url=ITEM_A)
        with (
            mock.patch.object(page_ops, "_transcript_from_panel", return_value=None),
            mock.patch.object(page_ops, "_transcript_from_download", return_value=None),
            mock.patch.object(page_ops.schema, "first_visible", return_value=container),
            mock.patch.object(page_ops.schema, "report_stale") as reported,
        ):
            result = page_ops.extract_transcript(page)
        return result, reported

    def test_no_panel_at_all_reports_nothing(self):
        result, reported = self._extract(None)
        self.assertEqual(result, (None, "FAILED"))
        reported.assert_not_called()

    def test_a_panel_that_yields_no_text_is_still_reported(self):
        result, reported = self._extract(object())
        self.assertEqual(result, (None, "FAILED"))
        reported.assert_called_once_with("the video transcript")
