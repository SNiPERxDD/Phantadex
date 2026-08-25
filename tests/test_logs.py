"""Terminal presentation contracts."""

import contextlib
import io
import logging
import os
import tempfile
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


class ProgressInterruptionTests(unittest.TestCase):
    """A log record arriving mid-animation must not weld itself to the bar."""

    def _paint_over_a_live_bar(self, emit):
        stream = io.StringIO()
        logger = logging.getLogger(logs.LOGGER_NAME)
        handler = logs.ConsoleHandler(stream)
        handler.setFormatter(logs.ConsoleFormatter())
        old_handlers, old_level = logger.handlers[:], logger.level
        old_paint = logs.paint.enabled
        logger.handlers = [handler]
        logger.setLevel(logging.DEBUG)
        logs.paint.enabled = True
        try:
            with contextlib.redirect_stdout(stream):
                logs.bar(0.5, "0:50 / 1:40")
                emit()
                logs.bar_done()
        finally:
            logs.paint.enabled = old_paint
            logger.handlers = old_handlers
            logger.setLevel(old_level)
        return logs.strip_ansi(stream.getvalue())

    def test_the_bar_is_erased_before_the_record_and_put_back_after(self):
        output = self._paint_over_a_live_bar(lambda: logs.warn("poll skipped"))
        before, _, after = output.partition("poll skipped")

        # Blanked: the bar's line is overwritten with spaces before the record
        # lands, so the two never share a terminal line.
        self.assertRegex(before, r"\r {10,}\r")
        # And repainted, so the animation survives the interruption rather than
        # being cut off at whatever frame it had reached.
        self.assertIn("50.0%", after)

    def test_the_bar_is_not_left_on_screen_once_it_is_done(self):
        output = self._paint_over_a_live_bar(lambda: None)
        self.assertTrue(output.rstrip(" ").endswith("\r"))
        self.assertEqual(logs._transient, "")

    def test_a_debug_record_interrupts_it_the_same_way(self):
        # Any record collides with the bar, not just the modal dismissals that
        # made it visible; -v raises the rate rather than changing the problem.
        output = self._paint_over_a_live_bar(
            lambda: logs.get_logger("test").debug("clicked something")
        )
        self.assertIn("clicked something", output)
        self.assertIn("50.0%", output.partition("clicked something")[2])


class RunLogTests(unittest.TestCase):
    """Every run leaves a full-detail record behind, whatever the console showed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patched = mock.patch.object(logs, "run_log_dir", return_value=self._tmp.name)
        patched.start()
        self.addCleanup(patched.stop)
        self.logger = logging.getLogger(logs.LOGGER_NAME)
        self._old_handlers = self.logger.handlers[:]
        self.addCleanup(self._restore)

    def _restore(self):
        for handler in self.logger.handlers:
            if handler not in self._old_handlers:
                handler.close()
        self.logger.handlers = self._old_handlers

    def test_a_run_log_is_written_and_named_for_the_command(self):
        path = logs.start_run_log("watch")
        self.assertIsNotNone(path)
        self.assertTrue(os.path.basename(path).startswith("watch-"))
        self.assertTrue(path.endswith(".log"))

    def test_it_records_detail_the_console_was_never_asked_for(self):
        # A quiet run still leaves a debug trace; that is the point of the file.
        logs.setup("WARNING")
        path = logs.start_run_log("watch")
        logs.get_logger("test").debug("selector fell back to the generic one")
        for handler in self.logger.handlers:
            handler.flush()

        with open(path, encoding="utf-8") as handle:
            contents = handle.read()
        self.assertIn("selector fell back to the generic one", contents)
        self.assertIn("DEBUG", contents)

    def test_colour_codes_do_not_reach_the_file(self):
        path = logs.start_run_log("watch")
        old_paint = logs.paint.enabled
        logs.paint.enabled = True
        try:
            logs.ok("archived")
        finally:
            logs.paint.enabled = old_paint
        for handler in self.logger.handlers:
            handler.flush()

        with open(path, encoding="utf-8") as handle:
            contents = handle.read()
        self.assertIn("archived", contents)
        self.assertNotIn("\x1b[", contents)

    def test_a_second_run_log_replaces_the_first_rather_than_doubling_it(self):
        # The package logger outlives one run inside a long-lived process. When
        # the old handler stayed attached, every record after the second call
        # was written to both files, and a suite that starts many runs left
        # logs with each line repeated once per run.
        # Two different commands, so the two runs cannot land on one file name.
        first = logs.start_run_log("dex")
        second = logs.start_run_log("watch")
        logs.get_logger("test").info("only once")
        for handler in self.logger.handlers:
            handler.flush()

        installed = [h for h in self.logger.handlers if isinstance(h, logs.RunLogHandler)]
        self.assertEqual(len(installed), 1)
        with open(second, encoding="utf-8") as handle:
            self.assertEqual(handle.read().count("only once"), 1)
        with open(first, encoding="utf-8") as handle:
            self.assertNotIn("only once", handle.read())

    def test_closing_the_run_log_leaves_the_console_handler_alone(self):
        logs.setup("INFO")
        before = len(self.logger.handlers)
        logs.start_run_log("watch")
        logs.close_run_log()

        self.assertEqual(len(self.logger.handlers), before)
        self.assertFalse(any(isinstance(h, logs.RunLogHandler) for h in self.logger.handlers))

    def test_old_run_logs_are_pruned_so_the_directory_stays_bounded(self):
        for index in range(6):
            with open(os.path.join(self._tmp.name, f"watch-2026010{index}-000000.log"), "w"):
                pass

        logs._prune_run_logs(self._tmp.name, keep=3)

        remaining = sorted(name for name in os.listdir(self._tmp.name) if name.endswith(".log"))
        self.assertEqual(len(remaining), 3)
        # The newest survive: the file names sort in the order they were written.
        self.assertEqual(remaining[-1], "watch-20260105-000000.log")

    def test_a_directory_that_cannot_be_opened_does_not_end_the_run(self):
        with (
            mock.patch.object(logs.os, "makedirs", side_effect=OSError("read-only")),
            capture_console() as console,
        ):
            self.assertIsNone(logs.start_run_log("watch"))
        self.assertIn("run log", console.getvalue().lower())


if __name__ == "__main__":
    unittest.main()


class ItemTypeVocabularyTests(unittest.TestCase):
    """One spelling and one colour per item type, wherever it is printed."""

    def test_a_label_reads_as_words_not_as_a_constant(self):
        self.assertEqual(logs.type_name("PEER_REVIEW"), "peer review")
        self.assertEqual(logs.type_name("VIDEO"), "video")

    def test_the_maps_own_name_for_a_survey_is_translated(self):
        # "FILLER" says nothing to the person reading the line.
        self.assertEqual(logs.type_name("FILLER"), "survey")

    def test_an_unknown_label_is_still_legible(self):
        self.assertEqual(logs.type_name("SOME_NEW_THING"), "some new thing")

    def test_the_two_halves_of_a_peer_assignment_read_alike(self):
        self.assertEqual(logs.type_name("REVIEW_PEERS"), logs.type_name("PEER_REVIEW"))

    def test_a_colourless_terminal_gets_the_bare_words(self):
        previous = logs.paint.enabled
        logs.paint.enabled = False
        self.addCleanup(setattr, logs.paint, "enabled", previous)
        self.assertEqual(logs.type_tag("VIDEO"), "video")

    def test_each_kind_of_item_is_coloured_apart_from_the_others(self):
        previous = logs.paint.enabled
        logs.paint.enabled = True
        self.addCleanup(setattr, logs.paint, "enabled", previous)
        tags = {logs.type_tag(label) for label in ("VIDEO", "READING", "QUIZ", "DISCUSSION")}
        self.assertEqual(len(tags), 4)
        self.assertTrue(all(tag.startswith("\033[") for tag in tags))

    def test_a_type_with_no_colour_of_its_own_recedes(self):
        previous = logs.paint.enabled
        logs.paint.enabled = True
        self.addCleanup(setattr, logs.paint, "enabled", previous)
        self.assertEqual(logs.type_tag("SURVEY"), logs.paint.dim("survey"))
