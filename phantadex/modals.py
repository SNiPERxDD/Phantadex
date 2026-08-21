"""Dismissal of the interstitial dialogs Coursera injects mid-item.

Each rule is evaluated independently. Previously all three checks shared a
single ``try/except: pass``, so a Playwright strict-mode violation on the first
check silently skipped Honor Code *and* Reflect handling for the whole run.
"""

import time

from . import logs

log = logs.get_logger("modals")

DIALOG_SELECTOR = "[role='dialog'], [aria-modal='true']"

# Headings already reported as unclearable, so the warning is not repeated on
# every tick the modal stays on screen.
_REPORTED_STALLS = set()

# Scope for the prompts Coursera injects into a playing video. A live capture of
# a Poll showed it inside `role="dialog" aria-modal="true"` (`cds-Dialog-dialog`)
# nested in the player's `rc-VideoQuiz` container; the container is included so
# an interrupt that renders without the dialog wrapper is still reachable.
IN_VIDEO_SCOPE = "[role='dialog'], [aria-modal='true'], .rc-VideoQuiz"

# (heading text, heading selector, button labels to try, message, scope)
#
# `scope` is the container the rule is confined to. ``None`` means the rule may
# fall back to the page, which only the full-page interstitials need. Every
# in-video prompt is scoped, because `has_text` is a case-insensitive substring
# match: unscoped, an ordinary page heading containing "Poll" or "Question"
# ("Question 1 of 5" on a graded quiz) satisfied the rule and sent it looking
# for a dismiss button.
MODAL_RULES = (
    ("Coursera Honor Code", "h1, h2", ("Continue",), "Honor Code accepted", None),
    # Declining controls only. The rule used to offer `Continue` and `Submit`,
    # either of which answers a survey about the user in their name and without
    # them present. If none of these is on screen the survey is left alone and
    # reported, which is the correct outcome: it is theirs to answer.
    (
        "Demographics Survey",
        "h1, h2",
        ("Skip", "Skip for now", "No thanks", "Not now", "Maybe later"),
        "demographics survey declined",
        None,
    ),
    # Coursera's daily-goal congratulation. It renders over the item and covers
    # the next-item control, so the click that should advance the run lands on
    # the overlay instead. Confined to the dialog: the sidebar carries a
    # "Today's goals" panel whose text would otherwise satisfy a page-wide rule.
    (
        "completed today's goals",
        "h1, h2, h3",
        ("Continue learning", "Close"),
        "daily-goal dialog dismissed",
        DIALOG_SELECTOR,
    ),
    ("Reflect", "h1, h2, h3", ("Continue",), "video interrupt dismissed", IN_VIDEO_SCOPE),
    ("Poll", "h2, h3", ("Skip", "Continue"), "poll skipped", IN_VIDEO_SCOPE),
    # The in-video comprehension check. `Submit` stays disabled until the
    # learner answers, so `Skip` is the only way past it.
    ("Question", "h2, h3", ("Skip",), "in-video question skipped", IN_VIDEO_SCOPE),
)


def dismiss_all(page):
    """Clears every known modal currently on screen. Returns the count handled."""
    handled = 0
    for heading, heading_selector, button_labels, message, scope in MODAL_RULES:
        try:
            if _dismiss_one(page, heading, heading_selector, button_labels, message, scope):
                handled += 1
        except Exception as exc:
            log.debug("Modal rule %r failed: %s", heading, exc)
    return handled


def _dismiss_one(page, heading, heading_selector, button_labels, message, scope=None):
    """Handles a single modal rule. Returns True when a button was clicked."""
    root = page.locator(scope or DIALOG_SELECTOR, has_text=heading).first
    if root.count() == 0:
        if scope:
            return False
        # Honor Code and the demographics survey render as full-page
        # interstitials rather than dialogs, so those two rules fall back to the
        # page. The button search below is still narrowed to the heading's own
        # container, so the click cannot land elsewhere on the page.
        root = page

    # `.first` matters: a bare multi-match locator raises under strict mode.
    header = root.locator(heading_selector, has_text=heading).first
    if header.count() == 0 or not header.is_visible():
        return False

    if root is page:
        root = _nearest_button_container(header, page)

    for label in button_labels:
        # Scoped to `root` so a scoped rule can never reach a same-named button
        # elsewhere on the page.
        button = root.locator(f"button:has-text('{label}')").first
        try:
            if button.count() > 0 and button.is_visible():
                # Lazy import avoids the interaction -> modals module cycle.
                from . import interaction

                if not interaction.click(page, button, force=True, reaction_range=(0.2, 0.5)):
                    continue
                # Logged after the click, not before: an earlier version
                # announced the dismissal up front, so the log claimed success
                # even when no button matched.
                logs.step(message)
                time.sleep(1)
                return True
        except Exception as exc:
            log.debug("Could not click %r on %r modal: %s", label, heading, exc)
    log.debug("Modal %r visible but no dismiss button matched %s", heading, button_labels)
    _report_stall(heading, button_labels)
    return False


def _report_stall(heading, button_labels):
    """Surfaces a modal the run will not clear, once per heading per process.

    Without this the only trace was a debug line, so a dialog that the run
    deliberately declines to answer -- or one whose controls have been renamed
    -- looked identical to no dialog at all.
    """
    if heading in _REPORTED_STALLS:
        return
    _REPORTED_STALLS.add(heading)
    labels = ", ".join(repr(label) for label in button_labels)
    logs.warn(f"'{heading}' is on screen and none of {labels} is available; it is yours to clear")


def _nearest_button_container(header, page):
    """Returns the closest ancestor of ``header`` that contains a button.

    A modal's dismiss button sits beside its heading, so this keeps the click
    inside the interstitial instead of anywhere on the page.
    """
    try:
        container = header.locator("xpath=ancestor::*[.//button][1]")
        if container.count() > 0:
            return container.first
    except Exception as exc:
        log.debug("Could not scope %r to its own container: %s", header, exc)
    return page
