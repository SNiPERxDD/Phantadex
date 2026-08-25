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
# Polls the loop may spend without the open item changing. At the two-second
# poll below that is about a minute -- far longer than any hop the session
# makes itself, and short enough that a wedged pass says so rather than
# sitting there.
STILL_TICKS = 30


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
    # exit condition is narrowed to those. An empty read is not such a course:
    # a sidebar that has not rendered, or one virtualized down to the open
    # module, yields nothing and used to empty the exit condition outright --
    # which closed the pass after a single page and called it a success.
    if found_types:
        state.required_types = found_types
        logs.step(f"exit targets: {sorted(found_types)}")
    else:
        logs.warn("the sidebar read as empty; keeping the exit targets already known")

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
        if item_type in targets and item_type not in state.attempted_types:
            url = targets[item_type]
            state.attempted_types.add(item_type)
            logs.nav(f"{item_type} found in the sidebar; jumping to {url}")
            page.goto(urls.absolute_url(url))
            return True

    logs.step(f"missing {missing}; no unvisited sidebar path, trying the Next button")
    return auto_hop_next(page, selectors)


def _course_tab(browser_context):
    """Returns the first open tab on the learning platform, or ``None``."""
    for candidate in browser_context.pages:
        if urls.PLATFORM_HOST in candidate.url:
            return candidate
    return None


def start_dynamic_observation(cdp_url=config.CDP_URL, course_url=""):
    """Watches the attached browser and verifies selectors on every item opened.

    A ``course_url`` opens that item before the pass begins, which is how a
    course is named when several tabs are open.
    """
    logs.step("observation active; smart-hop navigation engaged")
    logs.step("press Ctrl+C to disconnect")

    state = ObservationState(selectors=schema.verified_selectors())
    try:
        with session.BrowserSession(cdp_url) as browser_session:
            if course_url:
                browser_session.find_course_page(course_url)
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
    still_ticks = 0
    while True:
        # ``_course_tab`` rather than ``BrowserSession.find_course_page``: that
        # one raises the tab to the front, which at this poll interval would
        # take the user's focus away every two seconds.
        page = _course_tab(browser_context)

        if page is None:
            if last_url != WAITING:
                logs.pending("waiting for a course tab")
                last_url = WAITING
            time.sleep(2)
            continue

        current_url = last_url
        try:
            if not state.mapped:
                logs.step("performing the initial course mapping")
                get_sidebar_targets(page, state, force_print=True)

            current_url = page.url.split("?")[0].split("#")[0]
            still_ticks = 0 if current_url != last_url else still_ticks + 1
            if still_ticks >= STILL_TICKS:
                # ``auto_hop_next`` reports the click, not the navigation, so a
                # Next that is swallowed -- or absent because this is the last
                # item -- reads as a successful hop that goes nowhere. The poll
                # then waits for a URL change that will never come, in silence,
                # forever. This is the same shape the ``attempted_types`` trap
                # closed on the goto path; the fallback branch needed its own.
                logs.warn(f"nothing moved in {still_ticks} passes; the item is not advancing")
                _report_close(state)
                return
            if current_url != last_url:
                # Give the item a moment to render before probing it.
                time.sleep(4)
                selectors = probing.discover_selectors(page, state)
                last_url = current_url

                if not auto_hop_smart(page, selectors, state):
                    _report_close(state)
                    return

                if state.required_types and not missing_types(state):
                    logs.ok("all identified course types verified; closing the session")
                    return
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            # One unreadable page -- a tab mid-navigation, a frame detached
            # under the probe -- used to end the whole run. The pass is
            # resumable by design, so the poll survives it and moves on.
            logs.warn(f"skipping an item that could not be read: {exc}")
            log.debug("Observation tick failed", exc_info=True)
            # Treated as seen, so the poll does not retry the same failure
            # every two seconds for the rest of the session.
            last_url = current_url

        time.sleep(2)


def _report_close(state):
    """Says why the run is stopping, naming any type left unverified."""
    remaining = missing_types(state)
    if remaining:
        logs.warn(f"no route left to {remaining}; closing the session")
    else:
        logs.ok("discovery objective achieved; closing the session")
