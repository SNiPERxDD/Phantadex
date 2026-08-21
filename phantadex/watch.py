#!/usr/bin/env python3
"""Package entry point for Phantadex Watch.

This module parses Watch arguments and starts the traversal runner.

    python phantadex_watch.py --log-level DEBUG
    python phantadex_watch.py --no-video-skip
"""

import sys

from . import config, logs, runner


def main(argv=None):
    """Parses arguments and runs the traversal loop."""
    parser = config.add_course_url_arg(
        config.add_automation_args(
            config.build_parser(
                "Phantadex Watch — course traversal and archival co-pilot.", "watch"
            )
        )
    )
    settings = config.settings_from_args(parser.parse_args(argv))
    logs.setup(settings.log_level)

    try:
        runner.run(settings)
    except KeyboardInterrupt:
        logs.interrupted()
        return 0
    except Exception as exc:
        logs.get_logger().error("Fatal: %s", exc)
        logs.get_logger().debug("Traceback:", exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
