"""Discovery and termination of running Phantadex processes.

``pdex stop`` has to find runs it did not start: a watch left in another
terminal, a discovery session from an older build, a compatibility script
launched directly. Nothing shared is written at start-up to make that possible,
because a registry file survives a hard kill and then reports runs that are no
longer there. The process table is asked instead, and it is authoritative.
"""

import os
import signal
import subprocess
import sys
import time

from . import logs

log = logs.get_logger("processes")

# Command tokens that identify a Phantadex run.
ENTRY_POINT_NAMES = frozenset(
    {
        "pdex",
        "phantadex",
        "phantadex_watch.py",
        "phantadex_archive.py",
        "coursera_stealth.py",
        "coursera_archiver.py",
    }
)
MODULE_PACKAGE = "phantadex"

# How long a process is given to exit on its own before it is killed outright.
TERM_GRACE_SECONDS = 6.0
POLL_SECONDS = 0.25

_WINDOWS_QUERY = (
    "Get-CimInstance Win32_Process | "
    'ForEach-Object { "$($_.ProcessId)`t$($_.ParentProcessId)`t$($_.CommandLine)" }'
)


def process_table():
    """Returns ``(pid, parent_pid, command)`` for every visible process.

    An unreadable row is dropped rather than failing the scan: one malformed
    line from ``ps`` should not stop the rest of the runs being found.
    """
    if os.name == "nt":
        argv = ["powershell", "-NoProfile", "-Command", _WINDOWS_QUERY]
    else:
        argv = ["ps", "-axo", "pid=,ppid=,args="]

    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except Exception as exc:
        log.debug("Process listing failed: %s", exc)
        return []

    rows = []
    for line in completed.stdout.splitlines():
        fields = line.replace("\t", " ").split(None, 2)
        if len(fields) < 3:
            continue
        try:
            rows.append((int(fields[0]), int(fields[1]), fields[2].strip()))
        except ValueError:
            continue
    return rows


def is_phantadex_command(command):
    """Reports whether a command line is a Phantadex entry point.

    Only the executable and any token that names a file are considered. A bare
    word elsewhere on the line is an argument -- ``grep -rn pdex`` mentions the
    entry point without being one, and killing the user's search would be worse
    than missing a run.
    """
    tokens = (command or "").split()
    if not tokens:
        return False
    for index, token in enumerate(tokens):
        if token in {"-m", "--module"} and index + 1 < len(tokens):
            module = tokens[index + 1]
            if module == MODULE_PACKAGE or module.startswith(f"{MODULE_PACKAGE}."):
                return True
        names_a_file = "/" in token or "\\" in token or token.endswith(".py")
        if index == 0 or names_a_file:
            if os.path.basename(token.replace("\\", "/")) in ENTRY_POINT_NAMES:
                return True
    return False


def _own_process_tree(rows, pid=None):
    """Returns this process and every ancestor of it, so a scan never targets itself."""
    parents = {row[0]: row[1] for row in rows}
    current = os.getpid() if pid is None else pid
    tree = set()
    while current and current not in tree:
        tree.add(current)
        current = parents.get(current)
    return tree


def find_runs(rows=None, pid=None):
    """Returns ``(pid, command)`` for every Phantadex run outside this process tree."""
    rows = process_table() if rows is None else rows
    excluded = _own_process_tree(rows, pid)
    return [
        (row_pid, command)
        for row_pid, _parent, command in rows
        if row_pid not in excluded and is_phantadex_command(command)
    ]


def _signal(pid, number):
    """Sends a signal. Returns False only when the process is known to be gone."""
    try:
        os.kill(pid, number)
        return True
    except ProcessLookupError:
        return False
    except PermissionError as exc:
        # Refusing the signal proves the process exists: it is another user's,
        # or otherwise out of reach. Reporting it as gone would let ``stop``
        # claim it had ended something that is still running.
        log.debug("Not permitted to signal %s: %s", pid, exc)
        return True
    except OSError as exc:
        log.debug("Signalling %s failed: %s", pid, exc)
        return True


def is_alive(pid):
    """Reports whether a process still exists."""
    return _signal(pid, 0)


def terminate(pids, grace_seconds=TERM_GRACE_SECONDS, sleep=time.sleep):
    """Asks each process to exit, then kills whatever is still running.

    Returns ``(stopped, survivors)``. Termination is requested for every process
    before any waiting starts, so the grace period is shared rather than paid
    once per process.
    """
    pids = list(pids)
    for pid in pids:
        _signal(pid, signal.SIGTERM)

    deadline = grace_seconds
    remaining = [pid for pid in pids if is_alive(pid)]
    while remaining and deadline > 0:
        sleep(POLL_SECONDS)
        deadline -= POLL_SECONDS
        remaining = [pid for pid in remaining if is_alive(pid)]

    for pid in remaining:
        # SIGKILL is unavailable on Windows; TerminateProcess is what SIGTERM
        # already mapped to there, so there is no harder signal to escalate to.
        _signal(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    sleep(POLL_SECONDS)

    survivors = [pid for pid in pids if is_alive(pid)]
    stopped = [pid for pid in pids if pid not in survivors]
    return stopped, survivors


def stop_all():
    """Stops every Phantadex run and reports the result. Returns a process exit code."""
    runs = find_runs()
    if not runs:
        logs.ok("no Phantadex processes are running")
        return 0

    for pid, command in runs:
        logs.step(f"stopping {pid} · {_shorten(command)}")

    stopped, survivors = terminate(pid for pid, _command in runs)
    if stopped:
        logs.ok(f"stopped {len(stopped)} process(es)")
    for pid in survivors:
        logs.warn(f"{pid} is still running; it may belong to another user")
    return 1 if survivors else 0


def _shorten(command, limit=90):
    """Trims a command line to one readable log line."""
    collapsed = " ".join((command or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[: limit - 1]}…"


def list_runs():
    """Prints the running Phantadex processes without stopping any. Returns an exit code."""
    runs = find_runs()
    if not runs:
        logs.ok("no Phantadex processes are running")
        return 0
    for pid, command in runs:
        logs.step(f"{pid} · {_shorten(command)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - convenience for `python -m`
    sys.exit(stop_all())
