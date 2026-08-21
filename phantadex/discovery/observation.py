"""The live discovery loop: map the course, probe each page, hop to the next.

The only module in the package that navigates. It owns the session state and
passes it down, so nothing below it has to reach back up for context.
"""

import time

from .. import config, interaction, logs, schema, session, urls
from . import context, course_map, probing
from .rules import CORE_TYPES
from .state import ObservationState

log = logs.get_logger("discovery.observation")

# Sentinel written to the last-seen URL while no course tab is open, so the
# "waiting" notice is logged once rather than on every poll.
WAITING = "WAITING"


def auto_hop_next(page, config):
    """Attempts to automatically navigate to the next item."""
    logs.nav("preparing to hop to the next item")
    time.sleep(5)  # Give user 5 seconds to see highlights before jumping

    # 1. Look for 'Next' button in config or schema
    next_selectors = config.get("navigation", {}).get("next_item", [])
    if isinstance(next_selectors, str):
        next_selectors = [next_selectors]

    # Flatten list of lists if needed
    for selector in next_selectors:
        try:
            loc, loc_type = probing.find_element_in_frames(page, selector)
            if loc:
                logs.step(f"clicking Next [{selector}] ({loc_type})")
                if interaction.click(page, loc):
                    return True
        except Exception as exc:
            log.debug("Next-button candidate failed: %s", exc)
            continue

    # 2. Fallback: Search for any button with 'Next' text
    try:
        next_btn = page.locator("button:has-text('Next'), button[aria-label*='next']").first
        if next_btn.count() > 0 and next_btn.is_visible():
            logs.step("clicking the fallback Next button")
            if interaction.click(page, next_btn):
                return True
    except Exception as exc:
        log.debug("Next-button text fallback failed: %s", exc)

    logs.warn("Auto-hop failed: no Next button found.")
    return False


def get_sidebar_targets(page, state, force_print=False):
    """Maps the course and returns one URL per core content type it contains."""
    targets = {}

    outline = course_map.get_detailed_course_map(page)
    course_title = context.get_robust_course_name(page)

    # Kept on the session state so the module of the open item can be named
    # without rebuilding the outline on every page.
    state.course_map = outline

    if force_print or not state.mapped:
        course_map.print_course_map(outline, course_title)
        state.mapped = True

    found_types = set()
    for _module, lessons in outline.items():
        for _title, item_type, href, _duration in lessons:
            if item_type in CORE_TYPES:
                found_types.add(item_type)
                targets.setdefault(item_type, href)

    # Only the types the course actually contains can ever be verified, so the
    # exit condition is narrowed to those.
    state.required_types = found_types
    logs.step(f"exit targets: {sorted(found_types)}")

    return targets


def missing_types(state):
    """Returns the core types still unverified, in a stable order."""
    return sorted(state.required_types - state.discovered_types)


def auto_hop_smart(page, selectors, state):
    """Navigates via the sidebar towards a content type not yet verified."""
    logs.step("analysing the sidebar for missing content types")

    targets = get_sidebar_targets(page, state)
    missing = missing_types(state)

    if not missing:
        logs.ok("all core content types verified")
        return False

    for item_type in missing:
        if item_type in targets:
            url = targets[item_type]
            logs.nav(f"{item_type} found in the sidebar; jumping to {url}")
            page.goto(urls.absolute_url(url))
            return True

    logs.step(f"missing {missing}; no sidebar path, falling back to the Next button")
    return auto_hop_next(page, selectors)


def _course_tab(browser_context):
    """Returns the first open tab on the learning platform, or ``None``."""
    for candidate in browser_context.pages:
        if urls.PLATFORM_HOST in candidate.url:
            return candidate
    return None


def start_dynamic_observation(cdp_url=config.CDP_URL):
    """Watches the attached browser and verifies selectors on every item opened."""
    logs.step("observation active; smart-hop navigation engaged")
    logs.step("press Ctrl+C to disconnect")

    state = ObservationState(selectors=schema.verified_selectors())
    try:
        with session.BrowserSession(cdp_url) as browser_session:
            _observe(browser_session.context, state)
    except KeyboardInterrupt:
        logs.interrupted("Session terminated by user.")
    except Exception as exc:
        logs.error(f"Session error: {exc}")
    finally:
        logs.step("discovery process stopped")


def _observe(browser_context, state):
    """Polls the course tab, probing selectors whenever the item changes."""
    last_url = ""
    while True:
        # ``_course_tab`` rather than ``BrowserSession.find_course_page``: that
        # one raises the tab to the front, which at this poll interval would
        # take the user's focus away every two seconds.
        page = _course_tab(browser_context)

        if page:
            if not state.mapped:
                logs.step("performing the initial course mapping")
                get_sidebar_targets(page, state, force_print=True)

            current_url = page.url.split("?")[0].split("#")[0]
            if current_url != last_url:
                # Give the item a moment to render before probing it.
                time.sleep(4)
                selectors = probing.discover_selectors(page, state)
                last_url = current_url

                if not auto_hop_smart(page, selectors, state):
                    logs.ok("discovery objective achieved; closing the session")
                    return

                if state.required_types and not missing_types(state):
                    logs.ok("all identified course types verified; closing the session")
                    return
        else:
            if last_url != WAITING:
                logs.pending("waiting for a course tab")
                last_url = WAITING

        time.sleep(2)
