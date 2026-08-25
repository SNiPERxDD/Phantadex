"""Tests for the split between the run's remaining work and the user's."""

import unittest

from phantadex import progress

COURSE = "/learn/demo"
VIDEO = ("Intro", "VIDEO", f"{COURSE}/lecture/aaa/intro", "3 min")
READING = ("Notes", "READING", f"{COURSE}/supplement/bbb/notes", "5 min")
QUIZ = ("Week 1 Quiz", "QUIZ", f"{COURSE}/quiz/ccc/week-1", "")
PEER = ("Case study", "PEER_REVIEW", f"{COURSE}/peer/ddd/case", "")
SURVEY = ("About you", "FILLER", f"{COURSE}/ungradedWidget/eee/survey", "")
DISCUSSION = ("Say hello", "DISCUSSION", f"{COURSE}/discussionPrompt/fff/hello", "")


def status(*pairs):
    return {row[2]: state for row, state in pairs}


class OwnerTests(unittest.TestCase):
    """Which side of the count an unfinished row falls on."""

    def test_content_the_run_works_is_the_run_s(self):
        self.assertEqual(progress.owner("VIDEO"), "ours")
        self.assertEqual(progress.owner("READING"), "ours")

    def test_graded_work_is_the_user_s(self):
        for label in ("QUIZ", "ASSIGNMENT", "PEER_REVIEW", "REVIEW_PEERS"):
            self.assertEqual(progress.owner(label), "yours", label)

    def test_a_survey_is_the_user_s_too(self):
        # The run neither answers a survey nor archives it, so it will never be
        # the reason a course is still open as far as the run is concerned.
        self.assertEqual(progress.owner("FILLER"), "yours")

    def test_a_discussion_changes_hands_when_the_ledger_holds_it(self):
        self.assertEqual(progress.owner("DISCUSSION"), "ours")
        self.assertEqual(progress.owner("DISCUSSION", archived=True), "yours")


class SplitTests(unittest.TestCase):
    """The counts themselves."""

    def test_every_row_lands_in_exactly_one_bucket(self):
        rows = [VIDEO, READING, QUIZ, PEER, SURVEY]
        counts = progress.split(
            rows,
            status(
                (VIDEO, True),
                (READING, False),
                (QUIZ, False),
                (PEER, False),
                (SURVEY, False),
            ),
        )
        self.assertEqual(counts, progress.Split(5, 1, 1, 3, 0))
        self.assertEqual(counts.complete + counts.ours + counts.yours + counts.unknown, 5)

    def test_a_row_the_sidebar_says_nothing_about_is_counted_as_unreadable(self):
        counts = progress.split([VIDEO, READING], status((VIDEO, True)))
        self.assertEqual(counts, progress.Split(2, 1, 0, 0, 1))

    def test_the_ledger_moves_a_discussion_from_one_side_to_the_other(self):
        rows = [DISCUSSION]
        unheld = progress.split(rows, status((DISCUSSION, False)))
        held = progress.split(rows, status((DISCUSSION, False)), lambda href: True)
        self.assertEqual((unheld.ours, unheld.yours), (1, 0))
        self.assertEqual((held.ours, held.yours), (0, 1))

    def test_a_ledger_that_cannot_be_read_leaves_the_row_as_the_run_s(self):
        def broken(href):
            raise RuntimeError("ledger unreadable")

        counts = progress.split([DISCUSSION], status((DISCUSSION, False)), broken)
        self.assertEqual((counts.ours, counts.yours), (1, 0))


class SummaryTests(unittest.TestCase):
    """The line a person actually reads."""

    def test_both_sides_are_named(self):
        line = progress.summary(progress.Split(89, 25, 35, 29, 0))
        self.assertEqual(line, "89 items · 25 complete · 35 left to Phantadex · 29 left to you")

    def test_having_nothing_left_to_do_is_said_rather_than_left_out(self):
        line = progress.summary(progress.Split(10, 10, 0, 0, 0))
        self.assertEqual(line, "10 items · 10 complete · nothing left to Phantadex")

    def test_nothing_left_to_the_run_still_reports_what_is_left_to_you(self):
        line = progress.summary(progress.Split(89, 60, 0, 29, 0))
        self.assertEqual(
            line, "89 items · 60 complete · nothing left to Phantadex · 29 left to you"
        )

    def test_an_empty_user_side_is_left_out(self):
        self.assertNotIn("you", progress.summary(progress.Split(10, 8, 2, 0, 0)))

    def test_unreadable_rows_are_declared_so_the_parts_add_up(self):
        line = progress.summary(progress.Split(4, 1, 1, 1, 1))
        self.assertIn("1 unreadable", line)


class TagTests(unittest.TestCase):
    """The shortened form a module header carries."""

    def test_a_finished_module_says_only_how_much_of_it_is_done(self):
        self.assertEqual(progress.tag(progress.Split(7, 7, 0, 0, 0)), "7/7 complete")

    def test_an_unfinished_module_names_the_run_s_share_even_when_it_is_none(self):
        self.assertEqual(
            progress.tag(progress.Split(29, 20, 0, 9, 0)), "20/29 complete · 0 pdex · 9 you"
        )

    def test_both_shares_are_carried_when_both_have_rows(self):
        self.assertEqual(
            progress.tag(progress.Split(29, 20, 4, 5, 0)), "20/29 complete · 4 pdex · 5 you"
        )

    def test_unreadable_rows_keep_the_header_adding_up(self):
        self.assertEqual(progress.tag(progress.Split(3, 1, 1, 0, 1)), "1/3 complete · 1 pdex · 1 ?")
