"""What the bulk archiver visits, and what it reports as already done."""

import unittest

from phantadex import archive

COURSE = "/learn/c"
VIDEO = f"{COURSE}/lecture/aaa/intro"
READING = f"{COURSE}/supplement/bbb/notes"
DISCUSSION = f"{COURSE}/discussionPrompt/ccc/say-hello"

COURSE_MAP = {
    "Week 1": [
        ("Intro", "VIDEO", VIDEO, "5 min"),
        ("Say hello", "DISCUSSION", DISCUSSION, ""),
    ],
    "Week 2": [("Notes", "READING", READING, "")],
}


class CollectTargetsTests(unittest.TestCase):
    def test_only_videos_and_readings_are_visited(self):
        types = [
            item_type for _module, _title, item_type, _url in archive.collect_targets(COURSE_MAP)
        ]
        self.assertEqual(types, ["VIDEO", "READING"])

    def test_each_target_carries_the_module_it_belongs_to(self):
        # The module travels with the item so the run can be read the way the
        # course is laid out rather than as one flat list of titles.
        modules = [module for module, _title, _type, _url in archive.collect_targets(COURSE_MAP)]
        self.assertEqual(modules, ["Week 1", "Week 2"])

    def test_course_order_is_preserved(self):
        titles = [title for _module, title, _type, _url in archive.collect_targets(COURSE_MAP)]
        self.assertEqual(titles, ["Intro", "Notes"])


class AlreadyDoneTests(unittest.TestCase):
    """The count of finished work is counted against the work there is."""

    def test_a_ledger_entry_that_is_not_a_target_is_not_counted(self):
        # The failure this exists for: a run archives discussion prompts, which
        # this command never visits, so the ledger held 63 items against 62
        # targets and the line read "62 archivable items (63 already done)".
        targets = archive.collect_targets(COURSE_MAP)
        done = archive.already_done(targets, {VIDEO, READING, DISCUSSION})
        self.assertEqual(len(done), 2)
        self.assertLessEqual(len(done), len(targets))

    def test_an_unarchived_target_is_not_counted(self):
        targets = archive.collect_targets(COURSE_MAP)
        self.assertEqual(archive.already_done(targets, {VIDEO}), {VIDEO})

    def test_an_empty_ledger_leaves_everything_to_do(self):
        self.assertEqual(archive.already_done(archive.collect_targets(COURSE_MAP), set()), set())

    def test_paths_are_matched_by_item_identity_not_by_string(self):
        # The ledger stores paths; the map may carry absolute URLs of the same
        # items, and the two spellings must not read as different items.
        targets = archive.collect_targets(COURSE_MAP)
        absolute = f"https://www.coursera.org{VIDEO}"
        self.assertEqual(archive.already_done(targets, {absolute}), {VIDEO})


if __name__ == "__main__":
    unittest.main()
