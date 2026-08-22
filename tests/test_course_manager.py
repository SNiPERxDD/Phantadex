"""CourseManager ledger, filename and navigation tests."""

import os
import shutil
import sys
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import phantadex
from phantadex import course_manager as course_manager_module
from phantadex import storage
from phantadex.course_manager import CourseManager

COURSE_MAP = {
    "Module 1": [
        ("Welcome", "VIDEO", "/learn/demo/lecture/aaa/welcome", 5),
        ("Syllabus", "READING", "/learn/demo/supplement/bbb/syllabus", 3),
        ("Practice Quiz", "QUIZ", "/learn/demo/quiz/ccc/practice", 10),
    ],
    "Module 2": [
        ("Deep Dive", "VIDEO", "/learn/demo/lecture/ddd/deep-dive", 12),
    ],
}


class CourseManagerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.manager = CourseManager(COURSE_MAP, "Demo: Course/Name", root_dir=self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class SetupTests(CourseManagerTestCase):
    def test_course_name_is_sanitized(self):
        self.assertNotIn("/", self.manager.safe_course_name)
        self.assertNotIn(":", self.manager.safe_course_name)

    def test_ledger_created_with_archivable_items_only(self):
        self.assertTrue(os.path.exists(self.manager.xml_path))
        with open(self.manager.xml_path) as handle:
            ledger = handle.read()
        self.assertIn("/learn/demo/lecture/aaa/welcome", ledger)
        self.assertNotIn("/learn/demo/quiz/ccc/practice", ledger)

    def test_ledger_uses_the_course_slug(self):
        self.assertEqual(os.path.basename(self.manager.xml_path), "demo.pdex.xml")


class LedgerBannerTests(CourseManagerTestCase):
    """Every ledger says what wrote it and where that came from."""

    def _ledger(self):
        with open(self.manager.xml_path, encoding="utf-8") as handle:
            return handle.read()

    def test_the_ledger_names_the_tool_its_version_and_its_home(self):
        ledger = self._ledger()
        self.assertIn(f"Phantadex {phantadex.__version__}", ledger)
        self.assertIn(phantadex.REPOSITORY_URL, ledger)

    def test_the_banner_sits_above_the_root_element(self):
        # A generator note belongs in the prolog. Placed inside <course> it
        # would read as data, and anything walking the tree would meet it.
        ledger = self._ledger()
        self.assertLess(ledger.index("<!--"), ledger.index("<course"))
        self.assertTrue(ledger.startswith("<?xml"))

    def test_the_banner_leaves_the_ledger_parseable(self):
        tree = ET.parse(self.manager.xml_path)
        self.assertEqual(tree.getroot().tag, "course")
        self.assertTrue(tree.getroot().findall(".//item"))

    def test_a_rewritten_ledger_carries_exactly_one_banner(self):
        # ElementTree drops comments when it parses, so the banner is rebuilt
        # on each write. Appending it instead would stack one per save.
        self.manager.save_content("/learn/demo/lecture/aaa/welcome", "text", "Transcript")
        self.manager.save_content("/learn/demo/lecture/aaa/welcome", "text again", "Transcript")
        self.assertEqual(self._ledger().count(phantadex.REPOSITORY_URL), 1)

    def test_the_banner_carries_nothing_about_the_machine_or_the_account(self):
        # The file is the user's to share; the note identifies the tool, not them.
        banner = course_manager_module._ledger_banner()
        self.assertNotIn(str(Path.home()), banner)
        # Read from the environment rather than os.getlogin(), which raises
        # when the process has no controlling terminal, as on a CI runner.
        account = os.environ.get("USER") or os.environ.get("USERNAME")
        if account:
            self.assertNotIn(account, banner)

    def test_the_banner_is_a_well_formed_xml_comment(self):
        # "--" is forbidden inside a comment and a comment may not end with "-".
        body = course_manager_module._ledger_banner()
        self.assertTrue(body.startswith("<!--") and body.endswith("-->"))
        self.assertNotIn("--", body[4:-3])


class LegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.old_cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, self.old_cwd)
        self.new_root = "phantadex_archive"
        self.old_root = "coursera_transcripts"
        self.safe_course = storage.sanitize_filename("Demo: Course/Name")

    def _manager(self, root_dir=None):
        return CourseManager(
            COURSE_MAP,
            "Demo: Course/Name",
            root_dir=self.new_root if root_dir is None else root_dir,
        )

    def test_legacy_default_directory_and_ledger_are_migrated(self):
        old_course = os.path.join(self.old_root, self.safe_course)
        os.makedirs(old_course)
        old_ledger = os.path.join(old_course, "course_content.xml")
        with open(old_ledger, "w", encoding="utf-8") as handle:
            handle.write(
                "<?xml version='1.0'?><course name='Demo: Course/Name'>"
                "<module title='Module 1'><item title='Welcome' type='VIDEO' "
                "url='/learn/demo/lecture/aaa/welcome'><content>kept body</content>"
                "</item></module></course>"
            )

        manager = self._manager()

        self.assertFalse(os.path.exists(old_course))
        self.assertEqual(os.path.basename(manager.xml_path), "demo.pdex.xml")
        with open(manager.xml_path, encoding="utf-8") as handle:
            self.assertIn("kept body", handle.read())

    def test_existing_destination_is_never_overwritten(self):
        old_course = os.path.join(self.old_root, self.safe_course)
        new_course = os.path.join(self.new_root, self.safe_course)
        os.makedirs(old_course)
        os.makedirs(new_course)
        with open(os.path.join(old_course, "old.txt"), "w", encoding="utf-8") as handle:
            handle.write("old")
        with open(os.path.join(new_course, "new.txt"), "w", encoding="utf-8") as handle:
            handle.write("new")

        self._manager()

        self.assertTrue(os.path.exists(os.path.join(old_course, "old.txt")))
        with open(os.path.join(new_course, "new.txt"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "new")

    def test_custom_root_is_never_migrated(self):
        old_course = os.path.join(self.old_root, self.safe_course)
        os.makedirs(old_course)
        custom_root = os.path.join(self.tmp.name, "custom")

        manager = self._manager(custom_root)

        self.assertTrue(os.path.exists(old_course))
        self.assertTrue(manager.root_dir.startswith(custom_root))


class ResolveLocationTests(CourseManagerTestCase):
    def test_resolves_mapped_item(self):
        module, lesson, title, module_name = self.manager.resolve_location(
            "https://www.coursera.org/learn/demo/lecture/ddd/deep-dive?x=1"
        )
        self.assertEqual((module, lesson), (2, 1))
        self.assertEqual(title, "Deep_Dive")
        self.assertEqual(module_name, "Module 2")

    def test_unmapped_item_is_flagged(self):
        module, lesson, title, _ = self.manager.resolve_location("/learn/demo/lecture/zzz/other")
        self.assertEqual((module, lesson), (0, 0))
        self.assertEqual(title, "Unknown_Item")


class FilenameTests(CourseManagerTestCase):
    def test_mapped_filename(self):
        self.assertEqual(
            self.manager.filename_for("/learn/demo/lecture/aaa/welcome", "Transcript"),
            "M01_L01_Welcome_Transcript.txt",
        )

    def test_unmapped_items_get_distinct_filenames(self):
        # Regression: every unresolved item used to collapse onto
        # M00_L00_Unknown_Item_<type>.txt and silently overwrite the previous one.
        first = self.manager.filename_for("/learn/demo/lecture/zzz/one", "Transcript")
        second = self.manager.filename_for("/learn/demo/lecture/yyy/two", "Transcript")
        self.assertNotEqual(first, second)
        self.assertIn("zzz", first)
        self.assertIn("yyy", second)


class SaveContentTests(CourseManagerTestCase):
    def _stored_content(self, url):
        """Returns the ledger's copy of one item's content."""
        root = ET.parse(self.manager.xml_path).getroot()
        return root.find(f".//item[@url='{url}']").findtext("content")

    def test_an_oversized_reading_is_cut_short_in_the_ledger_only(self):
        # A supplement can embed a PDF viewer whose rendered text runs to book
        # length. The file on disk is the archive and keeps all of it; the
        # ledger is an index, so it keeps a bounded excerpt and a pointer.
        limit = course_manager_module.MAX_LEDGER_CONTENT_CHARS
        body = "\n".join(f"line {number}" for number in range(limit))
        filename, ledger_ok = self.manager.save_content(
            "/learn/demo/supplement/bbb/syllabus", body, "Reading"
        )
        self.assertTrue(ledger_ok)

        with open(os.path.join(self.manager.root_dir, filename)) as handle:
            self.assertEqual(handle.read(), body)

        stored = self._stored_content("/learn/demo/supplement/bbb/syllabus")
        self.assertLess(len(stored), limit + 200)
        self.assertTrue(stored.startswith("line 0\n"))
        self.assertIn(f"Truncated at {limit} characters of {len(body)}", stored)
        self.assertIn(filename, stored)

    def test_a_reading_inside_the_limit_is_stored_whole(self):
        body = "a" * course_manager_module.MAX_LEDGER_CONTENT_CHARS
        self.manager.save_content("/learn/demo/supplement/bbb/syllabus", body, "Reading")
        stored = self._stored_content("/learn/demo/supplement/bbb/syllabus")
        self.assertEqual(stored, body)

    def test_an_oversized_adopted_item_is_cut_short_too(self):
        limit = course_manager_module.MAX_LEDGER_CONTENT_CHARS
        body = "\n".join(f"line {number}" for number in range(limit))
        self.manager.save_content("/learn/demo/supplement/eee/unlisted", body, "Reading")
        adopted = (
            ET.parse(self.manager.xml_path)
            .getroot()
            .find(".//item[@url='/learn/demo/supplement/eee/unlisted']")
        )
        self.assertIn("Truncated at", adopted.findtext("content"))

    def test_a_cut_excerpt_ends_on_a_whole_line(self):
        limit = course_manager_module.MAX_LEDGER_CONTENT_CHARS
        body = ("word " * 40 + "\n") * (limit // 200)
        self.manager.save_content("/learn/demo/supplement/bbb/syllabus", body, "Reading")
        stored = self._stored_content("/learn/demo/supplement/bbb/syllabus")
        excerpt = stored.split("\n\n[Truncated")[0]
        self.assertTrue(body.startswith(excerpt))
        self.assertTrue(excerpt.endswith("word"))

    def test_writes_file_and_updates_ledger(self):
        filename, ledger_ok = self.manager.save_content(
            "https://www.coursera.org/learn/demo/lecture/aaa/welcome",
            "transcript body",
            "Transcript",
        )
        self.assertTrue(ledger_ok)
        self.assertTrue(os.path.exists(os.path.join(self.manager.root_dir, filename)))
        with open(self.manager.xml_path) as handle:
            self.assertIn("transcript body", handle.read())

    def test_unmapped_url_is_adopted_into_the_ledger(self):
        filename, ledger_ok = self.manager.save_content(
            "/learn/demo/lecture/zzz/other", "orphan body", "Transcript"
        )
        self.assertTrue(ledger_ok)
        self.assertTrue(os.path.exists(os.path.join(self.manager.root_dir, filename)))

        root = ET.parse(self.manager.xml_path).getroot()
        adopted = root.find(".//item[@url='/learn/demo/lecture/zzz/other']")
        self.assertIsNotNone(adopted)
        self.assertEqual(adopted.get("type"), "VIDEO")
        self.assertEqual(adopted.findtext("content"), "orphan body")
        module = root.find("module[@title='Unknown_Module']")
        self.assertIsNotNone(module)

    def test_an_item_the_map_calls_unarchivable_is_adopted_where_it_belongs(self):
        # The row scanned as FILLER, so the ledger never listed it; the live page
        # turned out to be a reading and was archived anyway.
        course_map = {
            "Module 1": [("Course Survey", "FILLER", "/learn/demo/supplement/eee/survey", 2)]
        }
        manager = CourseManager(course_map, "Demo", root_dir=self._tmp.name)
        _filename, ledger_ok = manager.save_content(
            "/learn/demo/supplement/eee/survey", "survey body", "Reading"
        )
        self.assertTrue(ledger_ok)

        root = ET.parse(manager.xml_path).getroot()
        adopted = root.find(".//item[@url='/learn/demo/supplement/eee/survey']")
        self.assertEqual(adopted.get("title"), "Course Survey")
        self.assertEqual(adopted.get("type"), "READING")
        self.assertEqual(
            root.find("module[@title='Module 1']").findall("item"),
            [adopted],
        )

    def test_adopting_an_item_does_not_duplicate_it_on_the_next_run(self):
        self.manager.save_content("/learn/demo/lecture/zzz/other", "orphan body", "Transcript")
        reopened = CourseManager(COURSE_MAP, "Demo: Course/Name", root_dir=self._tmp.name)
        root = ET.parse(reopened.xml_path).getroot()
        matches = root.findall(".//item[@url='/learn/demo/lecture/zzz/other']")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].findtext("content"), "orphan body")

    def test_two_unmapped_items_do_not_overwrite_each_other(self):
        first, _ = self.manager.save_content(
            "/learn/demo/lecture/zzz/one", "body one", "Transcript"
        )
        second, _ = self.manager.save_content(
            "/learn/demo/lecture/yyy/two", "body two", "Transcript"
        )
        self.assertNotEqual(first, second)
        with open(os.path.join(self.manager.root_dir, first)) as handle:
            self.assertEqual(handle.read(), "body one")
        with open(os.path.join(self.manager.root_dir, second)) as handle:
            self.assertEqual(handle.read(), "body two")

    def test_rewriting_distinct_content_preserves_original(self):
        self.manager.save_content("/learn/demo/lecture/aaa/welcome", "alpha " * 80, "Transcript")
        filename, _ = self.manager.save_content(
            "/learn/demo/lecture/aaa/welcome", "beta gamma delta " * 80, "Transcript"
        )
        self.assertTrue(filename.endswith("_v2.txt"))

    def test_save_is_guarded_by_the_course_lock(self):
        with mock.patch.object(
            self.manager, "_course_lock", wraps=self.manager._course_lock
        ) as course_lock:
            self.manager.save_content(
                "/learn/demo/lecture/aaa/welcome", "guarded body", "Transcript"
            )

        course_lock.assert_called_once_with()

    def test_ledger_replacement_is_atomic(self):
        with mock.patch("phantadex.course_manager.os.replace", wraps=os.replace) as replace:
            self.manager.save_content(
                "/learn/demo/lecture/aaa/welcome", "atomic body", "Transcript"
            )

        replace.assert_called()


class LedgerLockTests(CourseManagerTestCase):
    def test_a_second_manager_waits_for_the_course_lock(self):
        second = CourseManager(COURSE_MAP, "Demo: Course/Name", root_dir=self._tmp.name)
        acquired = threading.Event()

        def acquire_from_second_manager():
            with second._course_lock():
                acquired.set()

        with self.manager._course_lock():
            worker = threading.Thread(target=acquire_from_second_manager)
            worker.start()
            self.assertFalse(acquired.wait(0.1))

        worker.join(timeout=2)
        self.assertTrue(acquired.is_set())
        self.assertFalse(worker.is_alive())

    def test_windows_uses_a_one_byte_nonblocking_lock(self):
        calls = []
        fake_msvcrt = SimpleNamespace(
            LK_NBLCK=1,
            LK_UNLCK=2,
            locking=lambda descriptor, mode, size: calls.append((descriptor, mode, size)),
        )

        with (
            tempfile.TemporaryFile("w+b") as handle,
            mock.patch.object(course_manager_module.os, "name", "nt"),
            mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}),
        ):
            course_manager_module._lock_file(handle)
            course_manager_module._unlock_file(handle)

        self.assertEqual([mode for _descriptor, mode, _size in calls], [1, 2])
        self.assertTrue(all(size == 1 for _descriptor, _mode, size in calls))


class ArchivedStateTests(CourseManagerTestCase):
    def test_archived_paths_reflects_saved_content(self):
        self.assertEqual(self.manager.archived_paths(), set())
        self.manager.save_content("/learn/demo/lecture/aaa/welcome", "body", "Transcript")
        self.assertIn("/learn/demo/lecture/aaa/welcome", self.manager.archived_paths())

    def test_is_archived(self):
        self.assertFalse(self.manager.is_archived("/learn/demo/lecture/aaa/welcome"))
        self.manager.save_content("/learn/demo/lecture/aaa/welcome", "body text", "Transcript")
        self.assertTrue(self.manager.is_archived("/learn/demo/lecture/aaa/welcome"))


class NextUrlTests(CourseManagerTestCase):
    def test_advances_within_module(self):
        self.assertEqual(
            self.manager.get_next_url("/learn/demo/lecture/aaa/welcome"),
            "https://www.coursera.org/learn/demo/supplement/bbb/syllabus",
        )

    def test_crosses_module_boundary(self):
        self.assertEqual(
            self.manager.get_next_url("/learn/demo/quiz/ccc/practice"),
            "https://www.coursera.org/learn/demo/lecture/ddd/deep-dive",
        )

    def test_returns_none_at_end(self):
        self.assertIsNone(self.manager.get_next_url("/learn/demo/lecture/ddd/deep-dive"))

    def test_returns_none_for_unknown_url(self):
        self.assertIsNone(self.manager.get_next_url("/learn/demo/lecture/zzz/nope"))

    def test_tolerates_query_parameters(self):
        self.assertEqual(
            self.manager.get_next_url(
                "https://www.coursera.org/learn/demo/lecture/aaa/welcome?skip=1#t"
            ),
            "https://www.coursera.org/learn/demo/supplement/bbb/syllabus",
        )


class PreviousUrlTests(CourseManagerTestCase):
    def test_retreats_within_module(self):
        self.assertEqual(
            self.manager.get_previous_url("/learn/demo/supplement/bbb/syllabus"),
            "https://www.coursera.org/learn/demo/lecture/aaa/welcome",
        )

    def test_crosses_module_boundary(self):
        self.assertEqual(
            self.manager.get_previous_url("/learn/demo/lecture/ddd/deep-dive"),
            "https://www.coursera.org/learn/demo/quiz/ccc/practice",
        )

    def test_returns_none_at_start_or_for_unknown_url(self):
        self.assertIsNone(self.manager.get_previous_url("/learn/demo/lecture/aaa/welcome"))
        self.assertIsNone(self.manager.get_previous_url("/learn/demo/lecture/zzz/nope"))


if __name__ == "__main__":
    unittest.main()


class LedgerReconciliationTests(unittest.TestCase):
    """A ledger written from a partial sidebar must catch up on later runs."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def _manager(self, lessons):
        return CourseManager({"Module 1": lessons}, "Demo Course", root_dir=self.tmp)

    @staticmethod
    def _urls(manager):
        root = ET.parse(manager.xml_path).getroot()
        return [item.get("url") for item in root.findall(".//item")]

    def test_items_discovered_later_are_added(self):
        partial = [("One", "VIDEO", "/learn/demo/lecture/aaa/one", "1 min")]
        full = partial + [("Two", "READING", "/learn/demo/supplement/bbb/two", "2 min")]

        self._manager(partial)
        later = self._manager(full)

        self.assertEqual(
            self._urls(later),
            ["/learn/demo/lecture/aaa/one", "/learn/demo/supplement/bbb/two"],
        )

    def test_existing_content_survives_reconciliation(self):
        partial = [("One", "VIDEO", "/learn/demo/lecture/aaa/one", "1 min")]
        first = self._manager(partial)
        first.save_content("/learn/demo/lecture/aaa/one", "Archived body.", "Transcript")

        full = partial + [("Two", "READING", "/learn/demo/supplement/bbb/two", "2 min")]
        later = self._manager(full)

        root = ET.parse(later.xml_path).getroot()
        kept = root.find(".//item[@url='/learn/demo/lecture/aaa/one']")
        self.assertEqual(kept.findtext("content"), "Archived body.")

    def test_unchanged_map_adds_nothing(self):
        lessons = [("One", "VIDEO", "/learn/demo/lecture/aaa/one", "1 min")]
        self._manager(lessons)
        again = self._manager(lessons)
        self.assertEqual(self._urls(again), ["/learn/demo/lecture/aaa/one"])

    def test_non_archivable_types_stay_out_of_the_ledger(self):
        lessons = [
            ("One", "VIDEO", "/learn/demo/lecture/aaa/one", "1 min"),
            ("Pop", "QUIZ", "/learn/demo/quiz/ddd/pop", "5 min"),
        ]
        self.assertEqual(self._urls(self._manager(lessons)), ["/learn/demo/lecture/aaa/one"])

    def test_discussions_are_archivable(self):
        """DiscussionHandler writes archives, so the ledger must hold the item."""
        lessons = [("Chat", "DISCUSSION", "/learn/demo/discussionPrompt/ccc/chat", "5 min")]
        self.assertEqual(
            self._urls(self._manager(lessons)), ["/learn/demo/discussionPrompt/ccc/chat"]
        )
