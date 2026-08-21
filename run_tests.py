#!/usr/bin/env python3
"""Test entry point.

By default runs the offline suite: no Chrome, network, or platform account.
The previous harness gated every test on a live CDP session, so a normal run
executed zero tests and reported OK.

    python3 run_tests.py            # offline unit tests
    python3 run_tests.py --live     # also probe a running Chrome on :9222
"""

import argparse
import sys
import unittest


def offline_suite():
    """Discovers the browser-free unit tests under ./tests."""
    return unittest.defaultTestLoader.discover("tests", pattern="test_*.py")


def live_check(cdp_url):
    """Smoke-tests a real CDP connection. Returns True when reachable."""
    from phantadex import logs
    from phantadex.session import BrowserSession

    log = logs.get_logger("live")
    try:
        with BrowserSession(cdp_url) as session:
            page = session.find_course_page()
            if page is None:
                log.warning("Connected, but no course tab is open.")
                return False

            from phantadex import detection, page_ops

            logs.ok(f"tab: {page.title()}")
            label, tag = page_ops.page_context(page)
            logs.step(f"context {label} [{tag}]")
            logs.get_logger().info(f"   Classified: {detection.classify(page)}")
            return True
    except Exception as exc:
        log.error("Could not attach to Chrome at %s: %s", cdp_url, exc)
        log.error("Start it with: python3 scripts/start_chrome_debug.py")
        return False


def main(argv=None):
    """Runs the offline suite, plus the live probe when asked."""
    parser = argparse.ArgumentParser(description="Phantadex test runner.")
    parser.add_argument("--live", action="store_true", help="Also probe a running Chrome session")
    parser.add_argument("--cdp-url", default="http://localhost:9222")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    print("=" * 62)
    print("OFFLINE SUITE (no browser required)")
    print("=" * 62)
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(offline_suite())
    if not result.wasSuccessful():
        return 1

    if args.live:
        print("\n" + "=" * 62)
        print("LIVE SESSION PROBE")
        print("=" * 62)
        if not live_check(args.cdp_url):
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
