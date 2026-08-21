"""Builds the course outline from the sidebar and reports its completion.

The sidebar is a flat list of headers and item links in document order; the
hierarchy is recovered by walking that list, not by nesting selectors.
"""

import time

from .. import interaction, logs, page_ops, urls
from . import row_text
from .rules import apply_filler_override, classify_sidebar_row, parse_duration

log = logs.get_logger("discovery.course_map")


def expand_sidebar(page):
    """Finds and clicks all collapsed module accordions in the sidebar."""
    try:
        # Coursera uses cds-AccordionHeader-button
        collapsed = page.locator("button.cds-AccordionHeader-button[aria-expanded='false']")
        count = collapsed.count()
        if count > 0:
            logs.step(f"expanding {count} collapsed modules")
            # Click them one by one
            for i in range(count):
                try:
                    # The DOM shifts after each click, so always take the
                    # first still-collapsed header rather than an index.
                    btn = page.locator(
                        "button.cds-AccordionHeader-button[aria-expanded='false']"
                    ).first
                    if btn.count() > 0:
                        if interaction.click(page, btn, reaction_range=(0.2, 0.5)):
                            time.sleep(0.5)
                except Exception as exc:
                    log.debug("Module expansion click failed: %s", exc)
                    continue
            return True
    except Exception as exc:
        log.debug("Module expansion pass failed: %s", exc)
    return False


# The sidebar mixes module headers and item links in document order; walking
# that flat list in order is what recovers the hierarchy.
# The accordion header button and the labelled item anchors are specific to the
# course outline, so they need no scope. A bare "h2, h3" is not: it also matched
# the section headings inside a reading's own body, and those were then walked
# as module headers, flushing the accumulated lessons under an article heading.
# Only that fallback is confined to the containers an outline can live in.
_OUTLINE_SCOPES = (
    "nav",
    "div.rc-ModuleOutline",
    "div[data-testid='outline']",
    "div[class*='AccordionPanel']",
    "div[id*='accordion-panel']",
)
SIDEBAR_ITEM_SELECTOR = ", ".join(
    ["button.cds-AccordionHeader-button", "a[aria-label][href*='/learn/']"]
    + [f"{scope} {tag}" for scope in _OUTLINE_SCOPES for tag in ("h2", "h3")]
)
HEADER_TAGS = ("BUTTON", "H2", "H3")
DEFAULT_MODULE = "Course Highlights"
MIN_HEADER_CHARS = 3


def _row_text(item):
    """Reads a row's text, forcing a row that holds none yet to render first."""
    text = row_text.read_lines(item)
    if text:
        return text
    # A virtualized row can hold no text at all until it is scrolled into view.
    try:
        item.scroll_into_view_if_needed()
        time.sleep(0.3)
    except Exception as exc:
        log.debug("Item scroll failed: %s", exc)
        return ""
    return row_text.read_lines(item)


def _parse_lesson(item):
    """Turns one sidebar link into a ``(title, type, href, duration)`` row."""
    href = item.get_attribute("href")
    if not href:
        return None

    label = (item.get_attribute("aria-label") or "").lower()
    full_text = _row_text(item)
    lines = [line.strip() for line in full_text.split("\n") if line.strip()]

    title = lines[0] if lines else "Untitled Item (Wait For Load)"
    subtext = lines[1].lower() if len(lines) > 1 else ""

    item_type = classify_sidebar_row(subtext, label, href)
    item_type = apply_filler_override(item_type, title, subtext)
    return title, item_type, href, parse_duration(full_text)


def _module_title(item):
    """Returns the module name from a header row, or ``None`` if unusable."""
    header = item.inner_text().strip()
    if len(header) < MIN_HEADER_CHARS:
        return None
    return header.split("\n")[0]


def get_detailed_course_map(page):
    """Builds ``{module: [(title, type, href, duration), ...]}`` from the sidebar."""
    course_map = {}
    try:
        try:
            page.wait_for_selector("a[href*='/learn/']", timeout=10000)
        except Exception as exc:
            log.debug("Sidebar links did not appear in time: %s", exc)

        expand_sidebar(page)
        time.sleep(1)

        items = page.locator(SIDEBAR_ITEM_SELECTOR)
        count = items.count()
        if count == 0:
            return {}

        current_module = DEFAULT_MODULE
        lessons = []

        for index in range(count):
            item = items.nth(index)
            tag = item.evaluate("el => el.tagName")

            if tag in HEADER_TAGS:
                title = _module_title(item)
                if title is None:
                    continue
                if lessons:
                    # Merge rather than assign: a duplicated accordion, or two
                    # modules genuinely sharing a title, previously discarded
                    # every item recorded under the earlier one.
                    course_map.setdefault(current_module, []).extend(
                        lesson
                        for lesson in lessons
                        if lesson not in course_map.get(current_module, [])
                    )
                current_module, lessons = title, []
            elif tag == "A":
                lesson = _parse_lesson(item)
                if lesson and lesson not in lessons:
                    lessons.append(lesson)

        if lessons:
            course_map.setdefault(current_module, []).extend(
                lesson for lesson in lessons if lesson not in course_map.get(current_module, [])
            )

    except Exception as exc:
        log.error("Map generation failed: %s", exc)
    return course_map


def get_completion_status(page):
    """Returns ``{item_path: completed}`` from the live sidebar rows."""
    status = {}
    try:
        for link in page.locator("a[href*='/learn/']").all():
            path = urls.normalize_path(link.get_attribute("href"))
            if path:
                status[path] = status.get(path, False) or page_ops.sidebar_link_completed(link)
    except Exception as exc:
        log.debug("Sidebar completion scan failed: %s", exc)
    return status


def print_course_map(course_map, course_title="Course", completion_status=None):
    """Prints the course structure through the shared Phantadex theme."""
    if not course_map:
        logs.warn("Course map is empty or could not be parsed.")
        return

    all_lessons = [lesson for lessons in course_map.values() for lesson in lessons]
    completed = 0
    remaining = 0
    if completion_status is not None:
        for _title, _item_type, href, _duration in all_lessons:
            state = completion_status.get(urls.normalize_path(href))
            completed += state is True
            remaining += state is False

    logs.banner("Phantadex Dex", course_title)
    summary = f"{len(all_lessons)} items"
    if completion_status is not None:
        summary += f" · {completed} complete · {remaining} remaining"
    logs.step(summary)

    for module, lessons in course_map.items():
        module_completed = (
            sum(
                completion_status.get(urls.normalize_path(href)) is True
                for _title, _item_type, href, _duration in lessons
            )
            if completion_status is not None
            else 0
        )
        tag = f"{module_completed}/{len(lessons)} complete" if completion_status is not None else ""
        logs.item(module, tag)

        for title, item_type, href, duration in lessons:
            detail = f"{title} · {item_type.lower().replace('_', ' ')}"
            if duration:
                detail += f" · {duration}"
            state = (
                None
                if completion_status is None
                else completion_status.get(urls.normalize_path(href))
            )
            if state is True:
                logs.ok(detail)
            elif state is False:
                logs.pending(detail)
            else:
                logs.warn(f"? {detail}")
