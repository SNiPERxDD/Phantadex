"""Console presentation shared by every entry point.

Call sites say *what happened* (``logs.ok("transcript saved")``); this module
decides how it looks. Keeping the glyphs and colour in one place is why the rest
of the package contains no formatting characters at all.

Everything routes through :mod:`logging`, so ``--log-level DEBUG`` surfaces the
per-selector failures that used to vanish into ``except: pass``.
"""

import logging
import os
import shutil
import sys
import threading
from contextlib import contextmanager
from itertools import cycle

LOGGER_NAME = "phantadex"
_configured = False
_last_decile = -1

# Status glyphs. Deliberately plain Unicode -- no emoji, no private-use "nerd
# font" codepoints -- so the output is legible in any terminal and stays
# readable when piped to a file or a CI log.
GLYPH = {
    "item": "▎",
    "ok": "✓",
    "pending": "○",
    "step": "·",
    "nav": "→",
    "warn": "!",
    "error": "✗",
}

INDENT = "  "


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

    def cyan(self, text):
        return self(36, text)


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
    """Configures the package logger once. Safe to call from any entry point."""
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    if not _configured:
        handler = logging.StreamHandler(console_stream())
        handler.setFormatter(ConsoleFormatter())
        logger.addHandler(handler)
        logger.propagate = False
        _configured = True
    return logger


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
    """A sub-step that is not completed yet."""
    _emit(f"{INDENT}{paint.dim(GLYPH['pending'])} {message}")


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
    global _last_decile
    _last_decile = -1
    if not paint.enabled:
        return
    width = shutil.get_terminal_size((80, 24)).columns
    sys.stdout.write("\r" + " " * (width - 1) + "\r")
    sys.stdout.flush()


def progress(message):
    """Writes an in-place progress line (bypasses logging; not a record)."""
    sys.stdout.write(f"\r{message}")
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
