"""Filename sanitation and versioned-write tests."""

import os
import tempfile
import unittest

from phantadex import storage


class SanitizeFilenameTests(unittest.TestCase):
    def test_removes_path_separators(self):
        self.assertEqual(storage.sanitize_filename("a/b\\c"), "abc")

    def test_removes_reserved_characters(self):
        self.assertEqual(storage.sanitize_filename('a:b*c?d"e<f>g|h'), "abcdefgh")

    def test_collapses_whitespace(self):
        self.assertEqual(storage.sanitize_filename("Week 1  Intro"), "Week_1_Intro")

    def test_truncates(self):
        self.assertEqual(len(storage.sanitize_filename("x" * 500, max_length=50)), 50)

    def test_never_returns_empty(self):
        self.assertEqual(storage.sanitize_filename("///"), "Untitled")
        self.assertEqual(storage.sanitize_filename(""), "Untitled")


class SaveVersionedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "item.txt")

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_new_file(self):
        self.assertEqual(storage.save_versioned(self.path, "hello"), self.path)
        with open(self.path) as handle:
            self.assertEqual(handle.read(), "hello")

    def test_identical_content_is_deduplicated(self):
        storage.save_versioned(self.path, "hello world")
        self.assertEqual(storage.save_versioned(self.path, "hello world"), self.path)
        self.assertEqual(len(os.listdir(self._tmp.name)), 1)

    def test_distinct_content_is_versioned_not_overwritten(self):
        storage.save_versioned(self.path, "alpha " * 100)
        second = storage.save_versioned(self.path, "totally different beta " * 100)
        self.assertNotEqual(second, self.path)
        self.assertTrue(second.endswith("_v2.txt"))
        with open(self.path) as handle:
            self.assertIn("alpha", handle.read())

    def test_repeat_of_a_version_does_not_grow(self):
        storage.save_versioned(self.path, "alpha " * 100)
        changed = "totally different beta " * 100
        storage.save_versioned(self.path, changed)
        storage.save_versioned(self.path, changed)
        self.assertEqual(len(os.listdir(self._tmp.name)), 2)

    def test_windows_device_names_are_renamed(self):
        self.assertEqual(storage.sanitize_filename("CON"), "CON_")
        self.assertEqual(storage.sanitize_filename("nul.txt"), "nul.txt_")
        self.assertEqual(storage.sanitize_filename("COM1"), "COM1_")

    def test_ordinary_names_keep_their_spelling(self):
        self.assertEqual(storage.sanitize_filename("Console Basics"), "Console_Basics")
        self.assertEqual(storage.sanitize_filename("Auxiliary"), "Auxiliary")

    def test_creates_missing_parent_directories(self):
        nested = os.path.join(self._tmp.name, "a", "b", "item.txt")
        storage.save_versioned(nested, "content")
        self.assertTrue(os.path.exists(nested))


if __name__ == "__main__":
    unittest.main()
