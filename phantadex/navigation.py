"""Advancing to the next course item.

Consolidates the four near-identical "click Next, else fall back to the ledger"
blocks that appeared in the video, reading, quiz and plugin branches.
"""

import time

from . import interaction, jitter, logs, schema, urls

log = logs.get_logger("navigation")

# Every item transition ends in a settle. A fixed value repeated across
# hundreds of items is a cadence no person produces, so the wait is drawn.
SETTLE_RANGE = (3.5, 6.5)
MOVE_TIMEOUT_SECONDS = 8
MOVE_POLL_SECONDS = 0.5


def next_button(page):
    """Returns the first *visible* 'next item' button, or ``None``.

    Visibility matters: Coursera keeps hidden Next buttons inside collapsed
    player wrappers, and clicking one is a no-op that looks like a stall.

    The scan goes through :func:`schema.first_visible` rather than one
    comma-joined locator. A joined selector list is matched in DOM order, which
    discarded the "verified selectors first" priority that
    :func:`schema.selectors_for` establishes -- a generic fallback appearing
    higher in the document beat the precise verified selector.
    """
    return schema.first_visible(page, "navigation", "next_item")


def previous_button(page):
    """Returns the visible locked-item Previous control, or ``None``."""
    return schema.first_visible(page, "navigation", "previous_item")


def mark_complete(page):
    """Clicks the 'mark as complete' control when present. Returns success."""
    button = schema.first_visible(page, "navigation", "mark_complete")
    if button is None:
        return False
    try:
        completed = interaction.click(page, button, reaction_range=(0.4, 0.9))
        if completed:
            logs.ok("marked as completed")
        return completed
    except Exception as exc:
        log.debug("Mark-complete click failed: %s", exc)
        return False


def advance(page, manager, start_url=None, reaction_range=(0.8, 1.8)):
    """Moves to the next item: Next button first, then the ledger fallback.

    ``start_url`` guards against double-navigation -- if the page already moved
    on its own, this is a no-op. Returns one of ``"NAVIGATED"``, ``"ALREADY_MOVED"``,
    ``"COURSE_COMPLETE"`` or ``"FAILED"``.
    """
    if start_url is not None and not urls.same_item(page.url, start_url):
        return "ALREADY_MOVED"

    origin = page.url
    button = next_button(page)
    if button is not None:
        try:
            if button.is_enabled() and interaction.click(
                page, button, reaction_range=reaction_range
            ):
                # A visible, enabled Next button is not proof of navigation: on
                # some item types the click is a no-op. Without this check the
                # caller believes it moved and re-processes the same item.
                if _wait_for_move(page, origin):
                    time.sleep(jitter.duration(*SETTLE_RANGE))
                    return "NAVIGATED"
                log.debug("Next click did not change the item; using ledger fallback")
        except Exception as exc:
            log.debug("Next-button click failed: %s", exc)

    return _ledger_fallback(page, manager)


def retreat(page, manager, start_url=None, reaction_range=(0.8, 1.8)):
    """Returns from a locked item using its Previous control, then the course map.

    Returns ``"NAVIGATED"``, ``"ALREADY_MOVED"``, ``"AT_START"`` when the item is
    the first in the course, or ``"FAILED"``.
    """
    if start_url is not None and not urls.same_item(page.url, start_url):
        return "ALREADY_MOVED"

    origin = page.url
    button = previous_button(page)
    if button is not None:
        try:
            if interaction.click(page, button, reaction_range=reaction_range):
                if _wait_for_move(page, origin):
                    time.sleep(jitter.duration(*SETTLE_RANGE))
                    return "NAVIGATED"
                log.debug("Previous click did not change the item; using map fallback")
        except Exception as exc:
            log.debug("Previous-button click failed: %s", exc)

    if manager is None:
        logs.warn("Previous button unavailable and no course map loaded.")
        return "FAILED"
    previous_url = manager.get_previous_url(page.url)
    if not previous_url:
        if manager.is_mapped(page.url):
            logs.ok("already at the first item; nothing to retreat to")
            return "AT_START"
        logs.warn("Current item is not in the course map; cannot retreat.")
        return "FAILED"

    logs.nav(f"map retreat {previous_url}")
    try:
        page.goto(previous_url)
        time.sleep(jitter.duration(*SETTLE_RANGE))
        return "NAVIGATED"
    except Exception as exc:
        log.warning("Map retreat to %s failed: %s", previous_url, exc)
        return "FAILED"


def _wait_for_move(page, origin, timeout=None):
    """Polls until the current item differs from ``origin``, or the wait ends.

    The timeout is read from the module at call time rather than bound as a
    default, so it stays overridable.
    """
    deadline = time.time() + (MOVE_TIMEOUT_SECONDS if timeout is None else timeout)
    while time.time() < deadline:
        try:
            if not urls.same_item(page.url, origin):
                return True
        except Exception as exc:
            log.debug("URL read during move wait failed: %s", exc)
            return False
        time.sleep(MOVE_POLL_SECONDS)
    return False


def _ledger_fallback(page, manager):
    """Navigates directly to the next mapped URL when the UI button fails."""
    if manager is None:
        logs.warn("Next button unavailable and no course map loaded.")
        return "FAILED"

    next_url = manager.get_next_url(page.url)
    if not next_url:
        # ``None`` covers two situations: the item is genuinely last, or it was
        # never mapped (a sidebar that had not finished lazy-loading). Only the
        # first means the course is finished; reporting the second as complete
        # ended runs part-way through.
        if not manager.is_mapped(page.url):
            logs.warn("Current item is not in the course map; cannot pick the next one.")
            return "FAILED"
        logs.ok("no next item in course map -- traversal complete")
        return "COURSE_COMPLETE"

    if urls.same_item(next_url, page.url):
        # Navigating to the item already open advances nothing, and the caller
        # would read the success as progress and ask again on the next tick.
        # ``get_next_url`` no longer returns the current item, so reaching here
        # means the map itself repeats it -- which is a broken map, not an end.
        logs.warn("The next mapped item is the one already open; the course map repeats it.")
        return "FAILED"

    logs.nav(f"map navigate {next_url}")
    try:
        page.goto(next_url)
        time.sleep(jitter.duration(*SETTLE_RANGE))
        return "NAVIGATED"
    except Exception as exc:
        log.warning("Map navigation to %s failed: %s", next_url, exc)
        return "FAILED"
