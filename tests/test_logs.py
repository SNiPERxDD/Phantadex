"""Terminal presentation contracts."""

import io
import unittest
from unittest import mock

from phantadex import logs
from tests.fakes import capture_console


class ItemSpacingTests(unittest.TestCase):
    def test_compact_items_do_not_insert_blank_rows(self):
        with capture_console() as output:
            try:
                logs.item("One", "1/2", spaced=False)
                logs.item("Two", "2/2", spaced=False)
            except TypeError as exc:
                self.fail(f"compact item mode is missing: {exc}")

        self.assertEqual(output.getvalue().count("\n"), 2)
        self.assertNotIn("\n\n", output.getvalue())


class SpinnerTests(unittest.TestCase):
    def test_redirected_spinner_emits_one_static_step(self):
        spinner = getattr(logs, "spinner", None)
        self.assertIsNotNone(spinner, "spinner presentation is missing")

        with capture_console() as output, spinner("generating course map"):
            pass

        self.assertEqual(output.getvalue().count("generating course map"), 1)


class PendingStatusTests(unittest.TestCase):
    def test_pending_status_has_one_marker(self):
        with capture_console() as output:
            logs.pending("Next lesson")

        self.assertEqual(output.getvalue(), "  ○ Next lesson\n")


class InterruptionTests(unittest.TestCase):
    def test_interrupted_clears_progress_and_prints_one_line(self):
        interrupted = getattr(logs, "interrupted", None)
        self.assertIsNotNone(interrupted, "shared interruption output is missing")

        with capture_console() as output:
            interrupted()

        self.assertEqual(output.getvalue(), "Stopped by user.\n")


class ConsoleStreamTests(unittest.TestCase):
    """The status glyphs must survive a stream whose encoding cannot carry them."""

    def test_a_narrow_stream_is_widened_to_utf8(self):
        class NarrowStream:
            def __init__(self):
                self.encoding = "cp1252"
                self.errors = "strict"

            def reconfigure(self, encoding=None, errors=None):
                if encoding is not None:
                    self.encoding = encoding
                if errors is not None:
                    self.errors = errors

        stream = NarrowStream()
        with mock.patch.object(logs.sys, "stdout", stream):
            self.assertIs(logs.console_stream(), stream)

        self.assertEqual(stream.encoding, "utf-8")
        self.assertEqual(stream.errors, "backslashreplace")

    def test_a_stream_that_refuses_utf8_still_relaxes_its_errors(self):
        class StubbornStream:
            def __init__(self):
                self.errors = "strict"

            def reconfigure(self, encoding=None, errors=None):
                if encoding is not None:
                    raise ValueError("encoding cannot be changed")
                self.errors = errors

        stream = StubbornStream()
        with mock.patch.object(logs.sys, "stdout", stream):
            logs.console_stream()

        self.assertEqual(stream.errors, "backslashreplace")

    def test_a_stream_without_reconfigure_is_returned_untouched(self):
        stream = io.StringIO()
        with mock.patch.object(logs.sys, "stdout", stream):
            self.assertIs(logs.console_stream(), stream)


if __name__ == "__main__":
    unittest.main()
