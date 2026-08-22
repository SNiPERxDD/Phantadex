"""Timed console prompt used when an item is already marked complete."""

import os
import select
import sys
import time

from . import logs

log = logs.get_logger("prompt")


def stay_on_item(timeout=30):
    """Asks whether to stay on a completed item. Returns True to stay.

    Times out into "skip" on POSIX. On Windows without ``msvcrt`` it defaults to
    staying, so an unattended run cannot skip past something unintentionally.
    """
    if not sys.stdin.isatty():
        # Redirected stdin reads EOF instantly, which the old code mistook for a
        # keystroke and treated as "stay" -- so an unattended run never skipped.
        logs.step("already complete; skipping (stdin is not a terminal)")
        return False

    # The two paths differ: Windows acts on a single keypress, POSIX reads a
    # line. Announcing "+ Enter" on Windows described the wrong interaction.
    keys = "'s'" if os.name == "nt" else "'s' + Enter"
    logs.step(
        f"already complete -- press {keys} to skip, anything else to stay ({timeout}s auto-skips)"
    )
    if os.name == "nt":
        return _windows_prompt(timeout)
    return _posix_prompt(timeout)


def _posix_prompt(timeout):
    """select()-based timed read from stdin."""
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
    except Exception as exc:
        log.debug("select() on stdin failed: %s", exc)
        return True
    if not ready:
        return False  # timed out -> skip
    line = sys.stdin.readline()
    if line == "":
        return False  # EOF, not a keystroke
    return line.strip().lower() != "s"


def _windows_prompt(timeout):
    """msvcrt-based timed read, falling back to 'stay' when unavailable."""
    try:
        import msvcrt
    except ImportError:
        logs.step("(Windows: timed input unavailable; staying on this item.)")
        time.sleep(timeout)
        return True

    deadline = time.time() + timeout
    while time.time() < deadline:
        if msvcrt.kbhit():
            return msvcrt.getch().lower() != b"s"
        time.sleep(0.1)
    return False
