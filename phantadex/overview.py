"""The page ``pdex -h`` prints: every command and behaviour on one screen.

Written for a reader who has not seen the tool before, human or agent, so it
answers what each command does, what a run does to each kind of course item,
where files land, and what is deliberately never automated. Argparse's own
top-level help could not: it lists command *names* with no room to say what any
of them does.

What can be derived is derived rather than restated. The per-item lines are
built from the registered handlers and the defaults are read from
:mod:`phantadex.config`, so a handler added without a summary, or a default
changed in one place, is caught by ``tests/test_cli.py`` instead of quietly
leaving this page wrong.
"""

import textwrap

from . import REPOSITORY_URL, __version__, config, course_manager, handlers, schema

# Ordered as a reader meets them: look at the course, then run it, then the
# narrower tools. ``cli`` takes its command list from these keys.
COMMAND_SUMMARIES = {
    "dex": "Print the course tree with each item's type, length and completion state.",
    "skip": "Seek the video in the active tab once, without starting a run.",
    "watch": "Work through the course from the first unfinished item. The main command.",
    "archive": "Visit every mapped video and reading for their text alone, in bulk.",
    "discover": "Re-verify the page selectors against the live site and save what matched.",
    "chrome": "Start Chrome with remote debugging on, ready to be attached to.",
    "stop": "End every Phantadex process on this machine.",
}

_NEVER = (
    "Answer a quiz, graded or ungraded, or submit an attempt.",
    "Write, draft or post a discussion reply.",
    "Submit peer, honors or programming work.",
    "Answer a survey about you, or accept a consent dialog on your behalf.",
    "Handle your password: it attaches to a Chrome you signed in to yourself.",
)


# The page is written in ASCII alone. It is printed before logging has
# widened stdout to UTF-8, and a redirected stream on Windows inherits the ANSI
# code page, where a box-drawing rule would raise UnicodeEncodeError on the
# first line of help.
#
# Rules are laid down as markers and sized once the page is built, so a rule
# always spans the widest line under it: the longest entries are handler
# summaries and installation paths, neither of which this module can predict.
_RULE = "\x00rule:"
# Eighty columns is the width a terminal is still guaranteed to have.
MAX_PAGE_WIDTH = 80


def _heading(title, char="-"):
    """Returns a section title and the marker for the rule under it."""
    return [title, f"{_RULE}{char}"]


def _apply_rules(lines):
    """Replaces each rule marker with a rule as wide as the widest line."""
    width = min(
        max((len(line) for line in lines if not line.startswith(_RULE)), default=0), MAX_PAGE_WIDTH
    )
    return [line[len(_RULE) :] * width if line.startswith(_RULE) else line for line in lines]


def _rows(pairs, indent="  "):
    """Returns two-column rows, the second column aligned and wrapped.

    Handler summaries and installation paths are written without this layout in
    mind, so the column is wrapped to the page rather than allowed to set the
    page's width and push the rules out past the edge of a terminal.
    """
    label_width = max((len(left) for left, _ in pairs), default=0)
    body_width = max(MAX_PAGE_WIDTH - len(indent) - label_width - 2, 24)
    rows = []
    for left, right in pairs:
        wrapped = textwrap.wrap(right, body_width) or [""]
        rows.append(f"{indent}{left:<{label_width}}  {wrapped[0]}".rstrip())
        rows.extend(f"{indent}{'':<{label_width}}  {line}" for line in wrapped[1:])
    return rows


def _commands_section():
    """Returns the command list, one line each."""
    return _rows(list(COMMAND_SUMMARIES.items()))


def _items_section():
    """Returns one line per registered handler, in dispatch order."""
    return _rows(
        [(page_type.lower(), handler.summary) for page_type, handler in handlers.HANDLERS.items()]
    )


def render():
    """Returns the full help page as plain text."""
    program = config.program_name()
    skip_range = config.DEFAULT_VIDEO_SKIP_RANGE
    low, high = config.DEFAULT_VIDEO_THRESHOLD
    ledger_cap = f"{course_manager.MAX_LEDGER_CONTENT_CHARS:,}"
    lines = [
        f"Phantadex {__version__}",
        *_heading("", "=")[1:],
        "Works a Coursera course in a Chrome tab you are already signed in to.",
        "It attaches to that browser over the DevTools protocol; it never opens",
        "a session of its own and never handles your login.",
        "",
        *_heading("USAGE"),
        *_rows(
            [
                (f"{program} [command] [options] [URL]", "run a command"),
                (f"{program} <command> -h", "the options for one command"),
            ]
        ),
        "",
        "  A command works in the course tab that is open, so keep one course tab",
        "  open and no more: with several, the first one Chrome lists is used and",
        "  the choice is only reported, not made for you. Naming a course item",
        "  settles it and opens the item too, as a full link or a /learn/... path:",
        "",
        f"    {program} https://www.coursera.org/learn/<course>/lecture/<id>/<slug>",
        f"    {program} watch /learn/<course>/home/week/1",
        "",
        "  A run still starts at the first unfinished item; add --no-resume to",
        "  start on the item named instead.",
        "",
        *_heading("GETTING STARTED"),
        *_rows(
            [
                (f"1. {program} chrome", "start Chrome with debugging enabled"),
                ("2. sign in", "open the course in that window, in one tab"),
                (f"3. {program} watch", "leave it running"),
            ]
        ),
        "",
        *_heading("COMMANDS"),
        *_commands_section(),
        "",
        *_heading("WHAT A RUN DOES WITH EACH ITEM"),
        *_items_section(),
        "",
        *_heading("PACING"),
        f"  Videos play through to a target sampled per video from {low:g}-{high:g}%;",
        f"  --skip seeks into {skip_range} instead. Readings dwell for the duration the",
        "  page lists. Clicks, scrolls and pauses are drawn from a log-normal spread",
        "  rather than fired on a fixed interval.",
        "",
        *_heading("FILES"),
        f"  {config.TRANSCRIPT_DIR}/<course>/",
        "      One .txt per archived item, holding its full text.",
        f"  {config.TRANSCRIPT_DIR}/<course>/<course-slug>.pdex.xml",
        "      The ledger: every item, its type, and its archival state. It keeps up to",
        f"      {ledger_cap} characters of an item's text and names the .txt file",
        "      holding the rest. Nothing recorded is renamed, reordered or dropped.",
        f"  <state directory>/{schema.STATE_FILE_NAME}",
        "      Selectors 'discover' proved against the live site, layered over the ones",
        f"      shipped with the package. Set {schema.STATE_DIR_ENV} to relocate it.",
        "",
        *_heading("NEVER DOES"),
        *[f"  - {line}" for line in _NEVER],
        "",
        "  Graded work is detected, named in the log and stepped past so one item",
        "  cannot stall a run; --pause-on-graded waits for you to finish it instead.",
        "",
        *_heading("COMMON OPTIONS"),
        *_rows(
            [
                ("--cdp-url URL", f"Chrome DevTools endpoint. Default {config.CDP_URL}"),
                (
                    "--transcript-dir DIR",
                    f"Root archive directory. Default {config.TRANSCRIPT_DIR}",
                ),
                ("-v / -q / --log-level", "Raise or lower how much a run reports."),
                ("URL", "Course item to open first, instead of the open tab."),
                ("--version", "Print the version and exit."),
            ]
        ),
        "",
        *_heading("MORE"),
        f"  {REPOSITORY_URL}",
        "",
    ]
    return "\n".join(_apply_rules(lines))
