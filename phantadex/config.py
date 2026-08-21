"""Runtime settings and CLI parsing shared by the entry points."""

import argparse
from dataclasses import dataclass

CDP_URL = "http://localhost:9222"
TRANSCRIPT_DIR = "phantadex_archive"
DEFAULT_VIDEO_SKIP_RANGE = "97.5-98.5%"
DEFAULT_READING_MINUTES = (7, 12)
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


@dataclass
class Settings:
    """Everything tunable in one place, instead of module-level globals."""

    cdp_url: str = CDP_URL
    transcript_dir: str = TRANSCRIPT_DIR
    log_level: str = "INFO"

    # Fraction of a video that must elapse before advancing (percent).
    video_completion_threshold: float = 100.0
    # Optional pre-watch seek, e.g. "97.5-98.5%" or "00:30-01:15". Empty disables.
    # Default preserved from the pre-refactor script; --no-video-skip turns it off.
    video_skip_range: str = DEFAULT_VIDEO_SKIP_RANGE

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

    def describe(self):
        """Returns a short human-readable summary of the active settings."""
        seek = self.video_skip_range or "disabled"
        reading = "-".join(str(value) for value in self.reading_default_minutes)
        graded = "pause" if self.pause_on_graded else "skip"
        start = "first-unfinished" if self.resume_at_incomplete else "here"
        return (
            f"threshold={self.video_completion_threshold}% seek={seek} reading={reading}m "
            f"start={start} "
            f"graded={graded} log={self.log_level}"
        )


def build_parser(description):
    """Builds the argument parser shared by both entry points."""
    parser = argparse.ArgumentParser(description=description)
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
        "--video-skip-range",
        default=DEFAULT_VIDEO_SKIP_RANGE,
        help=(
            "Seek videos into this range before watching, e.g. '97.5-98.5%%' or "
            f"'00:30-01:15' (default: {DEFAULT_VIDEO_SKIP_RANGE.replace('%', '%%')})."
        ),
    )
    parser.add_argument(
        "--no-video-skip",
        action="store_true",
        help="Disable the pre-watch seek entirely and play videos through.",
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
        default=100.0,
        help="Percent of a video that must elapse before advancing (default: 100).",
    )
    parser.add_argument(
        "--reading-minutes",
        type=parse_reading_minutes,
        default=DEFAULT_READING_MINUTES,
        metavar="MIN[-MAX]",
        help="Fallback reading dwell range in minutes (default: 7-12).",
    )
    return parser


def parse_video_threshold(value):
    """Parses a completion percentage, rejecting values outside 0-100.

    Unvalidated values were silently unreachable: above 100 the target could
    only be met by the player's ``ended`` state, and negatives were masked.
    """
    try:
        percent = float(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("threshold must be a number") from exc
    if not 0.0 <= percent <= 100.0:
        raise argparse.ArgumentTypeError("threshold must be between 0 and 100")
    return percent


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
        video_completion_threshold=getattr(args, "video_threshold", 100.0),
        video_skip_range=(
            ""
            if getattr(args, "no_video_skip", False)
            else (getattr(args, "video_skip_range", DEFAULT_VIDEO_SKIP_RANGE) or "").strip()
        ),
        reading_default_minutes=getattr(args, "reading_minutes", DEFAULT_READING_MINUTES),
        pause_on_graded=getattr(args, "pause_on_graded", False),
        resume_at_incomplete=not getattr(args, "no_resume", False),
    )
