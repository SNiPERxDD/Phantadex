"""Console presentation shared by every entry point.

Call sites say *what happened* (``logs.ok("transcript saved")``); this module
decides how it looks. Keeping the glyphs and colour in one place is why the rest
of the package contains no formatting characters at all.

Everything routes through :mod:`logging`, so ``--log-level DEBUG`` surfaces the
per-selector failures that used to vanish into ``except: pass``.
"""

import datetime
import logging
import os
import re
import shutil
import sys
import threading
from contextlib import contextmanager
from itertools import cycle

LOGGER_NAME = "phantadex"
_configured = False
_last_decile = -1

# Directory of per-run log files, under the same user state directory the
# discovery selectors live in, and how many runs are kept there.
RUN_LOG_DIR_NAME = "logs"
RUN_LOG_KEEP = 20

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# The in-place line currently on screen -- a progress bar or a spinner frame --
# and the lock that keeps painting it atomic against a log record arriving.
# Without this a record lands on the same line the bar is repainting, welding
# the two together and leaving the bar's last frame frozen in the scrollback.
_transient = ""
_transient_lock = threading.RLock()


def strip_ansi(text):
    """Returns ``text`` with any colour escape sequences removed."""
    return _ANSI.sub("", text)


# Status glyphs. Deliberately plain Unicode -- no emoji, no private-use "nerd
# font" codepoints -- so the output is legible in any terminal and stays
# readable when piped to a file or a CI log.
GLYPH = {
    "item": "▎",
    "ok": "✓",
    "pending": "○",
    "step": "·",
    "tip": "»",
    "nav": "→",
    "warn": "!",
    "error": "✗",
}

INDENT = "  "

# How an item type is spelled and coloured for a person reading the output.
# One table, consulted by the course tree, the archiver and the traversal alike,
# so the same kind of item never appears under two different names or two
# different colours depending on which command printed it.
#
# The labels are the map's, which are finer than the runner's page types (a peer
# assignment and a submission are both handled as assignments but listed apart);
# both vocabularies are keyed here because both reach the screen.
TYPE_WORDS = {
    "FILLER": "survey",
    "PEER_REVIEW": "peer review",
    "REVIEW_PEERS": "peer review",
    "UNGRADED_PLUGIN": "ungraded plugin",
}

# Colour carries the same distinction the words do: what the run archives, what
# it plays, what it leaves to you, and what it walks past.
TYPE_COLOURS = {
    "VIDEO": "cyan",
    "READING": "green",
    "DISCUSSION": "magenta",
    "DIALOGUE": "magenta",
    "QUIZ": "yellow",
    "ASSIGNMENT": "yellow",
    "PEER_REVIEW": "yellow",
    "REVIEW_PEERS": "yellow",
    "PLUGIN": "blue",
    "UNGRADED_PLUGIN": "blue",
    "LAB": "blue",
}


def type_name(item_type):
    """Returns the reading name of an item type: ``PEER_REVIEW`` -> peer review."""
    label = str(item_type or "").upper()
    return TYPE_WORDS.get(label, label.lower().replace("_", " "))


def type_tag(item_type):
    """Returns the reading name of an item type, coloured for its kind.

    Types with no colour of their own -- a survey, an unclassified page -- are
    dimmed rather than left plain, because on a screen of item lines the ones
    the run does nothing with should be the ones that recede.
    """
    name = type_name(item_type)
    colour = TYPE_COLOURS.get(str(item_type or "").upper())
    return getattr(paint, colour)(name) if colour else paint.dim(name)


class _Palette:
    """ANSI codes, blanked out when the stream is not an interactive terminal."""

    def __init__(self, enabled):
        self.enabled = enabled

    def __call__(self, code, text):
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def dim(self, text):
        return self(2, text)

    def bold(self, text):
        return self(1, text)

    def green(self, text):
        return self(32, text)

    def yellow(self, text):
        return self(33, text)

    def red(self, text):
        return self(31, text)

    def bright_red(self, text):
        return self(91, text)

    def cyan(self, text):
        return self(36, text)

    def blue(self, text):
        return self(34, text)

    def magenta(self, text):
        return self(35, text)


def _enable_windows_ansi():
    """Turns on VT processing so a Windows console renders ANSI rather than echoing it.

    Windows Terminal enables this itself; the classic console host does not, and
    without it every escape sequence is printed literally. Failure is silent and
    means only that the console stays uncoloured.

    The handle is pointer-wide. ``ctypes`` assumes an undeclared function returns
    a 32-bit signed int, which truncates a 64-bit handle and then fails on every
    call that receives it, so the boundary is declared before it is crossed.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetConsoleMode.restype = wintypes.BOOL

        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        if not handle or handle == wintypes.HANDLE(-1).value:
            return
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:  # pragma: no cover - absent console, or a stubbed kernel32
        return


def _colour_enabled(stream):
    """Colour is opt-out via NO_COLOR and never used for redirected output."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        _enable_windows_ansi()
        return True
    if not getattr(stream, "isatty", lambda: False)():
        return False
    _enable_windows_ansi()
    return True


paint = _Palette(_colour_enabled(sys.stdout))


class ConsoleFormatter(logging.Formatter):
    """Prefixes each record with a glyph chosen from its level."""

    def format(self, record):
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}: {self.formatException(record.exc_info)}"
        if getattr(record, "preformatted", False):
            return message
        if record.levelno >= logging.ERROR:
            return f"{INDENT}{paint.red(GLYPH['error'])} {message}"
        if record.levelno >= logging.WARNING:
            return f"{INDENT}{paint.yellow(GLYPH['warn'])} {message}"
        if record.levelno <= logging.DEBUG:
            return paint.dim(f"{INDENT}{GLYPH['step']} {message}")
        return f"{INDENT}{message}"


class RunLogFormatter(logging.Formatter):
    """Formats a record for the run log file: timestamped, levelled, uncoloured.

    The console formatter's output is shaped for a terminal -- glyphs, indents
    and escape codes -- none of which belongs in a file that exists to be read
    back later. Colour is stripped rather than disabled at the source because
    :func:`_emit` hands over lines that were already painted.
    """

    default_msec_format = "%s.%03d"

    def format(self, record):
        record = logging.makeLogRecord(record.__dict__)
        record.msg = strip_ansi(record.getMessage())
        record.args = None
        return super().format(record)


class ConsoleHandler(logging.StreamHandler):
    """A stream handler that does not write over an in-place progress line.

    :func:`bar` and :func:`spinner` paint with a carriage return and no newline,
    so the cursor sits mid-line for as long as either is running. A record
    written straight out from there is appended to the bar instead of starting
    its own line, and the half-drawn bar is left behind in the scrollback
    looking like a run that stopped making progress. The line is erased before
    the record and repainted after it, so the animation survives the
    interruption instead of being cut off by it.
    """

    def emit(self, record):
        with _transient_lock:
            _erase_transient()
            try:
                super().emit(record)
            finally:
                _repaint_transient()


def console_stream():
    """Returns stdout, widened to UTF-8 when the stream will accept the change.

    The status glyphs are non-ASCII. A console attached to a terminal renders
    them, but a redirected stream inherits the locale encoding -- the ANSI code
    page on Windows, ASCII under a C locale -- which has no codepoint for them,
    so the first status line raises ``UnicodeEncodeError`` and ends the run.
    ``backslashreplace`` keeps output flowing on streams that refuse UTF-8.
    """
    stream = sys.stdout
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return stream
    try:
        reconfigure(encoding="utf-8", errors="backslashreplace")
    except (ValueError, OSError):
        try:
            reconfigure(errors="backslashreplace")
        except (ValueError, OSError):
            pass
    return stream


def setup(level="INFO"):
    """Configures the package logger once. Safe to call from any entry point.

    The logger itself is left at DEBUG and the console handler carries the
    requested level, so :func:`start_run_log` can record everything to a file
    regardless of how quiet the terminal was asked to be. That is the point of
    the file: the console level is chosen before anyone knows what will go
    wrong, and a run that fails under ``-q`` used to leave nothing to read.
    """
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    console_level = getattr(logging, str(level).upper(), logging.INFO)
    logger.setLevel(logging.DEBUG)
    if not _configured:
        handler = ConsoleHandler(console_stream())
        handler.setFormatter(ConsoleFormatter())
        logger.addHandler(handler)
        logger.propagate = False
        _configured = True
    for handler in logger.handlers:
        if isinstance(handler, ConsoleHandler):
            handler.setLevel(console_level)
    return logger


class RunLogHandler(logging.FileHandler):
    """Marks the file handler this module installs, so it can be found again.

    A plain :class:`logging.FileHandler` is indistinguishable from any other,
    and the package logger outlives a single run inside a long-lived process --
    a test suite, or an entry point called twice. Without a marker the second
    call added a second file, and every record from then on was written to both.
    """


def run_log_dir():
    """Returns the directory per-run log files are written to."""
    # Imported here rather than at module scope: ``schema`` logs through this
    # module, so a top-level import is a cycle.
    from . import schema

    return os.path.join(schema.state_dir(), RUN_LOG_DIR_NAME)


def start_run_log(command):
    """Adds a DEBUG-level file handler for this run. Returns the path, or ``None``.

    One file per run, named for the command and the moment it started. Nothing
    identifying the machine or the account goes into the name or the contents
    beyond what the run itself prints. A directory that cannot be created is not
    worth ending a run over, so the failure is reported and the run continues
    with console output alone.
    """
    directory = run_log_dir()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(directory, f"{command}-{stamp}.log")
    try:
        os.makedirs(directory, exist_ok=True)
        handler = RunLogHandler(path, encoding="utf-8")
    except OSError as exc:
        warn(f"Could not open a run log in {directory}: {exc}")
        return None
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(RunLogFormatter("%(asctime)s %(levelname)-7s %(name)s  %(message)s"))
    logger = logging.getLogger(LOGGER_NAME)
    close_run_log(logger)
    logger.addHandler(handler)
    _prune_run_logs(directory)
    return path


def close_run_log(logger=None):
    """Removes and closes any run log this module already installed.

    One run, one file. Called before a new one is opened so records are never
    fanned out across the handlers a previous call left behind.
    """
    logger = logging.getLogger(LOGGER_NAME) if logger is None else logger
    for handler in [h for h in logger.handlers if isinstance(h, RunLogHandler)]:
        logger.removeHandler(handler)
        handler.close()


def _prune_run_logs(directory, keep=RUN_LOG_KEEP):
    """Deletes all but the newest ``keep`` run logs, so the directory stays bounded.

    Sorted by modification time rather than by name. A run log is named
    ``{command}-{stamp}.log``, so sorting the names puts the command first and
    the timestamp second: with the directory full of ``watch`` logs, a run named
    ``archive`` sorted below every one of them and deleted the file it had just
    opened -- which on POSIX keeps the run writing into an unlinked inode.
    """
    try:
        entries = sorted(
            (name for name in os.listdir(directory) if name.endswith(".log")),
            key=lambda name: os.path.getmtime(os.path.join(directory, name)),
            reverse=True,
        )
        for name in entries[keep:]:
            os.remove(os.path.join(directory, name))
    except OSError as exc:
        log_path = os.path.join(directory, "*.log")
        get_logger().debug("Could not prune old run logs at %s: %s", log_path, exc)


def get_logger(name=None):
    """Returns a child logger under the package namespace."""
    if not _configured:
        setup()
    return logging.getLogger(LOGGER_NAME if name is None else f"{LOGGER_NAME}.{name}")


def _emit(text, level=logging.INFO):
    """Writes an already-formatted line, bypassing the level glyph."""
    get_logger().log(level, text, extra={"preformatted": True})


# --------------------------------------------------------------- public API


def banner(title, subtitle=""):
    """Opens a run with the course name and the settings in force."""
    _emit("")
    _emit(paint.bold(title))
    if subtitle:
        _emit(paint.dim(f"{INDENT}{subtitle}"))


def item(title, tag="", spaced=True):
    """Announces a new course item, with an optional right-aligned tag."""
    if spaced:
        _emit("")
    left = f"{paint.cyan(GLYPH['item'])} {paint.bold(title)}"
    if not tag:
        _emit(left)
        return
    width = shutil.get_terminal_size((80, 24)).columns
    room = width - len(tag) - 4
    if len(title) > room > 3:
        title = title[: room - 1] + "\u2026"
        left = f"{paint.cyan(GLYPH['item'])} {paint.bold(title)}"
    padding = max(1, width - len(title) - 2 - len(tag) - 1)
    _emit(f"{left}{' ' * padding}{paint.dim(tag)}")


def step(message, level=logging.INFO):
    """A neutral sub-step under the current item."""
    _emit(paint.dim(f"{INDENT}{GLYPH['step']} ") + message, level)


def ok(message):
    """A sub-step that completed successfully."""
    _emit(f"{INDENT}{paint.green(GLYPH['ok'])} {message}")


def pending(message):
    """A sub-step that is not completed yet.

    The marker is bright red rather than dim: on a course tree that is mostly
    ticks, the handful of rows still outstanding are what the page is being read
    for, and dimming them made the reader hunt. Errors keep plain red, which is
    the darker of the two.
    """
    _emit(f"{INDENT}{paint.bright_red(GLYPH['pending'])} {message}")


def tip(message):
    """A suggestion about how to change the run, not a report about this one.

    Kept apart from :func:`step` because a line that names a flag is addressed
    to the reader rather than describing what just happened, and the two read
    as the same kind of thing when they carry the same glyph.
    """
    _emit(f"{INDENT}{paint.cyan(GLYPH['tip'])} {message}")


def nav(message):
    """A move to another item."""
    _emit(f"{INDENT}{paint.cyan(GLYPH['nav'])} {message}")


def warn(message):
    get_logger().warning(message)


def error(message):
    get_logger().error(message)


def bar(fraction, caption="", width=24):
    """Renders an in-place progress bar. Call :func:`bar_done` to finish.

    When output is redirected, ``\r`` repainting would fill the file with
    hundreds of near-identical lines, so a piped run gets one line per decile
    instead.
    """
    global _last_decile
    fraction = max(0.0, min(1.0, fraction))
    if not paint.enabled:
        decile = int(fraction * 10)
        if decile != _last_decile:
            _last_decile = decile
            step(f"{fraction * 100:5.1f}%  {caption}".rstrip())
        return
    filled = width if fraction >= 1.0 else min(width - 1, int(fraction * width))
    track = "█" * filled + "░" * (width - filled)
    line = f"{INDENT}{INDENT}{paint.cyan(track)} {fraction * 100:5.1f}%"
    if caption:
        line = f"{line}  {paint.dim(caption)}"
    progress(line)


def bar_done():
    """Clears the in-place progress line so the next log lands cleanly."""
    global _last_decile, _transient
    _last_decile = -1
    if not paint.enabled:
        return
    with _transient_lock:
        _transient = ""
        _erase_transient()


def progress(message):
    """Writes an in-place progress line (bypasses logging; not a record).

    The line is remembered as well as written, so a log record arriving
    mid-animation can erase it, print itself on a line of its own, and put the
    animation back where it was.
    """
    global _transient
    with _transient_lock:
        _transient = message
        sys.stdout.write(f"\r{message}")
        sys.stdout.flush()


def _erase_transient():
    """Blanks the line the cursor is sitting on, leaving the cursor at its start."""
    if not paint.enabled:
        return
    width = shutil.get_terminal_size((80, 24)).columns
    sys.stdout.write("\r" + " " * (width - 1) + "\r")
    sys.stdout.flush()


def _repaint_transient():
    """Redraws the remembered in-place line after a record interrupted it."""
    if not _transient or not paint.enabled:
        return
    sys.stdout.write(f"\r{_transient}")
    sys.stdout.flush()


def interrupted(message="Stopped by user."):
    """Clears transient output and prints one consistent interruption line."""
    bar_done()
    _emit(message)


@contextmanager
def spinner(message):
    """Animates one TTY line while work runs; emits one step when redirected."""
    if not paint.enabled:
        step(message)
        yield
        return

    stopped = threading.Event()
    frames = cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")

    def animate():
        while not stopped.wait(0.08):
            progress(f"{INDENT}{paint.cyan(next(frames))} {message}")

    worker = threading.Thread(target=animate, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join(timeout=0.2)
        bar_done()
