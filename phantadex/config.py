"""Runtime settings and CLI parsing shared by the entry points."""

import argparse
import os
import sys
from dataclasses import dataclass

CDP_URL = "http://localhost:9222"
TRANSCRIPT_DIR = "phantadex_archive"
DEFAULT_VIDEO_SKIP_RANGE = "97.5-98.5%"
DEFAULT_VIDEO_THRESHOLD = (98.0, 100.0)
DEFAULT_READING_MINUTES = (7, 12)
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


@dataclass
class Settings:
    """Everything tunable in one place, instead of module-level globals."""

    cdp_url: str = CDP_URL
    transcript_dir: str = TRANSCRIPT_DIR
    log_level: str = "INFO"

    # Course item to open before the command starts. Empty means work in the
    # course tab already open, which is only unambiguous while there is one.
    course_url: str = ""

    # Fraction of a video that must elapse before advancing, as a ``(min, max)``
    # percent range sampled once per video. A single number is accepted too and
    # is held as a range of zero width.
    video_completion_threshold: tuple = DEFAULT_VIDEO_THRESHOLD
    # Optional pre-watch seek, e.g. "97.5-98.5%" or "00:30-01:15". Empty disables.
    # Off by default: a run plays the video through, and seeking to the end of
    # one is opt-in through --skip.
    video_skip_range: str = ""

    # Reading dwell when the page declares no duration.
    reading_default_minutes: tuple = DEFAULT_READING_MINUTES

    # Graded items are stepped past by default. The run used to block on them
    # indefinitely, which stalled the whole traversal on a single item.
    pause_on_graded: bool = False

    # Where a run starts: the first item the sidebar reports as unfinished,
    # rather than wherever the tab happens to be sitting.
    resume_at_incomplete: bool = True

    # Main-loop pacing.
    idle_poll_seconds: float = 2.0
    settle_seconds: float = 5.0
    stuck_iterations: int = 20
    paused_iterations_before_resume: int = 30
    completion_prompt_timeout: int = 30

    def __post_init__(self):
        """Normalises a scalar completion threshold into a range."""
        self.video_completion_threshold = threshold_bounds(self.video_completion_threshold)

    def describe(self):
        """Returns a short human-readable summary of the active settings."""
        seek = self.video_skip_range or "disabled"
        low, high = self.video_completion_threshold
        threshold = f"{low:g}" if low == high else f"{low:g}-{high:g}"
        reading = "-".join(str(value) for value in self.reading_default_minutes)
        graded = "pause" if self.pause_on_graded else "skip"
        start = "first-unfinished" if self.resume_at_incomplete else "here"
        return (
            f"threshold={threshold}% seek={seek} reading={reading}m "
            f"start={start} "
            f"graded={graded} log={self.log_level}"
        )


# The two console entry points the package installs. Anything else on
# ``argv[0]`` is a compatibility script, whose own name is the honest usage line.
ENTRY_POINTS = frozenset({"phantadex", "pdex"})


def program_name(subcommand=""):
    """Returns the invocation to print in a usage line.

    A parser left to work this out itself reports ``argv[0]`` alone, so every
    subcommand's help claimed to be ``pdex`` and none of the usage lines could
    be copied and run as printed.
    """
    name = os.path.basename(sys.argv[0] or "")
    if name not in ENTRY_POINTS:
        return name or "pdex"
    return f"{name} {subcommand}".strip()


def build_parser(description, subcommand=""):
    """Builds the argument parser shared by both entry points."""
    parser = argparse.ArgumentParser(description=description, prog=program_name(subcommand))
    parser.add_argument(
        "--cdp-url",
        default=CDP_URL,
        help=f"Chrome DevTools Protocol endpoint (default: {CDP_URL})",
    )
    parser.add_argument(
        "--transcript-dir",
        default=TRANSCRIPT_DIR,
        help=f"Root archive directory (default: {TRANSCRIPT_DIR})",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log every browser action: each click, scroll, seek and navigation.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Warnings and errors only.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=LOG_LEVELS,
        help="Set the level explicitly. Takes precedence over -v and -q.",
    )
    return parser


def add_course_url_arg(parser):
    """Adds the optional course link a command can be pointed at.

    Naming the item removes the guesswork of picking among several open tabs,
    and lets a run be started from a link without switching to the browser
    first. Every command that reads a course takes it, in the same position.
    """
    parser.add_argument(
        "url",
        nargs="?",
        default="",
        metavar="URL",
        help=(
            "Course item to open first, as a full link or a /learn/... path. "
            "Without it the command works in the open course tab."
        ),
    )
    return parser


def resolve_log_level(args):
    """Derives the log level from the explicit flag, then -q, then -v.

    ``--log-level`` wins so a scripted invocation can pin a level regardless of
    the shorthand flags; ``-q`` outranks ``-v`` because silencing output is the
    more conservative reading of a contradictory pair.
    """
    explicit = getattr(args, "log_level", None)
    if explicit:
        return explicit
    if getattr(args, "quiet", False):
        return "WARNING"
    if getattr(args, "verbose", False):
        return "DEBUG"
    return "INFO"


def add_automation_args(parser):
    """Adds the traversal-only arguments to a parser."""
    parser.add_argument(
        "--skip",
        action="store_true",
        help=(
            "Seek each video to the end of its range before watching, instead of "
            f"playing it through (range: {DEFAULT_VIDEO_SKIP_RANGE.replace('%', '%%')}, "
            "override with --video-skip-range)."
        ),
    )
    parser.add_argument(
        "--video-skip-range",
        default=None,
        help=(
            "Range the --skip seek lands in, e.g. '97.5-98.5%%' or '00:30-01:15'. "
            "Passing this implies --skip."
        ),
    )
    parser.add_argument(
        "--no-video-skip",
        action="store_true",
        help=(
            "Force the pre-watch seek off. This is the default; the flag is kept "
            "because it was previously the only way to ask for it, and it "
            "overrides --skip."
        ),
    )
    parser.add_argument(
        "--pause-on-graded",
        action="store_true",
        help=(
            "Stop on a graded quiz or assignment and wait for you to finish it. "
            "Without this the run logs the item and moves to the next one."
        ),
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help=(
            "Start on whichever item the tab is showing. Without this the run "
            "jumps to the first item the sidebar does not mark complete."
        ),
    )
    parser.add_argument(
        "--video-threshold",
        type=parse_video_threshold,
        default=DEFAULT_VIDEO_THRESHOLD,
        metavar="MIN[-MAX]",
        help=(
            "Percent of a video that must elapse before advancing, fixed (`95`) "
            "or a range sampled once per video (`98-100`, the default)."
        ),
    )
    parser.add_argument(
        "--reading-minutes",
        type=parse_reading_minutes,
        default=DEFAULT_READING_MINUTES,
        metavar="MIN[-MAX]",
        help="Fallback reading dwell range in minutes (default: 7-12).",
    )
    return parser


def resolve_skip_range(args):
    """Returns the pre-watch seek range, or ``""`` when videos play through.

    Seeking is opt-in. Naming a range is taken as asking for it, so
    ``--video-skip-range`` does not need ``--skip`` beside it, and
    ``--no-video-skip`` overrides both.
    """
    if getattr(args, "no_video_skip", False):
        return ""
    named_range = (getattr(args, "video_skip_range", None) or "").strip()
    if named_range:
        return named_range
    return DEFAULT_VIDEO_SKIP_RANGE if getattr(args, "skip", False) else ""


def parse_video_threshold(value):
    """Parses a completion percentage or ``MIN-MAX`` range, rejecting 0-100 breaches.

    Returns a ``(min, max)`` pair; a single number gives a range of zero width.
    Values outside 0-100 are unreachable rather than merely odd: above 100 the
    target can only be met by the player's ``ended`` state, and negatives are
    met by the first frame.
    """
    text = str(value).strip().rstrip("%")
    parts = text.split("-")
    if len(parts) not in (1, 2) or not all(part.strip() for part in parts):
        raise argparse.ArgumentTypeError("use MIN or MIN-MAX, for example 98-100")
    try:
        minimum, maximum = float(parts[0]), float(parts[-1])
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("threshold must be a number") from exc
    if not 0.0 <= minimum <= 100.0 or not 0.0 <= maximum <= 100.0:
        raise argparse.ArgumentTypeError("threshold must be between 0 and 100")
    if maximum < minimum:
        raise argparse.ArgumentTypeError("threshold range must be ordered, for example 98-100")
    return minimum, maximum


def threshold_bounds(value):
    """Returns a completion threshold as a ``(min, max)`` pair.

    Accepts what ``Settings`` may be handed directly -- a bare number, or a pair
    already parsed from the command line -- so the watch loop never has to ask
    which of the two it was given.
    """
    if isinstance(value, (tuple, list)):
        low, high = (float(bound) for bound in value)
        return (low, high) if low <= high else (high, low)
    percent = float(value)
    return percent, percent


def parse_reading_minutes(value):
    """Parses a positive ``MIN`` or ordered ``MIN-MAX`` minute range."""
    parts = str(value).strip().split("-")
    if len(parts) not in (1, 2):
        raise argparse.ArgumentTypeError("use MIN or MIN-MAX, for example 7-12")
    try:
        minimum, maximum = int(parts[0]), int(parts[-1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("minutes must be whole numbers") from exc
    if minimum < 1 or maximum < minimum:
        raise argparse.ArgumentTypeError("minutes must be positive and ordered")
    return minimum, maximum


def settings_from_args(args):
    """Converts parsed arguments into a :class:`Settings` instance."""
    return Settings(
        cdp_url=getattr(args, "cdp_url", CDP_URL),
        transcript_dir=getattr(args, "transcript_dir", TRANSCRIPT_DIR),
        log_level=resolve_log_level(args),
        video_completion_threshold=getattr(args, "video_threshold", DEFAULT_VIDEO_THRESHOLD),
        video_skip_range=resolve_skip_range(args),
        reading_default_minutes=getattr(args, "reading_minutes", DEFAULT_READING_MINUTES),
        pause_on_graded=getattr(args, "pause_on_graded", False),
        resume_at_incomplete=not getattr(args, "no_resume", False),
        course_url=getattr(args, "url", "") or "",
    )
