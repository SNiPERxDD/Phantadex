"""Modal dismissal tests."""

import unittest
from unittest import mock

from phantadex import modals
from tests.fakes import FakeLocator, FakePage


class DismissTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(modals.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        # The stall report is deduplicated for the life of the process.
        modals._REPORTED_STALLS.clear()
        self.addCleanup(modals._REPORTED_STALLS.clear)

    def test_clicks_continue_on_honor_code(self):
        page = FakePage()
        button = FakeLocator(count=1, events=page.pointer_events)
        page.locators = {
            "h1, h2|Coursera Honor Code": FakeLocator(count=1),
            "button:has-text('Continue')": button,
        }
        self.assertEqual(modals.dismiss_all(page), 1)
        self.assertEqual(button.clicked, 1)
        self.assertEqual(page.pointer_events[:2], ["move", "click"])

    def test_the_demographics_survey_is_never_answered(self):
        # The survey asks about the user. Submitting it unattended answers for
        # them, so the run may only decline it.
        submit = FakeLocator(count=1)
        proceed = FakeLocator(count=1)
        page = FakePage(
            locators={
                "h1, h2|Demographics Survey": FakeLocator(count=1),
                "button:has-text('Submit')": submit,
                "button:has-text('Continue')": proceed,
            }
        )
        with mock.patch.object(modals.logs, "warn") as warn:
            self.assertEqual(modals.dismiss_all(page), 0)
        self.assertEqual(submit.clicked, 0)
        self.assertEqual(proceed.clicked, 0)
        warn.assert_called_once()

    def test_the_demographics_survey_is_declined_when_it_offers_a_way_out(self):
        skip = FakeLocator(count=1)
        page = FakePage(
            locators={
                "h1, h2|Demographics Survey": FakeLocator(count=1),
                "button:has-text('Skip')": skip,
            }
        )
        self.assertEqual(modals.dismiss_all(page), 1)
        self.assertEqual(skip.clicked, 1)

    def test_an_icon_only_close_control_is_found_by_its_label(self):
        close = FakeLocator(count=1)
        page = FakePage(
            locators={
                "h1, h2|Demographics Survey": FakeLocator(count=1),
                "button[aria-label='Close']": close,
            }
        )
        self.assertEqual(modals.dismiss_all(page), 1)
        self.assertEqual(close.clicked, 1)

    def test_an_unclearable_modal_is_reported_once(self):
        page = FakePage(locators={"h1, h2|Demographics Survey": FakeLocator(count=1)})
        with mock.patch.object(modals.logs, "warn") as warn:
            modals.dismiss_all(page)
            modals.dismiss_all(page)
        self.assertEqual(warn.call_count, 1)

    def test_no_modal_is_a_noop(self):
        self.assertEqual(modals.dismiss_all(FakePage()), 0)

    def test_one_failing_rule_does_not_block_the_others(self):
        # Regression: all rules shared one try/except, so a strict-mode error on
        # the first check silently skipped Honor Code *and* Reflect handling.
        reflect_button = FakeLocator(count=1)
        reflect_dialog = FakeLocator(
            count=1,
            children={
                "h1, h2, h3|Reflect": FakeLocator(count=1),
                "button:has-text('Continue')": reflect_button,
            },
        )

        page = FakePage(
            locators={
                "h1, h2|Coursera Honor Code": FakeLocator(count=1),
                f"{modals.IN_VIDEO_SCOPE}|Reflect": reflect_dialog,
            }
        )
        original_locator = page.locator

        def flaky_locator(selector, has_text=None):
            if has_text == "Coursera Honor Code":
                raise RuntimeError("strict mode violation")
            return original_locator(selector, has_text)

        page.locator = flaky_locator
        modals.dismiss_all(page)
        self.assertGreaterEqual(reflect_button.clicked, 1)

    def test_skips_the_in_video_question_dialog(self):
        # Regression: no rule named "Question", and `has_text` is a substring
        # match, so the "Poll" rule never matched it -- the dialog sat open and
        # its disabled Submit meant nothing else could clear it either.
        skip = FakeLocator(count=1)
        dialog = FakeLocator(
            count=1,
            children={"h2, h3|Question": FakeLocator(count=1), "button:has-text('Skip')": skip},
        )
        page = FakePage(locators={f"{modals.IN_VIDEO_SCOPE}|Question": dialog})
        self.assertEqual(modals.dismiss_all(page), 1)
        self.assertEqual(skip.clicked, 1)

    def test_question_heading_outside_the_player_is_ignored(self):
        # A graded quiz page renders "Question 1" headings and a "Skip" control.
        # The rule is scoped to the player precisely so it cannot fire there.
        skip = FakeLocator(count=1)
        page = FakePage(
            locators={"h2, h3|Question": FakeLocator(count=1), "button:has-text('Skip')": skip}
        )
        self.assertEqual(modals.dismiss_all(page), 0)
        self.assertEqual(skip.clicked, 0)

    def test_a_page_heading_containing_poll_is_ignored(self):
        # "Polling the market" is an ordinary reading heading; `has_text` is a
        # substring match, so an unscoped rule treated it as an open poll.
        skip = FakeLocator(count=1)
        page = FakePage(
            locators={"h2, h3|Poll": FakeLocator(count=1), "button:has-text('Skip')": skip}
        )
        self.assertEqual(modals.dismiss_all(page), 0)
        self.assertEqual(skip.clicked, 0)

    def test_an_interrupt_without_the_dialog_wrapper_is_still_reachable(self):
        # The scope covers the player container as well as the dialog role, so
        # an interrupt rendered without `role="dialog"` is not stranded.
        skip = FakeLocator(count=1)
        player = FakeLocator(
            count=1,
            children={"h2, h3|Poll": FakeLocator(count=1), "button:has-text('Skip')": skip},
        )
        page = FakePage(locators={f"{modals.IN_VIDEO_SCOPE}|Poll": player})
        self.assertEqual(modals.dismiss_all(page), 1)
        self.assertEqual(skip.clicked, 1)

    def test_dismisses_the_daily_goal_dialog(self):
        # It renders over the item and covers "Go to next item", so an advance
        # click lands on the overlay and the run cannot leave the page.
        button = FakeLocator(count=1)
        dialog = FakeLocator(
            count=1,
            children={
                "h1, h2, h3|completed today's goals": FakeLocator(count=1),
                "button:has-text('Continue learning')": button,
            },
        )
        page = FakePage(locators={f"{modals.DIALOG_SELECTOR}|completed today's goals": dialog})
        self.assertEqual(modals.dismiss_all(page), 1)
        self.assertEqual(button.clicked, 1)

    def test_the_sidebar_goals_panel_is_not_mistaken_for_the_dialog(self):
        # The item sidebar carries a "Today's goals" panel. The rule is confined
        # to the dialog so a page-wide text match cannot fire against it.
        button = FakeLocator(count=1)
        page = FakePage(
            locators={
                "h1, h2, h3|completed today's goals": FakeLocator(count=1),
                "button:has-text('Continue learning')": button,
            }
        )
        self.assertEqual(modals.dismiss_all(page), 0)
        self.assertEqual(button.clicked, 0)

    def test_message_is_logged_only_after_a_button_is_clicked(self):
        # The announcement used to precede the click, so the log read as a
        # dismissal even when no button matched.
        page = FakePage(locators={"h1, h2|Coursera Honor Code": FakeLocator(count=1)})
        with mock.patch.object(modals.logs, "step") as step:
            self.assertEqual(modals.dismiss_all(page), 0)
        step.assert_not_called()


if __name__ == "__main__":
    unittest.main()
