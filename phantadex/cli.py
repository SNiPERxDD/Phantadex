"""Unified Phantadex command line with ``phantadex`` and ``pdex`` aliases."""

import sys

from . import (
    __version__,
    archive,
    chrome,
    config,
    discovery,
    logs,
    overview,
    processes,
    urls,
    video,
    watch,
)
from .course_manager import CourseManager
from .session import BrowserSession

# One source for the command list: the overview documents each of them, and a
# command missing from it would be dispatchable but unmentioned by ``-h``.
COMMANDS = tuple(overview.COMMAND_SUMMARIES)


def dex_main(argv=None):
    """Prints the active course tree without navigating or writing a ledger."""
    parser = config.add_course_url_arg(
        config.build_parser("Phantadex Dex — show the active course tree.", "dex")
    )
    settings = config.settings_from_args(parser.parse_args(argv))
    logs.setup(settings.log_level)

    with BrowserSession(settings.cdp_url) as browser_session:
        page = browser_session.find_course_page(settings.course_url)
        if page is None:
            logs.error("No course tab found. Open a course in the debug Chrome first.")
            return 1
        course_name = discovery.get_robust_course_name(page)
        with logs.spinner("generating course map"):
            course_map = discovery.get_detailed_course_map(page)
        if not course_map:
            logs.error("Course tree generation failed.")
            return 1
        # The tree counts what is left to the run apart from what is left to
        # the user, and a discussion the ledger already holds has moved from
        # one side to the other -- so the tree reads the same ledger a run
        # would. Read-only in the strict sense: on a course no run has touched
        # it creates neither the directory nor the ledger, and it never takes
        # the lock a live run in another terminal is holding.
        manager = CourseManager(
            course_map, course_name, root_dir=settings.transcript_dir, read_only=True
        )
        discovery.print_course_map(
            course_map,
            course_name,
            discovery.get_completion_status(page),
            is_archived=manager.is_archived,
        )
    return 0


def skip_main(argv=None):
    """Seeks the active video once to a random point in the configured range."""
    parser = config.add_course_url_arg(
        config.build_parser("Phantadex Skip — seek the active video once.", "skip")
    )
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
        page = browser_session.find_course_page(settings.course_url)
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
    parser = config.add_course_url_arg(
        config.build_parser(
            "Phantadex Discover -- re-verify selectors against the live course.", "discover"
        )
    )
    settings = config.settings_from_args(parser.parse_args(argv))
    logs.setup(settings.log_level)
    discovery.start_dynamic_observation(settings.cdp_url, settings.course_url)
    return 0


def stop_main(argv=None):
    """Terminates every Phantadex process on this machine.

    Built on the shared parser like every other command, even though stopping
    reads nothing from the browser. A hand-rolled parser here rejected the
    global options the rest of the tool accepts, so the one command reached for
    when a run has gone wrong was also the one that refused the flag the user
    had been typing all session.
    """
    parser = config.build_parser("Phantadex Stop — end every running Phantadex process.", "stop")
    parser.add_argument(
        "--list",
        action="store_true",
        help="Show what is running without stopping it.",
    )
    args = parser.parse_args(argv)
    logs.setup(config.resolve_log_level(args))
    return processes.list_runs() if args.list else processes.stop_all()


def _resolve_flag(token, flags):
    """Reports whether ``token`` is a global option, and whether it takes a value.

    Matches what argparse itself accepts, so the two agree on where the flags
    end: ``--flag=value`` carried in one token, and any prefix long enough to
    name exactly one option. Anything else answers "not mine".
    """
    name = token.split("=", 1)[0]
    if name in flags:
        return True, flags[name] and "=" not in token
    if not name.startswith("--"):
        return False, False
    matches = [option for option in flags if option.startswith(name)]
    if len(matches) == 1:
        return True, flags[matches[0]] and "=" not in token
    return False, False


def _first_word(arguments):
    """Returns the index of the first argument that is not a leading flag.

    A command is recognised wherever it sits among the global flags, because
    ``pdex -v watch`` is a reasonable thing to type. Looking only at position
    zero sent that form to the default command instead, whose optional URL then
    swallowed ``watch`` -- and the default command opens whatever URL it is
    handed, so a flag typed first navigated the user's own course tab to
    ``/watch`` and lost their place. The token after a value-taking flag is
    stepped over as well, so ``--transcript-dir watch`` names a directory
    rather than a command.
    """
    flags = config.global_flags()
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if not token.startswith("-"):
            return index
        known, takes_value = _resolve_flag(token, flags)
        if not known:
            # Stop on it rather than step over it. An option this parser does
            # not know belongs to a command -- ``pdex --items 5 watch`` -- and
            # walking past it landed on the value, which was then announced as
            # the unknown command ``'5'``: true, useless, and not the mistake.
            return index
        index += 2 if takes_value else 1
    return len(arguments)


def main(argv=None):
    """Dispatches the package CLI; no command defaults to Phantadex Dex."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in {"-h", "--help", "help"}:
        print(overview.render())
        return 0
    if arguments and arguments[0] == "--version":
        print(f"phantadex {__version__}")
        return 0

    lead = _first_word(arguments)
    if lead < len(arguments) and arguments[lead] in COMMANDS:
        command = arguments.pop(lead)
    elif lead == len(arguments) or urls.is_platform_url(arguments[lead]):
        # A bare course link is the default command pointed at that item, so
        # ``pdex <url>`` reads the course the link names.
        command = "dex"
    else:
        program = config.program_name()
        token = arguments[lead]
        if token.startswith("-"):
            print(f"{program}: unknown option {token!r} before the command", file=sys.stderr)
            print(
                f"A command's own options go after its name, as in '{program} watch {token} ...'.",
                file=sys.stderr,
            )
        else:
            print(f"{program}: unknown command {token!r}", file=sys.stderr)
        print(f"Commands: {', '.join(COMMANDS)}", file=sys.stderr)
        print(f"Run '{program} -h' for what each one does.", file=sys.stderr)
        return 2

    handlers = {
        "dex": dex_main,
        "skip": skip_main,
        "watch": watch.main,
        "archive": archive.main,
        "discover": discover_main,
        "chrome": chrome.main,
        "stop": stop_main,
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
