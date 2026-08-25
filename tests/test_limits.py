"""Bounds on how far a run goes, and on how long it may get nowhere."""

import unittest

from phantadex import limits
from tests.fakes import capture_console


class BudgetDisabledTests(unittest.TestCase):
    """Without a limit, nothing is counted and nothing is refused."""

    def test_no_limit_admits_everything(self):
        budget = limits.Budget()
        self.assertFalse(budget.enabled)
        for index in range(50):
            self.assertTrue(budget.admits(f"/item/{index}", "Module 1"))

    def test_no_limit_prints_nothing_on_validation(self):
        with capture_console() as output:
            limits.Budget().check_against(4, 40)
        self.assertEqual(output.getvalue(), "")


class ItemBudgetTests(unittest.TestCase):
    def test_the_run_stops_after_the_named_number_of_items(self):
        budget = limits.Budget(items=3)
        for index in range(3):
            self.assertTrue(budget.admits(f"/item/{index}", "Module 1"))
        self.assertFalse(budget.admits("/item/3", "Module 1"))

    def test_returning_to_a_counted_item_is_not_charged_twice(self):
        budget = limits.Budget(items=2)
        budget.admits("/item/0", "Module 1")
        # The loop turns several times on one item: a modal, a retry, a reload.
        for _ in range(5):
            self.assertTrue(budget.admits("/item/0", "Module 1"))
        self.assertTrue(budget.admits("/item/1", "Module 1"))
        self.assertFalse(budget.admits("/item/2", "Module 1"))

    def test_a_limit_larger_than_the_course_is_reported_not_refused(self):
        budget = limits.Budget(items=99)
        with capture_console() as output:
            budget.check_against(4, 40)
        self.assertIn("99", output.getvalue())
        self.assertIn("40", output.getvalue())
        self.assertTrue(budget.admits("/item/0", "Module 1"))


class ModuleBudgetTests(unittest.TestCase):
    def test_items_inside_the_allowed_modules_all_pass(self):
        budget = limits.Budget(modules=2)
        self.assertTrue(budget.admits("/a", "Module 1"))
        self.assertTrue(budget.admits("/b", "Module 1"))
        self.assertTrue(budget.admits("/c", "Module 2"))
        self.assertTrue(budget.admits("/d", "Module 2"))

    def test_the_first_item_of_the_module_after_the_limit_is_refused(self):
        budget = limits.Budget(modules=1)
        self.assertTrue(budget.admits("/a", "Module 1"))
        self.assertFalse(budget.admits("/b", "Module 2"))

    def test_an_item_with_no_module_does_not_consume_one(self):
        # An unmapped page reports no module. Counting it would let a page the
        # map does not know about spend part of the budget.
        budget = limits.Budget(modules=1)
        self.assertTrue(budget.admits("/stray", ""))
        self.assertTrue(budget.admits("/a", "Module 1"))
        self.assertFalse(budget.admits("/b", "Module 2"))


class BudgetSummaryTests(unittest.TestCase):
    def test_the_summary_counts_what_was_actually_covered(self):
        budget = limits.Budget(modules=2)
        budget.admits("/a", "Module 1")
        budget.admits("/b", "Module 1")
        budget.admits("/c", "Module 2")
        self.assertEqual(budget.summary(), "3 items across 2 modules")

    def test_one_of_each_is_written_in_the_singular(self):
        budget = limits.Budget(items=1)
        budget.admits("/a", "Module 1")
        self.assertEqual(budget.summary(), "1 item across 1 module")


class StallGuardTests(unittest.TestCase):
    def test_reaching_new_items_never_trips_the_guard(self):
        guard = limits.StallGuard(limit=3)
        for index in range(100):
            self.assertTrue(guard.observe(f"/item/{index}"))

    def test_sitting_on_one_item_trips_the_guard_after_the_limit(self):
        guard = limits.StallGuard(limit=3)
        self.assertTrue(guard.observe("/a"))
        for _ in range(3):
            self.assertTrue(guard.observe("/a"))
        self.assertFalse(guard.observe("/a"))

    def test_a_cycle_between_two_visited_items_trips_it_too(self):
        # The failure this exists for: navigation succeeds every time, so
        # nothing looks stuck, but the pair of items simply repeats.
        guard = limits.StallGuard(limit=4)
        guard.observe("/a")
        guard.observe("/b")
        results = [guard.observe("/a" if index % 2 else "/b") for index in range(10)]
        self.assertIn(False, results)

    def test_reaching_somewhere_new_resets_the_count(self):
        guard = limits.StallGuard(limit=3)
        guard.observe("/a")
        for _ in range(3):
            guard.observe("/a")
        self.assertTrue(guard.observe("/b"))
        self.assertEqual(guard.idle_ticks, 0)


if __name__ == "__main__":
    unittest.main()
