"""Page-level reads: context labels, completion state, and content extraction.

These were duplicated between ``phantadex_watch.py`` and
``phantadex_archive.py``; both now import from here.
"""

import os
import random
import re
import time

from . import interaction, logs, schema, urls
from . import text as text_utils

log = logs.get_logger("page_ops")

MIN_TRANSCRIPT_CHARS = 50
MIN_READING_CHARS = 20
# A course outline is long but bounded; this caps a pathological DOM scan.
MAX_SIDEBAR_ROWS = 400


def page_context(page):
    """Returns ``(label, tag)`` for the current item.

    The tag carries the item id so two items sharing a title do not collapse
    into one context string (which previously defeated the navigation guards).
    Presentation is left to :mod:`phantadex.logs`.
    """
    module = "Unknown Module"
    locator = schema.first_visible(page, "course_metadata", "module_name")
    if locator is not None:
        try:
            module = locator.inner_text().strip() or module
        except Exception as exc:
            log.debug("Module name read failed: %s", exc)

    try:
        course = page.title().split("|")[0].strip()
    except Exception as exc:
        log.debug("Course title read failed: %s", exc)
        course = "Unknown Course"

    path = urls.normalize_path(page.url)
    return f"{course} › {module}", "/".join(path.split("/")[-2:])


def is_already_completed(page):
    """Reports whether the sidebar or item header marks this item complete."""
    try:
        time.sleep(1.5)  # let the sidebar finish its transition
        current_url = page.url

        # Coursera renders more than one link to the active item (the outline row
        # plus the in-page pager). Only the outline row carries the success icon,
        # so every match is examined rather than just the first in DOM order.
        matched_any = False
        for link in page.locator("a[href*='/learn/']").all():
            href = link.get_attribute("href")
            if not urls.same_item(href, current_url):
                continue
            matched_any = True
            if sidebar_link_completed(link):
                logs.step(f"sidebar confirms '{page.title().split('|')[0].strip()}' is complete")
                return True
        if matched_any:
            return False

        # A reading with no "mark complete" control has already been completed.
        has_body = schema.first_visible(page, "content", "reading_body") is not None
        has_mark = schema.first_visible(page, "navigation", "mark_complete") is not None
        return bool(has_body and not has_mark)
    except Exception as exc:
        log.debug("Completion check failed: %s", exc)
        return False


def is_locked_item(page):
    """Reports whether the page is a locked interstitial barring further progress.

    A visible Previous control is not enough on its own: Coursera's ordinary
    pager carries one too, and Playwright's ``:has-text()`` matches its label
    case-insensitively, so keying on that alone classified every item after the
    first as locked and the runner advanced and retreated without ever
    processing content. A genuine lock also offers no way forward.
    """
    if schema.first_visible(page, "navigation", "previous_item") is None:
        return False
    return schema.first_visible(page, "navigation", "next_item") is None


def sidebar_link_completed(link):
    """Reports completion from one specific sidebar link."""
    try:
        for selector in schema.selectors_for("sidebar", "success_icon"):
            if link.locator(selector).count() > 0:
                return True
        aria_parts = {
            part.strip().lower() for part in (link.get_attribute("aria-label") or "").split(",")
        }
        return "completed" in aria_parts
    except Exception as exc:
        log.debug("Sidebar completion read failed: %s", exc)
        return False


def extract_transcript(page):
    """Returns ``(text, method)`` for the active video, or ``(None, "FAILED")``.

    Stage 1 scrapes the transcript panel; stage 2 falls back to the downloadable
    ``.txt`` in the Files/Downloads panel.
    """
    scraped = _transcript_from_panel(page)
    if scraped:
        return text_utils.clean_transcript(scraped), "UI_Scrape"

    downloaded = _transcript_from_download(page)
    if downloaded:
        # The .txt is already prose, but normalise it through the same path so
        # both methods produce comparable text for the ledger's dedup check.
        return text_utils.clean_transcript(downloaded), "File_Download"

    return None, "FAILED"


def _transcript_from_panel(page):
    """Scrapes the on-page transcript panel, opening the tab when needed."""
    try:
        tab = schema.first_visible(page, "transcript", "transcript_container")
        if tab is not None and tab.evaluate("el => el.tagName").lower() == "button":
            if interaction.click(page, tab, reaction_range=(0.2, 0.5)):
                time.sleep(1.5)
    except Exception as exc:
        log.debug("Transcript tab toggle failed: %s", exc)

    for selector in schema.selectors_for("transcript", "transcript_container"):
        try:
            page.wait_for_selector(selector, timeout=3000)
            text = page.locator(selector).first.inner_text().strip()
            if len(text) >= MIN_TRANSCRIPT_CHARS and text != "Transcript":
                return text
        except Exception as exc:
            log.debug("Transcript selector %r failed: %s", selector, exc)
    return None


def _transcript_from_download(page):
    """Downloads the transcript ``.txt`` from Files/Downloads, if offered."""
    try:
        link = _first_visible_transcript_download(page, download_attribute_only=True)
        if link is None:
            downloads_tab = schema.first_visible(page, "transcript", "downloads_tab")
            if downloads_tab is None:
                return None
            if not interaction.click(page, downloads_tab, reaction_range=(0.2, 0.5)):
                return None
            time.sleep(2)
            link = _first_visible_transcript_download(page)
        if link is None:
            return None

        logs.step("panel scrape failed; trying .txt download fallback")
        with page.expect_download(timeout=10000) as download_info:
            if not interaction.click(page, link, reaction_range=(0.2, 0.5)):
                raise RuntimeError("Transcript download link was not clickable")
        temp_path = download_info.value.path()
        if temp_path and os.path.exists(temp_path):
            with open(temp_path, encoding="utf-8", errors="ignore") as handle:
                text = handle.read().strip()
            if text:
                return text
    except Exception as exc:
        log.debug("Transcript download fallback failed: %s", exc)
    return None


def _first_visible_transcript_download(page, download_attribute_only=False):
    """Returns a visible transcript link, optionally requiring ``download``."""
    for selector in schema.selectors_for("transcript", "transcript_download_link"):
        if download_attribute_only and "[download" not in selector:
            continue
        try:
            matches = page.locator(selector)
            for index in range(min(matches.count(), schema.MAX_VISIBILITY_SCAN)):
                candidate = matches.nth(index)
                if candidate.is_visible():
                    return candidate
        except Exception as exc:
            log.debug("Transcript download selector %r failed: %s", selector, exc)
    return None


def extract_reading(page):
    """Returns the reading body text, or ``None`` when it is missing/too short."""
    try:
        body = schema.first_visible(page, "content", "reading_body")
        if body is None:
            return None
        body_text = text_utils.clean_reading(body.inner_text())
        return body_text if len(body_text) >= MIN_READING_CHARS else None
    except Exception as exc:
        log.debug("Reading extraction failed: %s", exc)
        return None


DURATION_PATTERN = re.compile(r"(\d+)\s*min", re.IGNORECASE)

# Tried in order. The item header is the ideal source but Coursera does not
# render a duration there on every course, so the *active* sidebar row is the
# fallback -- it always carries a "Reading | 10 min" subtext.
HEADER_SCOPES = (
    "div[data-testid='item-page-content'] header",
    "main header",
    "main h1",
)


def detect_reading_minutes(page, default_range=(7, 12)):
    """Reads the declared reading duration from item metadata.

    Returns ``(minutes, source)``. ``source`` is ``"header"``, ``"sidebar"`` or
    ``"default"``.
    """
    for finder, source in ((_minutes_from_header, "header"), (_minutes_from_sidebar, "sidebar")):
        try:
            minutes = finder(page)
        except Exception as exc:
            log.debug("Duration source %r failed: %s", source, exc)
            continue
        if minutes:
            return minutes, source

    return random.randint(*default_range), "default"


def _minutes_from_header(page):
    """Reads the duration from the item header, or ``None``.

    Scoped to the header rather than scanning every div: an unscoped search
    matched hundreds of nested ancestors and often picked a sidebar quiz's
    duration instead of this item's.
    """
    for scope in HEADER_SCOPES:
        try:
            container = page.locator(scope).first
            if container.count() == 0 or not container.is_visible():
                continue
            match = DURATION_PATTERN.search(container.inner_text())
            if match:
                return int(match.group(1))
        except Exception as exc:
            log.debug("Duration scope %r failed: %s", scope, exc)
    return None


def _minutes_from_sidebar(page):
    """Reads the duration from the sidebar row for the current item, or ``None``.

    Every header scope above returns zero matches on the courses measured, so
    without this the function always fell through to a random default and the
    declared duration was never actually used.
    """
    rows = page.locator("a[href*='/learn/']")
    for index in range(min(rows.count(), MAX_SIDEBAR_ROWS)):
        row = rows.nth(index)
        try:
            href = row.get_attribute("href")
            if not href or not urls.same_item(href, page.url):
                continue
            match = DURATION_PATTERN.search(row.inner_text())
            if match:
                return int(match.group(1))
        except Exception as exc:
            log.debug("Sidebar duration row %d failed: %s", index, exc)
    return None
