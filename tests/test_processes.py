"""Tests for the process scan behind ``pdex stop``."""

import os
import signal
import unittest
from unittest import mock

from phantadex import processes


class CommandRecognitionTests(unittest.TestCase):
    def test_recognises_the_console_script(self):
        self.assertTrue(processes.is_phantadex_command("/usr/local/bin/python /usr/bin/pdex watch"))

    def test_recognises_a_bare_entry_point(self):
        self.assertTrue(processes.is_phantadex_command("phantadex discover"))

    def test_recognises_module_invocation(self):
        self.assertTrue(processes.is_phantadex_command("python -m phantadex.watch"))
        self.assertTrue(processes.is_phantadex_command("python -m phantadex"))

    def test_recognises_the_compatibility_scripts(self):
        self.assertTrue(processes.is_phantadex_command("python3 phantadex_watch.py"))

    def test_a_mention_in_an_argument_is_not_a_run(self):
        # Killing the user's search because it named the entry point would be
        # worse than missing a run of it.
        self.assertFalse(processes.is_phantadex_command("grep -rn pdex src"))
        self.assertFalse(processes.is_phantadex_command("vim notes-about-phantadex"))

    def test_an_unrelated_module_is_not_a_run(self):
        self.assertFalse(processes.is_phantadex_command("python -m phantadexter"))

    def test_empty_input_is_not_a_run(self):
        self.assertFalse(processes.is_phantadex_command(""))
        self.assertFalse(processes.is_phantadex_command(None))


class FindRunsTests(unittest.TestCase):
    def test_lists_only_phantadex_processes(self):
        rows = [
            (10, 1, "/bin/python /bin/pdex watch"),
            (11, 1, "/usr/bin/firefox"),
            (12, 1, "python -m phantadex.discovery"),
        ]

        self.assertEqual(
            processes.find_runs(rows, pid=999),
            [(10, "/bin/python /bin/pdex watch"), (12, "python -m phantadex.discovery")],
        )

    def test_never_targets_itself_or_its_own_ancestors(self):
        # ``pdex stop`` is itself a Phantadex process, and so is a `pdex` shell
        # that spawned it; stopping either would kill the stop command midway.
        rows = [
            (10, 1, "/bin/python /bin/pdex watch"),
            (20, 30, "/bin/python /bin/pdex stop"),
            (30, 1, "/bin/python /bin/phantadex"),
        ]

        self.assertEqual(processes.find_runs(rows, pid=20), [(10, rows[0][2])])

    def test_scans_the_live_table_by_default(self):
        with mock.patch.object(processes, "process_table", return_value=[]) as table:
            self.assertEqual(processes.find_runs(), [])
        table.assert_called_once_with()


class ProcessTableTests(unittest.TestCase):
    def _run(self, stdout):
        completed = mock.Mock(stdout=stdout)
        with mock.patch.object(processes.subprocess, "run", return_value=completed):
            return processes.process_table()

    def test_parses_the_listing(self):
        self.assertEqual(
            self._run("  10     1 /bin/python /bin/pdex watch\n 11 10 sleep 5\n"),
            [(10, 1, "/bin/python /bin/pdex watch"), (11, 10, "sleep 5")],
        )

    def test_drops_rows_it_cannot_read(self):
        self.assertEqual(self._run("PID PPID COMMAND\nnot a row\n 12 1 pdex\n"), [(12, 1, "pdex")])

    def test_a_failed_listing_is_not_fatal(self):
        with mock.patch.object(processes.subprocess, "run", side_effect=OSError("no ps")):
            self.assertEqual(processes.process_table(), [])


class TerminateTests(unittest.TestCase):
    """Signal escalation, with liveness stubbed so the platform does not matter."""

    def setUp(self):
        self.signals = []
        self.alive = set()
        # Stands in for the escalation signal, which is SIGTERM again on
        # Windows: a distinct number is what makes the two steps tellable
        # apart in the assertions below on either platform.
        self.sigkill = signal.SIGINT

    def _kill(self, pid, number):
        """Stands in for ``os.kill``: SIGTERM is obeyed, anything else recorded."""
        self.signals.append((pid, number))
        if pid not in self.alive:
            raise ProcessLookupError(pid)
        if number == signal.SIGTERM:
            self.alive.discard(pid)

    def _terminate(self, pids, kill=None, **kwargs):
        with (
            mock.patch.object(processes, "KILL_SIGNAL", self.sigkill),
            mock.patch.object(processes.os, "kill", side_effect=kill or self._kill),
            mock.patch.object(processes, "is_alive", side_effect=lambda pid: pid in self.alive),
        ):
            return processes.terminate(pids, sleep=lambda _seconds: None, **kwargs)

    def test_a_cooperative_process_is_never_killed(self):
        self.alive = {10}

        stopped, survivors = self._terminate([10])

        self.assertEqual((stopped, survivors), ([10], []))
        # One signal, not two: escalation is counted rather than compared by
        # number, because Windows has no SIGKILL to tell the second one apart.
        self.assertEqual(self.signals, [(10, signal.SIGTERM)])

    def test_a_process_that_ignores_the_request_is_killed(self):
        stubborn = 11
        self.alive = {stubborn}

        def kill(pid, number):
            self.signals.append((pid, number))
            if number == self.sigkill:
                self.alive.discard(pid)

        stopped, survivors = self._terminate([stubborn], kill=kill, grace_seconds=0.5)

        self.assertEqual((stopped, survivors), ([stubborn], []))
        self.assertEqual(self.signals, [(stubborn, signal.SIGTERM), (stubborn, self.sigkill)])

    def test_a_process_that_cannot_be_signalled_is_reported_as_a_survivor(self):
        # A refused signal proves the process is still there. Counting it as
        # stopped would have ``pdex stop`` claim an exit that never happened.
        alive_forever = 12
        self.alive = {alive_forever}

        def kill(pid, number):
            self.signals.append((pid, number))
            raise PermissionError(pid)

        stopped, survivors = self._terminate([alive_forever], kill=kill, grace_seconds=0.5)

        self.assertEqual(stopped, [])
        self.assertEqual(survivors, [alive_forever])

    def test_a_refused_signal_reads_as_still_running(self):
        with mock.patch.object(processes.os, "kill", side_effect=PermissionError):
            self.assertTrue(processes._signal(13, signal.SIGTERM))

    def test_this_process_is_alive(self):
        self.assertTrue(processes.is_alive(os.getpid()))

    def test_windows_liveness_never_signals(self):
        # Signal 0 is CTRL_C_EVENT on Windows, so probing with `os.kill` sends a
        # console interrupt to the target's group -- this process included.
        with (
            mock.patch.object(processes.os, "name", "nt"),
            mock.patch.object(processes, "_windows_alive", return_value=True) as probe,
            mock.patch.object(processes.os, "kill", side_effect=AssertionError("signalled")),
        ):
            self.assertTrue(processes.is_alive(4321))
        probe.assert_called_once_with(4321)


class StopAllTests(unittest.TestCase):
    def test_reports_when_nothing_is_running(self):
        with mock.patch.object(processes, "find_runs", return_value=[]):
            self.assertEqual(processes.stop_all(), 0)

    def test_stops_every_run_it_finds(self):
        runs = [(10, "pdex watch"), (12, "pdex discover")]
        with (
            mock.patch.object(processes, "find_runs", return_value=runs),
            mock.patch.object(processes, "terminate", return_value=([10, 12], [])) as terminate,
        ):
            self.assertEqual(processes.stop_all(), 0)

        self.assertEqual(list(terminate.call_args[0][0]), [10, 12])

    def test_a_survivor_is_a_failure(self):
        runs = [(10, "pdex watch")]
        with (
            mock.patch.object(processes, "find_runs", return_value=runs),
            mock.patch.object(processes, "terminate", return_value=([], [10])),
        ):
            self.assertEqual(processes.stop_all(), 1)

    def test_listing_never_signals_anything(self):
        with (
            mock.patch.object(processes, "find_runs", return_value=[(10, "pdex watch")]),
            mock.patch.object(processes.os, "kill", side_effect=AssertionError("signalled")),
        ):
            self.assertEqual(processes.list_runs(), 0)


if __name__ == "__main__":
    unittest.main()
