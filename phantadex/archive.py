#!/usr/bin/env python3
"""Phantadex Archive: saves mapped transcripts and readings in bulk.

Unlike the traversal engine this does not watch videos or mark items complete --
it only visits mapped items and extracts their text.

    python phantadex_archive.py
    python phantadex_archive.py --force
"""

import sys
import time

from . import config, jitter, logs, modals, page_ops, urls
from .course_manager import CourseManager
from .discovery import get_detailed_course_map, get_robust_course_name
from .session import BrowserSession

log = logs.get_logger("archiver")

# Settling time after opening an item, and the gap left between items.
ITEM_SETTLE_RANGE = (2.0, 4.0)
BETWEEN_ITEMS_RANGE = (1.0, 3.0)

ARCHIVABLE = {"VIDEO": "Transcript", "READING": "Reading"}


def collect_targets(course_map):
    """Flattens the course map into the archivable items, in course order."""
    return [
        (title, item_type, url)
        for _module, lessons in course_map.items()
        for title, item_type, url, _duration in lessons
        if item_type in ARCHIVABLE
    ]


def archive_item(page, manager, title, item_type, url):
    """Visits one item and archives its text. Returns True on success."""
    try:
        target = urls.absolute_url(url)
        log.debug("Navigating to %s", target)
        page.goto(target, wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        log.warning("Navigation to %s failed: %s", title, exc)
        return False

    time.sleep(jitter.duration(*ITEM_SETTLE_RANGE))
    modals.dismiss_all(page)

    if item_type == "VIDEO":
        text, method = page_ops.extract_transcript(page)
    else:
        text, method = page_ops.extract_reading(page), "UI_Scrape"

    if not text:
        logs.warn(f"Nothing extracted from {title}.")
        return False

    filename, ledger_ok = manager.save_content(page.url, text, ARCHIVABLE[item_type])
    logs.ok(f"archived via {method} · {filename} · ledger {'ok' if ledger_ok else 'unmatched'}")
    return True


def run(settings, force=False):
    """Archives every mapped video transcript and reading in the current course."""
    with BrowserSession(settings.cdp_url) as session:
        page = session.find_course_page(settings.course_url)
        if page is None:
            log.error("No course tab found. Open a course in the debug Chrome first.")
            return 1

        course_name = get_robust_course_name(page)
        if not course_name:
            logs.error("Course name is not available yet. Wait for the item to finish loading.")
            return 1
        logs.banner("Phantadex Archive", course_name)
        with logs.spinner("generating course map"):
            course_map = get_detailed_course_map(page)
        if not course_map:
            log.error("Course map generation failed. Aborting.")
            return 1

        manager = CourseManager(course_map, course_name, root_dir=settings.transcript_dir)
        logs.step(f"archive {manager.root_dir}")
        logs.step(f"Phantadex Dex · ledger {manager.xml_path}")

        targets = collect_targets(course_map)
        # Read the ledger once. The previous version re-parsed the whole XML --
        # transcripts included -- for every target, which was O(n^2) on disk.
        already_archived = set() if force else manager.archived_paths()
        logs.step(f"{len(targets)} archivable items ({len(already_archived)} already done)")

        succeeded = failed = skipped = 0
        for index, (title, item_type, url) in enumerate(targets, 1):
            if not force and any(urls.same_item(path, url) for path in already_archived):
                logs.item(title, f"{index}/{len(targets)} · skip", spaced=False)
                skipped += 1
                continue

            logs.item(
                title,
                f"{index}/{len(targets)} · {item_type.lower()}",
                spaced=False,
            )
            if archive_item(page, manager, title, item_type, url):
                succeeded += 1
            else:
                failed += 1
            time.sleep(jitter.duration(*BETWEEN_ITEMS_RANGE))

        logs.banner(f"Done. {succeeded} archived, {skipped} skipped, {failed} failed.")
        return 0 if failed == 0 else 2


def main(argv=None):
    """Parses arguments and runs the archiver."""
    parser = config.add_course_url_arg(
        config.build_parser("Phantadex Archive — bulk course content archiver.", "archive")
    )
    parser.add_argument("--force", action="store_true", help="Re-scrape already archived items")
    args = parser.parse_args(argv)

    settings = config.settings_from_args(args)
    logs.setup(settings.log_level)

    try:
        return run(settings, force=args.force)
    except KeyboardInterrupt:
        logs.interrupted()
        return 0
    except Exception as exc:
        log.error("Fatal: %s", exc)
        log.debug("Traceback:", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
