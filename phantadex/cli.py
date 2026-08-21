"""Unified Phantadex command line with ``phantadex`` and ``pdex`` aliases."""

import argparse
import os
import sys

from . import __version__, archive, chrome, config, discovery, logs, video, watch
from .session import BrowserSession

COMMANDS = ("dex", "skip", "watch", "archive", "discover", "chrome")


def _help_parser():
    """Builds the small top-level command chooser."""
    program_name = os.path.basename(sys.argv[0])
    if program_name not in {"phantadex", "pdex"}:
        program_name = "pdex"
    parser = argparse.ArgumentParser(
        prog=program_name,
        description="Phantadex course companion.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=COMMANDS,
        help="dex (default), skip, watch, archive, discover, or chrome",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"phantadex {__version__}",
    )
    return parser


def dex_main(argv=None):
    """Prints the active course tree without navigating or writing a ledger."""
    parser = config.build_parser("Phantadex Dex — show the active course tree.")
    settings = config.settings_from_args(parser.parse_args(argv))
    logs.setup(settings.log_level)

    with BrowserSession(settings.cdp_url) as browser_session:
        page = browser_session.find_course_page()
        if page is None:
            logs.error("No course tab found. Open a course in the debug Chrome first.")
            return 1
        course_name = discovery.get_robust_course_name(page)
        with logs.spinner("generating course map"):
            course_map = discovery.get_detailed_course_map(page)
        if not course_map:
            logs.error("Course tree generation failed.")
            return 1
        discovery.print_course_map(
            course_map,
            course_name,
            discovery.get_completion_status(page),
        )
    return 0


def skip_main(argv=None):
    """Seeks the active video once to a random point in the configured range."""
    parser = config.build_parser("Phantadex Skip — seek the active video once.")
    parser.add_argument(
        "--video-skip-range",
        default=config.DEFAULT_VIDEO_SKIP_RANGE,
        help=(
            "Random seek range, e.g. '97.5-98.5%%' or '00:30-01:15' "
            f"(default: {config.DEFAULT_VIDEO_SKIP_RANGE.replace('%', '%%')})."
        ),
    )
    settings = config.settings_from_args(parser.parse_args(argv))
    logs.setup(settings.log_level)

    with BrowserSession(settings.cdp_url) as browser_session:
        page = browser_session.find_course_page()
        if page is None:
            logs.error("No course tab found. Open a video in the debug Chrome first.")
            return 1
        return 0 if video.seek_into_range(page, settings.video_skip_range) else 1


def discover_main(argv=None):
    """Re-verifies every schema selector against the live pages of a course.

    Walks the course through the sidebar, probes each selector on the item it
    lands on, and writes the ones that matched to the user state file, which is
    layered over the packaged defaults on the next run.
    """
    parser = config.build_parser(
        "Phantadex Discover -- re-verify selectors against the live course."
    )
    settings = config.settings_from_args(parser.parse_args(argv))
    logs.setup(settings.log_level)
    discovery.start_dynamic_observation(settings.cdp_url)
    return 0


def main(argv=None):
    """Dispatches the package CLI; no command defaults to Phantadex Dex."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in {"-h", "--help"}:
        _help_parser().print_help()
        return 0
    if arguments and arguments[0] == "--version":
        _help_parser().parse_args(["--version"])
        return 0

    if arguments and arguments[0] in COMMANDS:
        command = arguments.pop(0)
    elif not arguments or arguments[0].startswith("-"):
        command = "dex"
    else:
        _help_parser().error(f"invalid command: {arguments[0]}")

    handlers = {
        "dex": dex_main,
        "skip": skip_main,
        "watch": watch.main,
        "archive": archive.main,
        "discover": discover_main,
        "chrome": chrome.main,
    }
    try:
        return handlers[command](arguments)
    except KeyboardInterrupt:
        logs.interrupted()
        return 0
    except Exception as exc:
        logs.get_logger().error("Fatal: %s", exc)
        logs.get_logger().debug("Traceback:", exc_info=True)
        return 1
