"""Tests for the traversal loop's own decisions (not the per-type handlers)."""

import unittest
from unittest import mock

from phantadex import config, course_manager, detection, handlers, logs, runner
from tests.fakes import FakePage, capture_console


class FakeManager:
    """A course manager that records what was saved and what it already holds."""

    def __init__(self, archived=False, mapped=False, next_url=None):
        self._archived = archived
        self._mapped = mapped
        self._next_url = next_url
        self.saved = []

    def is_archived(self, _url):
        return self._archived

    def is_mapped(self, _url):
        return self._mapped

    def get_next_url(self, _url):
        return self._next_url

    def save_content(self, url, text, content_type):
        self.saved.append((url, text, content_type))
        return "Item_Reading.txt", True


class ArchiveBeforeSkipTests(unittest.TestCase):
    """A completed reading is still worth archiving before moving past it."""

    def setUp(self):
        self.runner = runner.Runner(config.Settings())
        self.page = FakePage(url="https://www.coursera.org/learn/c/supplement/abc/read")

    def _skip(self, manager, text="Body text."):
        self.runner.ctx.manager = manager
        with mock.patch.object(runner.page_ops, "extract_reading", return_value=text):
            self.runner._archive_before_skip(self.page, detection.READING)

    def test_unarchived_reading_is_saved(self):
        manager = FakeManager(archived=False)
        self._skip(manager)
        self.assertEqual(len(manager.saved), 1)
        self.assertEqual(manager.saved[0][1], "Body text.")
        self.assertEqual(manager.saved[0][2], "Reading")

    def test_reading_already_in_the_ledger_is_not_saved_twice(self):
        manager = FakeManager(archived=True)
        self._skip(manager)
        self.assertEqual(manager.saved, [])

    def test_non_reading_types_are_left_alone(self):
        manager = FakeManager(archived=False)
        self.runner.ctx.manager = manager
        with mock.patch.object(runner.page_ops, "extract_reading", return_value="x"):
            self.runner._archive_before_skip(self.page, detection.PLUGIN)
        self.assertEqual(manager.saved, [])

    def test_persistent_handler_failure_advances_after_three_attempts(self):
        current = FakePage(url="https://www.coursera.org/learn/c/lecture/abc/item")
        watch = runner.Runner(config.Settings())
        broken = mock.Mock()
        broken.handle.side_effect = RuntimeError("detached DOM")
        with (
            mock.patch.object(watch, "_detect_stuck", return_value=False),
            mock.patch.object(watch, "_sync_course_map"),
            mock.patch.object(watch, "_log_context"),
            mock.patch.object(runner.modals, "dismiss_all"),
            mock.patch.object(runner.detection, "classify", return_value=detection.VIDEO),
            mock.patch.object(runner.handlers, "for_page_type", return_value=broken),
            mock.patch.object(watch, "_skip_completed", return_value="PROCEED"),
            mock.patch.object(runner.navigation, "advance", return_value="NAVIGATED") as advance,
            mock.patch.object(runner.time, "sleep"),
        ):
            for _ in range(3):
                result = watch._tick(current)
        self.assertEqual(result, runner.handlers.CONTINUE)
        advance.assert_called_once()

    def test_locked_item_retreats_before_classification(self):
        watch = runner.Runner(config.Settings())
        watch.ctx.manager = FakeManager()
        page = FakePage(url="https://www.coursera.org/learn/c/supplement/abc/locked")
        with (
            mock.patch.object(watch, "_detect_stuck", return_value=False),
            mock.patch.object(watch, "_sync_course_map"),
            mock.patch.object(watch, "_log_context"),
            mock.patch.object(runner.page_ops, "is_locked_item", return_value=True),
            mock.patch.object(runner.navigation, "retreat", return_value="NAVIGATED") as retreat,
            mock.patch.object(runner.detection, "classify") as classify,
        ):
            result = watch._tick(page)

        self.assertEqual(result, runner.handlers.CONTINUE)
        retreat.assert_called_once_with(page, watch.ctx.manager, start_url=page.url)
        classify.assert_not_called()

    def test_empty_body_is_not_written(self):
        manager = FakeManager(archived=False)
        self._skip(manager, text="")
        self.assertEqual(manager.saved, [])

    def test_extraction_failure_does_not_block_the_skip(self):
        manager = FakeManager(archived=False)
        self.runner.ctx.manager = manager
        with mock.patch.object(
            runner.page_ops, "extract_reading", side_effect=RuntimeError("detached")
        ):
            self.runner._archive_before_skip(self.page, detection.READING)
        self.assertEqual(manager.saved, [])

    def test_no_course_map_is_a_no_op(self):
        self.runner.ctx.manager = None
        with mock.patch.object(runner.page_ops, "extract_reading") as extract:
            self.runner._archive_before_skip(self.page, detection.READING)
        extract.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class UnhandledPageTypeTests(unittest.TestCase):
    """A type with no handler must not hold the traversal forever."""

    def setUp(self):
        self.watch = runner.Runner(config.Settings())
        self.page = FakePage(url="https://www.coursera.org/learn/c/ungradedLab/abc/lab")

    def test_the_first_passes_only_wait(self):
        with (
            mock.patch.object(runner.navigation, "advance") as advance,
            mock.patch.object(runner.time, "sleep") as sleep,
        ):
            for _ in range(runner.Runner.UNHANDLED_WAIT_LIMIT - 1):
                outcome = self.watch._step_past_unhandled(self.page, detection.LAB)
        self.assertEqual(outcome, runner.handlers.CONTINUE)
        advance.assert_not_called()
        self.assertEqual(sleep.call_count, runner.Runner.UNHANDLED_WAIT_LIMIT - 1)

    def test_the_item_is_stepped_past_once_the_wait_is_spent(self):
        with (
            mock.patch.object(runner.navigation, "advance", return_value="NAVIGATED") as advance,
            mock.patch.object(runner.time, "sleep"),
        ):
            for _ in range(runner.Runner.UNHANDLED_WAIT_LIMIT):
                outcome = self.watch._step_past_unhandled(self.page, detection.LAB)
        advance.assert_called_once()
        self.assertEqual(outcome, runner.handlers.CONTINUE)

    def test_a_finished_course_is_reported_rather_than_looped(self):
        with (
            mock.patch.object(runner.navigation, "advance", return_value="COURSE_COMPLETE"),
            mock.patch.object(runner.time, "sleep"),
        ):
            for _ in range(runner.Runner.UNHANDLED_WAIT_LIMIT):
                outcome = self.watch._step_past_unhandled(self.page, detection.LAB)
        self.assertEqual(outcome, runner.handlers.COURSE_COMPLETE)

    def test_each_item_gets_its_own_wait_budget(self):
        other = FakePage(url="https://www.coursera.org/learn/c/ungradedLab/xyz/lab2")
        with (
            mock.patch.object(runner.navigation, "advance") as advance,
            mock.patch.object(runner.time, "sleep"),
        ):
            self.watch._step_past_unhandled(self.page, detection.LAB)
            self.watch._step_past_unhandled(other, detection.LAB)
        advance.assert_not_called()


class MappedManager:
    """A manager exposing only the course map the resume scan reads."""

    def __init__(self, course_map, archived=()):
        self.course_map = course_map
        self.archived = list(archived)
        self._flat_paths = course_manager.CourseManager._flatten_map(self)

    def is_archived(self, href):
        return any(course_manager.urls.same_item(href, item) for item in self.archived)

    # These read nothing but the map and the ledger, so the double borrows the
    # real ones rather than restating the rules these tests exist to exercise.
    unfinished_items = course_manager.CourseManager.unfinished_items
    next_actionable = course_manager.CourseManager.next_actionable
    awaiting_your_post = course_manager.CourseManager.awaiting_your_post
    _archived_quietly = course_manager.CourseManager._archived_quietly
    _match_indices = course_manager.CourseManager._match_indices


COURSE = "https://www.coursera.org/learn/c"
FIRST = f"{COURSE}/lecture/aaa/intro"
SECOND = f"{COURSE}/supplement/bbb/notes"
THIRD = f"{COURSE}/lecture/ccc/wrap"


class ResumeAtFirstIncompleteTests(unittest.TestCase):
    """The run starts where work is left, not where the tab happens to sit."""

    def setUp(self):
        self.watch = runner.Runner(config.Settings())
        self.watch.ctx.manager = MappedManager(
            {
                "Week 1": [("Intro", "VIDEO", FIRST, "5 min"), ("Notes", "READING", SECOND, "")],
                "Week 2": [("Wrap", "VIDEO", THIRD, "3 min")],
            }
        )
        self.page = FakePage(url=FIRST)

    def _resume(self, status):
        with (
            mock.patch.object(runner, "get_completion_status", return_value=status),
            mock.patch.object(runner.time, "sleep"),
            capture_console() as console,
        ):
            outcome = self.watch._resume_or_finish(self.page)
        self.console = console.getvalue()
        return outcome

    def _paths(self, *pairs):
        return {runner.urls.normalize_path(url): state for url, state in pairs}

    def test_jumps_over_completed_items_to_the_first_unfinished_one(self):
        outcome = self._resume(self._paths((FIRST, True), (SECOND, True), (THIRD, False)))
        self.assertEqual(outcome, handlers.CONTINUE)
        self.assertEqual(self.page.goto_calls, [THIRD])

    def test_staying_put_when_the_open_item_is_already_the_first_unfinished(self):
        outcome = self._resume(self._paths((FIRST, False), (SECOND, False)))
        self.assertIsNone(outcome)
        self.assertEqual(self.page.goto_calls, [])

    def test_a_fully_complete_course_ends_the_run_where_it_stands(self):
        outcome = self._resume(self._paths((FIRST, True), (SECOND, True), (THIRD, True)))
        self.assertEqual(outcome, runner.STOPPED)
        self.assertEqual(self.page.goto_calls, [])
        self.assertIn("already complete", self.console)

    def test_rows_whose_state_is_unreadable_are_not_treated_as_unfinished(self):
        # An unknown row says nothing about its state. Targeting one would send
        # a run that is near the end of a course back to its beginning.
        outcome = self._resume({runner.urls.normalize_path(SECOND): True})
        self.assertEqual(outcome, runner.STOPPED)
        self.assertEqual(self.page.goto_calls, [])

    def test_no_resume_leaves_the_run_on_the_open_item(self):
        self.watch.settings = config.Settings(resume_at_incomplete=False)
        outcome = self._resume(self._paths((FIRST, True), (THIRD, False)))
        self.assertIsNone(outcome)
        self.assertEqual(self.page.goto_calls, [])

    def test_a_failed_scan_is_not_fatal(self):
        with mock.patch.object(
            runner, "get_completion_status", side_effect=RuntimeError("sidebar gone")
        ):
            self.assertIsNone(self.watch._resume_or_finish(self.page))
        self.assertEqual(self.page.goto_calls, [])


class NothingLeftButGradedWorkTests(unittest.TestCase):
    """A course whose remaining items are all graded is finished, for this run.

    The run neither answers graded work nor archives it, so walking the rest of
    the course to reach that verdict is a tour with a skip prompt on every item
    already done.
    """

    QUIZ_URL = f"{COURSE}/quiz/ddd/week-1-quiz"
    PEER_URL = f"{COURSE}/peer/eee/case-study"

    def setUp(self):
        self.watch = runner.Runner(config.Settings())
        self.watch.ctx.manager = MappedManager(
            {
                "Week 1": [
                    ("Intro", "VIDEO", FIRST, "5 min"),
                    ("Week 1 Quiz", "QUIZ", self.QUIZ_URL, ""),
                ],
                "Week 2": [("Case study", "PEER_REVIEW", self.PEER_URL, "")],
            }
        )
        self.page = FakePage(url=FIRST)

    def _resume(self, status, settings=None):
        if settings is not None:
            self.watch.settings = settings
        with (
            mock.patch.object(runner, "get_completion_status", return_value=status),
            mock.patch.object(runner.time, "sleep"),
            capture_console() as console,
        ):
            outcome = self.watch._resume_or_finish(self.page)
        self.console = console.getvalue()
        return outcome

    def _paths(self, *pairs):
        return {runner.urls.normalize_path(url): state for url, state in pairs}

    def test_the_run_stops_instead_of_touring_the_graded_items(self):
        outcome = self._resume(
            self._paths((FIRST, True), (self.QUIZ_URL, False), (self.PEER_URL, False))
        )
        self.assertEqual(outcome, runner.STOPPED)
        self.assertEqual(self.page.goto_calls, [])
        self.assertIn("2 unfinished items are not this run's work", self.console)
        self.assertIn("peer review", self.console)
        self.assertIn("--pause-on-graded", self.console)

    def test_the_closing_report_splits_the_whole_course_by_who_is_left(self):
        # The count of what was walked past reads as a course barely started.
        # The line beside it says how much of that is the user's own work.
        self._resume(self._paths((FIRST, True), (self.QUIZ_URL, False), (self.PEER_URL, False)))
        self.assertIn(
            "3 items · 1 complete · nothing left to Phantadex · 2 left to you", self.console
        )

    def test_the_graded_flag_is_offered_as_a_tip_rather_than_a_step(self):
        self._resume(self._paths((FIRST, True), (self.QUIZ_URL, False)))
        self.assertIn("» --pause-on-graded", logs.strip_ansi(self.console))
        self.assertIn("waits for you to answer it", self.console)

    def test_one_such_item_is_counted_in_the_singular(self):
        outcome = self._resume(self._paths((FIRST, True), (self.QUIZ_URL, False)))
        self.assertEqual(outcome, runner.STOPPED)
        self.assertIn("1 unfinished item is not this run's work", self.console)

    def test_pausing_on_graded_makes_them_work_again(self):
        outcome = self._resume(
            self._paths((FIRST, True), (self.QUIZ_URL, False)),
            settings=config.Settings(pause_on_graded=True),
        )
        self.assertEqual(outcome, handlers.CONTINUE)
        self.assertEqual(self.page.goto_calls, [self.QUIZ_URL])

    def test_a_survey_the_map_named_is_not_work_either(self):
        # The run steps past a survey without answering or archiving it, the
        # same as a graded item, so it cannot be what a run is still open for.
        self.watch.ctx.manager.course_map["Week 2"].append(
            ("Tell us about yourself", "FILLER", f"{COURSE}/ungradedWidget/fff/survey", "")
        )
        outcome = self._resume(
            self._paths(
                (FIRST, True),
                (self.QUIZ_URL, False),
                (f"{COURSE}/ungradedWidget/fff/survey", False),
            )
        )
        self.assertEqual(outcome, runner.STOPPED)
        self.assertIn("survey", self.console)

    def test_pausing_on_graded_does_not_make_a_survey_work(self):
        self.watch.ctx.manager.course_map["Week 2"].append(
            ("Tell us about yourself", "FILLER", f"{COURSE}/ungradedWidget/fff/survey", "")
        )
        outcome = self._resume(
            self._paths((FIRST, True), (f"{COURSE}/ungradedWidget/fff/survey", False)),
            settings=config.Settings(pause_on_graded=True),
        )
        self.assertEqual(outcome, runner.STOPPED)
        self.assertNotIn("--pause-on-graded", self.console)

    def test_an_unfinished_video_still_outranks_the_graded_rows(self):
        outcome = self._resume(
            self._paths((FIRST, False), (self.QUIZ_URL, False), (self.PEER_URL, False))
        )
        self.assertIsNone(outcome)
        self.assertEqual(self.page.goto_calls, [])


class ArchivedDiscussionTests(unittest.TestCase):
    """A discussion the ledger holds is done, whatever the sidebar says.

    The platform marks a discussion complete only once the user posts to it, so
    the sidebar lists it as unfinished for the life of the course. Left as work,
    it was the item every run resumed at -- and having nothing else to do after
    it, the run walked on into whatever row came next, finished or not.
    """

    DISCUSSION_URL = f"{COURSE}/discussionPrompt/ggg/introduce-yourself"

    def setUp(self):
        self.watch = runner.Runner(config.Settings())
        self.course_map = {
            "Week 1": [
                ("Intro", "VIDEO", FIRST, "5 min"),
                ("Say hello", "DISCUSSION", self.DISCUSSION_URL, ""),
            ],
        }
        self.page = FakePage(url=FIRST)

    def _resume(self, archived=()):
        self.watch.ctx.manager = MappedManager(self.course_map, archived=archived)
        status = {
            runner.urls.normalize_path(FIRST): True,
            runner.urls.normalize_path(self.DISCUSSION_URL): False,
        }
        with (
            mock.patch.object(runner, "get_completion_status", return_value=status),
            mock.patch.object(runner.time, "sleep"),
            capture_console() as console,
        ):
            outcome = self.watch._resume_or_finish(self.page)
        self.console = console.getvalue()
        return outcome

    def test_an_archived_discussion_is_no_longer_offered_as_work(self):
        outcome = self._resume(archived=[self.DISCUSSION_URL])
        self.assertEqual(outcome, runner.STOPPED)
        self.assertEqual(self.page.goto_calls, [])

    def test_the_run_says_which_item_is_waiting_on_the_user(self):
        self._resume(archived=[self.DISCUSSION_URL])
        self.assertIn("Say hello", self.console)
        self.assertIn("waits on a post of yours", self.console)

    def test_a_discussion_the_ledger_does_not_hold_is_still_work(self):
        outcome = self._resume()
        self.assertEqual(outcome, handlers.CONTINUE)
        self.assertEqual(self.page.goto_calls, [self.DISCUSSION_URL])

    def test_a_ledger_that_cannot_be_read_leaves_the_discussion_as_work(self):
        # Refusing to work an item because the ledger check threw would silently
        # narrow a run to nothing. An unreadable ledger means "not archived".
        self.watch.ctx.manager = MappedManager(self.course_map)
        with mock.patch.object(
            MappedManager, "is_archived", side_effect=RuntimeError("ledger unreadable")
        ):
            status = {
                runner.urls.normalize_path(FIRST): True,
                runner.urls.normalize_path(self.DISCUSSION_URL): False,
            }
            with (
                mock.patch.object(runner, "get_completion_status", return_value=status),
                mock.patch.object(runner.time, "sleep"),
                capture_console(),
            ):
                outcome = self.watch._resume_or_finish(self.page)
        self.assertEqual(outcome, handlers.CONTINUE)
        self.assertEqual(self.page.goto_calls, [self.DISCUSSION_URL])


class TickMutesBeforeAnyPromptTests(unittest.TestCase):
    """Muting has to happen on the tick, not inside a handler.

    An item the sidebar already marks complete never reaches a handler: the run
    offers to skip it first, and its narration used to play aloud for the whole
    length of that prompt.
    """

    def test_a_completed_item_is_muted_before_the_skip_prompt(self):
        watch = runner.Runner(config.Settings())
        page = FakePage(url=f"{COURSE}/supplement/bbb/notes")
        with (
            mock.patch.object(runner.Runner, "_detect_stuck", return_value=False),
            mock.patch.object(runner.Runner, "_sync_course_map", return_value=False),
            mock.patch.object(runner.Runner, "_log_context"),
            mock.patch.object(runner.page_ops, "is_locked_item", return_value=False),
            mock.patch.object(runner.modals, "dismiss_all"),
            mock.patch.object(runner.detection, "classify", return_value=detection.READING),
            mock.patch.object(runner.interaction, "silence_media") as silence,
            mock.patch.object(runner.Runner, "_skip_completed", return_value="SKIPPED") as skip,
        ):
            watch._tick(page)
        silence.assert_called_once_with(page)
        skip.assert_called_once()


class UnmappedCourseTests(unittest.TestCase):
    """A run with no ledger has to say so, once."""

    def setUp(self):
        self.watch = runner.Runner(config.Settings())

    def test_a_course_that_cannot_be_named_is_reported_once(self):
        # _sync_course_map runs on every tick, so the notice has to be raised
        # once per failure rather than once per poll. Before this it was not
        # raised at all: nothing was archived and nothing said why.
        page = FakePage(url="https://www.coursera.org/learn/demo/lecture/a/one")
        with (
            mock.patch.object(runner, "get_robust_course_name", return_value=""),
            capture_console() as console,
        ):
            for _ in range(5):
                self.assertFalse(self.watch._sync_course_map(page))

        self.assertEqual(console.getvalue().lower().count("no ledger"), 1)

    def test_a_lookup_that_raises_is_reported_too(self):
        page = FakePage(url="https://www.coursera.org/learn/demo/lecture/a/one")
        with (
            mock.patch.object(runner, "get_robust_course_name", side_effect=RuntimeError("boom")),
            capture_console() as console,
        ):
            self.assertFalse(self.watch._sync_course_map(page))
        self.assertIn("no ledger", console.getvalue().lower())

    def test_nothing_is_said_once_a_ledger_is_open(self):
        page = FakePage(url="https://www.coursera.org/learn/demo/lecture/a/one")
        self.watch.ctx.manager = mock.Mock()
        with (
            mock.patch.object(runner, "get_robust_course_name", return_value=""),
            capture_console() as console,
        ):
            self.assertFalse(self.watch._sync_course_map(page))
        self.assertNotIn("no ledger", console.getvalue().lower())


COURSE_ITEM = "https://www.coursera.org/learn/demo/lecture/AbCd/one"
PLATFORM_PAGE = "https://www.coursera.org/my-learning"


class _TickHarness(unittest.TestCase):
    """Drives ``_tick`` far enough to reach the two bounds, and no further."""

    def _tick(self, watch, page):
        with (
            mock.patch.object(runner.Runner, "_detect_stuck", return_value=False),
            mock.patch.object(runner.Runner, "_sync_course_map", return_value=False),
            mock.patch.object(runner.interaction, "silence_media"),
            mock.patch.object(runner.page_ops, "is_locked_item", return_value=False),
            mock.patch.object(runner.modals, "dismiss_all"),
            mock.patch.object(runner.detection, "classify", return_value=detection.UNKNOWN),
            mock.patch.object(runner.time, "sleep"),
        ):
            return watch._tick(page)


class ItemLimitTests(_TickHarness):
    """``--items N`` covers N items and then the run ends."""

    def test_the_run_stops_when_the_item_budget_is_spent(self):
        watch = runner.Runner(config.Settings(item_limit=2))
        watch.ctx.manager = mock.Mock(**{"module_for.return_value": "Module 1"})

        outcomes = []
        for index in range(3):
            page = FakePage(url=f"https://www.coursera.org/learn/demo/lecture/id{index}/x")
            outcomes.append(self._tick(watch, page))

        self.assertEqual(outcomes[-1], runner.STOPPED)
        self.assertNotEqual(outcomes[0], runner.STOPPED)

    def test_a_peer_assignment_s_two_rows_spend_one_item_of_the_budget(self):
        # The bound was quoted against `CourseManager.item_count`, which counts
        # distinct item ids -- so the two rows of a peer assignment, one item id
        # under two URLs, must not spend two of what the user asked for.
        watch = runner.Runner(config.Settings(item_limit=2))
        watch.ctx.manager = mock.Mock(**{"module_for.return_value": "Module 1"})

        for url in (
            "https://www.coursera.org/learn/demo/peer/AbCd/case-study",
            "https://www.coursera.org/learn/demo/peer/AbCd/review-classmates",
            "https://www.coursera.org/learn/demo/lecture/Zz99/next",
        ):
            self.assertNotEqual(self._tick(watch, FakePage(url=url)), runner.STOPPED, url)
        self.assertEqual(watch.budget.summary(), "2 items across 1 module")

    def test_the_refused_item_is_never_classified(self):
        # The stop happens on arrival, before the item is touched, so the run
        # ends having watched exactly what was asked for.
        watch = runner.Runner(config.Settings(item_limit=1))
        watch.ctx.manager = mock.Mock(**{"module_for.return_value": "Module 1"})
        self._tick(watch, FakePage(url=COURSE_ITEM))

        second = FakePage(url="https://www.coursera.org/learn/demo/lecture/Zz/two")
        with capture_console() as console:
            self.assertEqual(self._tick(watch, second), runner.STOPPED)

        self.assertEqual(second.evaluated, [])
        self.assertIn("limit reached", console.getvalue())

    def test_no_limit_leaves_the_run_going(self):
        watch = runner.Runner(config.Settings())
        for index in range(30):
            page = FakePage(url=f"https://www.coursera.org/learn/demo/lecture/id{index}/x")
            self.assertNotEqual(self._tick(watch, page), runner.STOPPED)


class ModuleLimitTests(_TickHarness):
    def test_the_run_stops_on_the_first_item_of_the_module_after_the_limit(self):
        watch = runner.Runner(config.Settings(module_limit=1))
        modules = iter(["Module 1", "Module 1", "Module 2"])
        watch.ctx.manager = mock.Mock(**{"module_for.side_effect": lambda _url: next(modules)})

        outcomes = []
        for index in range(3):
            page = FakePage(url=f"https://www.coursera.org/learn/demo/lecture/id{index}/x")
            outcomes.append(self._tick(watch, page))

        self.assertEqual(outcomes, [handlers.CONTINUE, handlers.CONTINUE, runner.STOPPED])


class StallStopTests(_TickHarness):
    """A run that stops reaching new items ends instead of cycling."""

    def test_repeating_one_item_forever_ends_the_run(self):
        watch = runner.Runner(config.Settings(stall_iterations=5))
        page = FakePage(url=COURSE_ITEM)

        outcomes = [self._tick(watch, page) for _ in range(8)]

        # The real loop returns on the first STOPPED; what matters is that one
        # arrives, and that the passes before it were left alone.
        self.assertIn(runner.STOPPED, outcomes)
        self.assertNotIn(runner.STOPPED, outcomes[: watch.stall.limit])

    def test_the_stop_says_why(self):
        watch = runner.Runner(config.Settings(stall_iterations=2))
        page = FakePage(url=COURSE_ITEM)
        with capture_console() as console:
            for _ in range(5):
                self._tick(watch, page)
        self.assertIn("going in circles", console.getvalue())


class OffCourseTests(unittest.TestCase):
    """A tab on the platform but outside a course is not an item."""

    def setUp(self):
        self.watch = runner.Runner(config.Settings())

    def test_a_platform_page_is_not_treated_as_a_course_item(self):
        self.assertFalse(self.watch._inside_course(FakePage(url=PLATFORM_PAGE)))
        self.assertTrue(self.watch._inside_course(FakePage(url=COURSE_ITEM)))

    def test_the_run_steers_back_to_the_last_item_it_was_on(self):
        self.watch.last_course_url = "/learn/demo/lecture/AbCd/one"
        page = FakePage(url=PLATFORM_PAGE)
        with mock.patch.object(runner.time, "sleep"), capture_console() as console:
            self.assertEqual(self.watch._recover_off_course(page), handlers.CONTINUE)

        self.assertEqual(len(page.goto_calls), 1)
        self.assertIn("/learn/demo/lecture/AbCd/one", page.goto_calls[0])
        self.assertIn("left the course", console.getvalue())

    def test_the_page_being_escaped_from_is_never_where_the_run_returns_to(self):
        """The stuck check moves ``last_url`` to whatever page is open.

        Which on the tick the tab drifts is the page the recovery is trying to
        leave: the run navigated to the sign-in page it was already on, called
        the arrival a return to the course, and repeated that until it gave up.
        """
        item = FakePage(url=COURSE_ITEM)
        drifted = FakePage(url=PLATFORM_PAGE)
        with (
            mock.patch.object(runner.Runner, "_detect_stuck", return_value=False),
            mock.patch.object(runner.Runner, "_sync_course_map", return_value=False),
            mock.patch.object(runner.interaction, "silence_media"),
            mock.patch.object(runner.page_ops, "is_locked_item", return_value=False),
            mock.patch.object(runner.modals, "dismiss_all"),
            mock.patch.object(runner.detection, "classify", return_value=detection.UNKNOWN),
            mock.patch.object(runner.time, "sleep"),
            capture_console(),
        ):
            self.watch._tick(item)

        # The stuck check is the real one here: it is what moves ``last_url``.
        with (
            mock.patch.object(runner.Runner, "_video_playing", return_value=False),
            mock.patch.object(runner.time, "sleep"),
            capture_console(),
        ):
            self.watch._detect_stuck(drifted)
            self.watch._recover_off_course(drifted)

        self.assertEqual(self.watch.last_url, "/my-learning")
        self.assertEqual(self.watch.last_course_url, "/learn/demo/lecture/AbCd/one")
        self.assertIn("/learn/demo/lecture/AbCd/one", drifted.goto_calls[0])
        self.assertNotIn("my-learning", drifted.goto_calls[0])

    def test_it_gives_up_rather_than_waiting_on_a_tab_that_will_not_return(self):
        page = FakePage(url=PLATFORM_PAGE)
        with mock.patch.object(runner.time, "sleep"), capture_console() as console:
            outcomes = [self.watch._recover_off_course(page) for _ in range(5)]

        self.assertEqual(outcomes[-1], runner.STOPPED)
        self.assertIn("pdex watch", console.getvalue())

    def test_the_warning_is_raised_once_not_once_per_pass(self):
        page = FakePage(url=PLATFORM_PAGE)
        with mock.patch.object(runner.time, "sleep"), capture_console() as console:
            for _ in range(3):
                self.watch._recover_off_course(page)

        self.assertEqual(console.getvalue().count("left the course"), 1)

    def test_a_tick_back_inside_the_course_clears_the_count(self):
        self.watch.off_course_ticks = 2
        self.watch.warned_off_course = True
        page = FakePage(url=COURSE_ITEM)
        with (
            mock.patch.object(runner.Runner, "_detect_stuck", return_value=False),
            mock.patch.object(runner.Runner, "_sync_course_map", return_value=False),
            mock.patch.object(runner.interaction, "silence_media"),
            mock.patch.object(runner.page_ops, "is_locked_item", return_value=False),
            mock.patch.object(runner.modals, "dismiss_all"),
            mock.patch.object(runner.detection, "classify", return_value=detection.UNKNOWN),
            mock.patch.object(runner.time, "sleep"),
        ):
            self.watch._tick(page)

        self.assertEqual(self.watch.off_course_ticks, 0)
        self.assertFalse(self.watch.warned_off_course)


class UnhandledPageTests(unittest.TestCase):
    """An unclassifiable page that will not move must not be retried hot."""

    def setUp(self):
        self.watch = runner.Runner(config.Settings())
        self.watch.unhandled_ticks["/learn/demo/lecture/AbCd/one"] = (
            runner.Runner.UNHANDLED_WAIT_LIMIT
        )

    def test_a_failed_advance_waits_before_the_next_pass(self):
        page = FakePage(url=COURSE_ITEM)
        with (
            mock.patch.object(runner.navigation, "advance", return_value="FAILED"),
            mock.patch.object(runner.time, "sleep") as slept,
            capture_console(),
        ):
            self.assertEqual(
                self.watch._step_past_unhandled(page, detection.UNKNOWN),
                handlers.CONTINUE,
            )

        slept.assert_called_once_with(self.watch.settings.idle_poll_seconds)

    def test_a_successful_advance_does_not_wait(self):
        page = FakePage(url=COURSE_ITEM)
        with (
            mock.patch.object(runner.navigation, "advance", return_value="NAVIGATED"),
            mock.patch.object(runner.time, "sleep") as slept,
            capture_console(),
        ):
            self.watch._step_past_unhandled(page, detection.UNKNOWN)

        slept.assert_not_called()


class StartupOutsideACourseTests(unittest.TestCase):
    """Attaching to a platform tab that holds no course says so and stops."""

    def _run_against(self, url):
        page = FakePage(url=url)
        session = mock.Mock()
        session.find_course_page.return_value = page
        session.__enter__ = mock.Mock(return_value=session)
        session.__exit__ = mock.Mock(return_value=False)
        watch = runner.Runner(config.Settings())
        with (
            mock.patch.object(runner, "BrowserSession", return_value=session),
            mock.patch.object(runner.Runner, "_tick", return_value=handlers.COURSE_COMPLETE),
            capture_console() as console,
        ):
            watch.run()
        return session, console.getvalue()

    def test_a_platform_tab_outside_a_course_ends_the_run_with_advice(self):
        session, output = self._run_against(PLATFORM_PAGE)
        self.assertIn("not inside a course", output)
        self.assertIn("pdex watch", output)
        session.reclaim.assert_not_called()

    def test_a_course_tab_starts_the_loop(self):
        session, _output = self._run_against(COURSE_ITEM)
        session.reclaim.assert_called()


class AdvanceToWorkTests(unittest.TestCase):
    """Leaving an item means going to the next thing to do, not the next row.

    The item after the one just finished is very often already complete, and
    walking into it costs a page load and a completion prompt to learn what the
    sidebar could have said from where the run already stood.
    """

    def setUp(self):
        self.watch = runner.Runner(config.Settings())
        self.watch.ctx.manager = MappedManager(
            {
                "Week 1": [("Intro", "VIDEO", FIRST, "5 min"), ("Notes", "READING", SECOND, "")],
                "Week 2": [("Wrap", "VIDEO", THIRD, "3 min")],
            }
        )
        self.page = FakePage(url=FIRST)

    def _advance(self, status, start_url=FIRST):
        with (
            mock.patch.object(runner, "get_completion_status", return_value=status),
            mock.patch.object(runner.time, "sleep"),
            capture_console() as console,
        ):
            outcome = self.watch._advance_to_work(self.page, start_url)
        self.console = console.getvalue()
        return outcome

    def _paths(self, *pairs):
        return {runner.urls.normalize_path(url): state for url, state in pairs}

    def test_the_finished_item_is_skipped_over_even_while_listed_unfinished(self):
        # The sidebar takes a moment to record a completion.
        outcome = self._advance(self._paths((FIRST, False), (SECOND, False)))
        self.assertEqual(outcome, handlers.CONTINUE)
        self.assertEqual(self.page.goto_calls, [SECOND])

    def test_completed_items_in_between_are_not_walked_through(self):
        outcome = self._advance(self._paths((FIRST, True), (SECOND, True), (THIRD, False)))
        self.assertEqual(outcome, handlers.CONTINUE)
        self.assertEqual(self.page.goto_calls, [THIRD])

    def test_the_type_of_the_item_being_opened_is_named(self):
        self._advance(self._paths((FIRST, True), (SECOND, False)))
        self.assertIn("Notes", self.console)
        self.assertIn("reading", self.console)

    def test_nothing_left_ends_the_run_rather_than_advancing(self):
        outcome = self._advance(self._paths((FIRST, True), (SECOND, True), (THIRD, True)))
        self.assertEqual(outcome, runner.STOPPED)
        self.assertEqual(self.page.goto_calls, [])

    def test_a_sidebar_that_still_lists_only_this_item_falls_back_to_next(self):
        # Not an ended course: the sidebar has not caught up. Returning None
        # hands the move back to the platform's Next button.
        outcome = self._advance(self._paths((FIRST, False), (SECOND, True), (THIRD, True)))
        self.assertIsNone(outcome)
        self.assertEqual(self.page.goto_calls, [])

    def test_a_failed_scan_falls_back_to_next(self):
        with mock.patch.object(
            runner, "get_completion_status", side_effect=RuntimeError("sidebar gone")
        ):
            self.assertIsNone(self.watch._advance_to_work(self.page, FIRST))
        self.assertEqual(self.page.goto_calls, [])

    def test_no_resume_leaves_the_platform_to_do_the_advancing(self):
        self.watch.settings = config.Settings(resume_at_incomplete=False)
        self.assertIsNone(self._advance(self._paths((FIRST, True), (THIRD, False))))
        self.assertEqual(self.page.goto_calls, [])

    def test_a_handler_reaches_this_through_the_context(self):
        # The seam itself: handlers do not know the runner, they know next_work.
        self.assertEqual(self.watch.ctx.next_work, self.watch._advance_to_work)

    def test_an_item_that_never_completes_is_not_offered_twice(self):
        # A discussion prompt is read and archived but never posted to, so the
        # sidebar lists it as work for the whole run. Observed live: the run
        # walked to it, off it onto a finished item, and straight back to it.
        listing = self._paths((FIRST, False), (SECOND, True), (THIRD, True))
        self.assertIsNone(self._advance(listing, start_url=FIRST))
        self.assertEqual(self.watch.attempted_items, [FIRST])

        outcome = self._advance(listing, start_url=SECOND)
        self.assertEqual(outcome, runner.STOPPED)
        self.assertEqual(self.page.goto_calls, [])

    def test_what_is_left_is_still_reported_in_full(self):
        # Excluded from the routing, not from the account of what remains, and
        # the run says why it is stopping on an item it did work.
        listing = self._paths((FIRST, False), (SECOND, True), (THIRD, True))
        self._advance(listing, start_url=FIRST)
        self._advance(listing, start_url=SECOND)
        self.assertIn("1 unfinished item", self.console)
        self.assertIn("worked and still listed as unfinished", self.console)

    def test_one_item_is_remembered_once(self):
        listing = self._paths((FIRST, False), (SECOND, True), (THIRD, True))
        for _ in range(3):
            self._advance(listing, start_url=FIRST)
        self.assertEqual(self.watch.attempted_items, [FIRST])
