"""Verifies schema selectors against the live page and records what works.

Deliberately free of navigation: this module says what the current page
contains, and the caller decides where to go next. That is what keeps the
dependency between probing and hopping pointing one way.
"""

import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime

import yaml

from .. import logs, schema
from ..element_schema import ELEMENTS_SCHEMA
from . import context, rules

log = logs.get_logger("discovery.probing")

# Selectors this pass verifies are written to the user state file, never into
# the installed package: package directories may be read-only, and a reinstall
# would discard every repair. phantadex.schema layers the two at read time.
CONFIG_FILE = schema.state_config_path()


def find_element_in_frames(page, selector):
    """Searches main page and all iframes for a selector."""
    try:
        # Check main page
        loc = page.locator(selector).first
        if loc.count() > 0 and loc.is_visible():
            return loc, "main"

        # Check all frames
        for frame in page.frames:
            try:
                loc = frame.locator(selector).first
                if loc.count() > 0 and _is_usable(loc):
                    return loc, f"frame[{frame.name or frame.url[:30]}]"
            except Exception as exc:
                log.debug("Frame probe failed: %s", exc)
                continue
    except Exception as exc:
        log.debug("Cross-frame locator search failed: %s", exc)
    return None, None


def _is_usable(locator):
    """Reports whether a frame match is actually on screen.

    The frame branch used to accept any match, on the grounds that visibility is
    awkward across frames. The cost was silent: a hidden match was recorded as a
    *verified* selector, and verified selectors outrank the packaged defaults
    until the next discovery run. A hidden element still fails the visibility
    check, so the fallback is a non-empty bounding box for the frames where
    ``is_visible`` itself cannot answer.
    """
    try:
        return locator.is_visible()
    except Exception as exc:
        log.debug("Frame visibility check failed: %s", exc)
    try:
        box = locator.bounding_box()
    except Exception as exc:
        log.debug("Frame bounding box unavailable: %s", exc)
        return False
    return bool(box and box.get("width") and box.get("height"))


def backup_config():
    """Timestamps a copy of the selector state file alongside it."""
    if not os.path.exists(CONFIG_FILE):
        return
    try:
        backup_dir = os.path.join(os.path.dirname(CONFIG_FILE), "backups")
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(CONFIG_FILE, os.path.join(backup_dir, f"selectors_{timestamp}.yaml"))
    except OSError as exc:
        log.warning("Selector state backup failed: %s", exc)


def verify_scraping(page, selector, el_name):
    """Performs a test scrape to verify the selector actually works."""
    try:
        loc, _ = find_element_in_frames(page, selector)
        if loc:
            text = loc.inner_text().strip()
            if text:
                preview = text[:150].replace("\n", " ") + "..."
                logs.ok(f'{el_name} scrapes: "{preview}"')
                return True
            else:
                logs.warn(f"{el_name}: selector matched but returned no text.")
    except Exception as e:
        logs.warn(f"{el_name}: scrape failed: {e}")
    return False


# Which schema categories are worth probing on each page type. Scanning every
# category everywhere let video-player text match a reading-body selector.
_COMMON = ("course_metadata", "navigation", "sidebar")

RELEVANT_CATEGORIES = {
    "VIDEO": ("video_controls", "transcript") + _COMMON,
    "READING": ("content",) + _COMMON,
    "QUIZ": _COMMON,
    "ASSIGNMENT": _COMMON,
    "LAB": _COMMON,
    "DISCUSSION": ("content",) + _COMMON,
}

# Categories whose selectors are worth dumping a text sample for.
VERIFIABLE_CATEGORIES = ("content", "transcript", "course_metadata")

_HIGHLIGHT_JS = """(sel) => {
    const el = document.querySelector(sel);
    if (el) {
        el.style.outline = '4px solid #00FFFF';
        el.style.boxShadow = '0 0 10px #00FFFF';
        setTimeout(() => { el.style.outline = ''; el.style.boxShadow = ''; }, 2000);
    }
}"""


def categories_to_scan(page_type):
    """Returns the schema categories relevant to one page type."""
    return RELEVANT_CATEGORIES.get(page_type, tuple(ELEMENTS_SCHEMA))


def read_course_name(page, existing_config):
    """Reads the course name using the configured selector, or ``"N/A"``."""
    selector = existing_config.get("course_metadata", {}).get(
        "course_name", "a[title*='Home Page']"
    )
    try:
        loc = page.locator(selector).first
        if loc.count() > 0:
            return loc.inner_text().strip() or loc.get_attribute("title")
    except Exception as exc:
        log.debug("Course-name element read failed: %s", exc)
    return "N/A"


def _probe_element(page, el_info):
    """Finds the first selector that matches, returning ``(selector, where)``."""
    for selector in el_info["selectors"]:
        loc, location_type = find_element_in_frames(page, selector)
        if not loc:
            continue
        if location_type == "main":
            try:
                page.evaluate(_HIGHLIGHT_JS, selector)
            except Exception as exc:
                log.debug("Selector highlight failed: %s", exc)
        return selector, location_type
    return None, "main"


def _seed_findings(existing_config):
    """Copies the existing state so untouched categories survive the rewrite.

    This used to collect only the categories relevant to the current page type
    and then dump that over ``config.yaml`` wholesale -- so verifying selectors
    on a video page erased every reading and quiz selector already on file.
    Harmless while nothing read the file back; a live data-loss bug now that the
    runtime does.
    """
    return {
        category: dict(elements or {}) for category, elements in (existing_config or {}).items()
    }


def _print_banner(page, course, module, item, subtext, page_type):
    logs.item(item or page.title(), tag=page_type)
    logs.step(f"{course} · {module}")
    if subtext:
        logs.step(subtext)
    logs.step(page.url, level=logging.DEBUG)


def _save_findings(findings):
    """Backs up and atomically rewrites the verified-selector state file.

    The write goes through a temporary file in the same directory followed by
    :func:`os.replace`, so an interruption mid-write cannot leave a truncated
    state file that the next run would parse as an empty selector set.
    """
    logs.step(f"recording verified selectors in {CONFIG_FILE}")
    backup_config()
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        dir=os.path.dirname(CONFIG_FILE), prefix=".selectors.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            # allow_unicode keeps a selector's non-ASCII text readable in the
            # file instead of escaping it, and the pair makes the write
            # independent of the locale encoding on the machine that runs it.
            yaml.dump(findings, handle, sort_keys=False, allow_unicode=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, CONFIG_FILE)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise
    # The runtime caches this file on first read; drop that cache so a repair
    # made mid-session is visible without a restart.
    schema.reload_verified_selectors()
    logs.ok("verified selectors updated and backed up")


def discover_selectors(page, state):
    """Verifies each schema selector against the live page and records what works.

    Returns the full selector state after the pass. Progress is recorded on
    ``state`` so the caller can decide when the course has been covered.
    """
    existing_config = state.selectors
    course_name_val = read_course_name(page, existing_config)
    module_name_val, item_name_val, subtext_val = context.get_page_metadata(page, state.course_map)
    page_type = rules.detect_page_type(page)
    _print_banner(page, course_name_val, module_name_val, item_name_val, subtext_val, page_type)

    # Recorded early so the exit condition can see progress even if this page
    # turns up no new selectors.
    if page_type not in ("UNKNOWN", "FILLER"):
        state.discovered_types.add(page_type)

    # Seeded from the learned state alone. Seeding from the merged view wrote
    # the packaged defaults into the state file, where they permanently shadowed
    # any later upgrade of the shipped config.
    findings = _seed_findings(schema.state_selectors())
    wanted = categories_to_scan(page_type)
    pending_updates = False

    for category, elements in ELEMENTS_SCHEMA.items():
        if category not in wanted:
            continue
        known = findings.setdefault(category, {})
        header_printed = False

        for el_name, el_info in elements.items():
            selector, location_type = _probe_element(page, el_info)
            if not selector:
                continue

            # Compared against the effective value (state over packaged), so a
            # selector that merely restates a shipped default is not persisted.
            old_value = (existing_config.get(category) or {}).get(el_name)
            if old_value == selector:
                continue
            if old_value and old_value != "NOT_FOUND_YET":
                label, detail = "MODIFIED", f"{el_name}: {old_value} -> {selector}"
            else:
                label, detail = "NEW", f"{el_name}: {selector}"

            if not header_printed:
                logs.step(f"{category} updates:")
                header_printed = True
            logs.step(f"{label} {detail} ({location_type})")

            if category in VERIFIABLE_CATEGORIES:
                verify_scraping(page, selector, el_name)
            known[el_name] = selector
            pending_updates = True

    if pending_updates:
        _save_findings(findings)
    else:
        logs.ok("selectors are stable; no changes needed")

    state.selectors = findings
    if sys.stdout.isatty():
        # Only worth holding the highlights on screen for someone watching them;
        # a scripted discovery pass paid the wait for nothing.
        logs.step("pausing for inspection (5s)")
        time.sleep(5)
    return findings
