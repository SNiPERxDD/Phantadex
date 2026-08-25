#!/usr/bin/env python3
"""Phantadex Archive: saves mapped transcripts and readings in bulk.

Unlike the traversal engine this does not watch videos or mark items complete --
it only visits mapped items and extracts their text.

    python phantadex_archive.py
    python phantadex_archive.py --force
"""

import sys
import time
from collections import Counter

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
    """Returns the archivable items in course order, each with its module.

    The module travels with the item so the run can be read the way the course
    is laid out. Flattening it away made a hundred-line list of titles with no
    indication of where in the course any of them sat.
    """
    return [
        (module, title, item_type, url)
        for module, lessons in course_map.items()
        for title, item_type, url, _duration in lessons
        if item_type in ARCHIVABLE
    ]


def already_done(targets, archived_paths):
    """Returns the target URLs the ledger already holds.

    Counted against the targets rather than reported as the size of the ledger.
    A run archives discussion prompts too, and this command visits only videos
    and readings, so the ledger holds items that are not targets -- and the raw
    ledger count read as more items done than there were to do.
    """
    return {
        url
        for _module, _title, _item_type, url in targets
        if any(urls.same_item(path, url) for path in archived_paths)
    }


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
    logs.ok(
        f"archived {logs.type_tag(item_type)} via {method} · {filename} · "
        f"ledger {'ok' if ledger_ok else 'unmatched'}"
    )
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
        done = already_done(targets, set() if force else manager.archived_paths())
        logs.step(f"{len(targets)} archivable items ({len(done)} already done)")

        # Counted up front so a module header can say how much sits under it,
        # which is what tells it apart from the item lines that follow.
        per_module = Counter(module for module, _title, _type, _url in targets)

        succeeded = failed = skipped = 0
        current_module = None
        for index, (module, title, item_type, url) in enumerate(targets, 1):
            if module != current_module:
                count = per_module[module]
                logs.item(module, f"{count} item{'' if count == 1 else 's'}")
                current_module = module

            state = "skip" if url in done else "archive"
            logs.item(
                title,
                f"{index}/{len(targets)} · {logs.type_name(item_type)} · {state}",
                spaced=False,
            )
            if url in done:
                skipped += 1
                continue

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
