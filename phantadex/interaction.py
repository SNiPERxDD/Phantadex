"""Mouse and scroll primitives, plus the paced reading session.

Coursera renders course items inside an inner overflow container, so
``window.scrollY`` never moves and ``document.body.offsetHeight`` reports ``0``.
Everything here therefore works against the element that actually scrolls,
resolved at runtime, rather than against the window.
"""

import logging
import random
import time

from . import jitter, logs, modals, timing, urls

log = logs.get_logger("interaction")

DEFAULT_SCROLL_ANCHOR = (500, 300)
# Bound on a single click attempt. Without actionability forced, Playwright
# would otherwise wait out its 30s default on an element that never becomes
# clickable, stalling the whole run on one control.
CLICK_TIMEOUT_MS = 5000
# Shortest dwell a content-bearing page gets, unless the caller asks for less.
MINIMUM_DWELL_MINUTES = 1.0
VIEWPORT_MARGIN = 120
BOTTOM_TOLERANCE = 40
# Gap between scroll passes during a reading dwell.
READING_PAUSE_RANGE = (2.5, 6.0)

# Nearest overflowing ancestor of the reading body. A page-wide search can
# select the independent course sidebar when the reading itself is short.
_CONTENT_SCROLLER_JS = """el => {
    for (let node = el; node; node = node.parentElement) {
        const overflow = getComputedStyle(node).overflowY;
        if (node.scrollHeight > node.clientHeight + 80 &&
                (overflow === 'auto' || overflow === 'scroll')) {
            return node;
        }
    }
    return null;
}"""

_METRICS_JS = """el => {
    const box = el.getBoundingClientRect();
    return {
        top: el.scrollTop,
        max: Math.max(0, el.scrollHeight - el.clientHeight),
        x: box.left + Math.min(el.clientWidth, box.width) / 2,
        y: box.top + Math.min(el.clientHeight, box.height) / 2,
    };
}"""


def move(page, target_x, target_y):
    """Moves the cursor to a point over several intermediate steps."""
    page.mouse.move(target_x, target_y, steps=random.randint(5, 15))


def scroll(page, amount, x_pos, y_pos):
    """Scrolls by ``amount`` pixels in small ticks under the given cursor point."""
    if not amount:
        return
    move(page, x_pos, y_pos)
    remaining = amount
    while abs(remaining) > 0:
        chunk = random.randint(20, 60) if abs(remaining) > 60 else abs(remaining)
        if remaining < 0:
            chunk = -chunk
        page.mouse.wheel(0, chunk)
        remaining -= chunk
        time.sleep(random.uniform(0.01, 0.05))


def describe(locator):
    """Returns a short label for a locator, for logging only.

    Never raises: it runs on the debug path, where an element that has already
    gone away must not turn into a failed action.
    """
    try:
        text = " ".join((locator.inner_text() or "").split())
        if not text:
            text = " ".join((locator.get_attribute("aria-label") or "").split())
        return text[:60] if text else "unlabelled element"
    except Exception:
        return "unreadable element"


def click(page, locator, force=False, reaction_range=(0.4, 0.9), timeout=CLICK_TIMEOUT_MS):
    """Moves to a locator and clicks it. Returns False if it was not clickable.

    ``force`` skips Playwright's actionability checks, including whether the
    element actually receives pointer events. It defaults to False so a click
    landing on an overlay raises instead of reporting success; callers that must
    click through an animating container pass it explicitly.

    The click point carries a few pixels of jitter. It is passed as ``position``
    rather than only moved to: without it the cursor visited the offset point and
    the click still landed on the element's exact centre.
    """
    try:
        if not locator.is_visible():
            locator.wait_for(state="visible", timeout=timeout)
        box = locator.bounding_box()
        if not box:
            log.debug("Locator has no bounding box; cannot click")
            return False

        offset_x = box["width"] / 2 + random.randint(-5, 5)
        offset_y = box["height"] / 2 + random.randint(-5, 5)
        # Keep the jitter inside the element; a small control can be narrower
        # than the offset range.
        offset_x = min(max(offset_x, 1.0), max(1.0, box["width"] - 1))
        offset_y = min(max(offset_y, 1.0), max(1.0, box["height"] - 1))
        position = {"x": offset_x, "y": offset_y}

        # Read the label before clicking: the control is often replaced by the
        # click that follows, leaving nothing to describe afterwards.
        label = describe(locator) if log.isEnabledFor(logging.DEBUG) else ""

        move(page, box["x"] + offset_x, box["y"] + offset_y)
        locator.hover(force=force, position=position, timeout=timeout)
        time.sleep(jitter.duration(*reaction_range))
        locator.click(force=force, position=position, timeout=timeout)
        log.debug(
            "clicked %s at (%.0f, %.0f)%s",
            label,
            box["x"] + offset_x,
            box["y"] + offset_y,
            " (forced)" if force else "",
        )
        return True
    except Exception as exc:
        log.debug("Click failed: %s", exc)
        return False


# --------------------------------------------------------------- scrolling


def find_scroller(_page, content):
    """Returns the nearest scrollable ancestor of the reading content."""
    try:
        handle = content.evaluate_handle(_CONTENT_SCROLLER_JS)
        element = handle.as_element()
        if element is None:
            log.debug("Reading content has no scrollable ancestor")
        return element
    except Exception as exc:
        log.debug("Scroller lookup failed: %s", exc)
        return None


def scroll_metrics(scroller):
    """Reads scroll position, travel, and the viewport point over the scroller."""
    if scroller is None:
        return None
    try:
        return scroller.evaluate(_METRICS_JS)
    except Exception as exc:
        log.debug("Scroll metrics read failed: %s", exc)
        return None


def _clamp_to_viewport(page, x_pos, y_pos):
    """Keeps the cursor inside the window; off-screen points swallow wheel events."""
    try:
        width, height = page.evaluate("() => [window.innerWidth, window.innerHeight]")
    except Exception as exc:
        log.debug("Viewport read failed: %s", exc)
        return x_pos, y_pos
    x_pos = min(max(x_pos, VIEWPORT_MARGIN), max(VIEWPORT_MARGIN, width - VIEWPORT_MARGIN))
    y_pos = min(max(y_pos, VIEWPORT_MARGIN), max(VIEWPORT_MARGIN, height - VIEWPORT_MARGIN))
    return x_pos, y_pos


def scroll_by(page, scroller, delta, x_pos, y_pos):
    """Scrolls and confirms it moved, falling back to ``scrollTop`` if it did not.

    The fallback is a correctness measure, not a rotation of techniques: some
    containers do not accept synthesised wheel events at all, and without this
    the reading session silently sits still.
    """
    if not delta:
        # Nothing to scroll: the bottom is reached, or there is no overflow at
        # all. Falling through logged "wheel produced no movement" and issued a
        # `scrollTop += 0` round-trip on every remaining tick of the session.
        return scroll_metrics(scroller)

    before = scroll_metrics(scroller)
    x_pos, y_pos = _clamp_to_viewport(page, x_pos, y_pos)
    scroll(page, delta, x_pos, y_pos)

    after = scroll_metrics(scroller)
    if before is None or after is None or after["top"] != before["top"]:
        if before is not None and after is not None:
            log.debug(
                "scrolled %+dpx: %d -> %d of %d", delta, before["top"], after["top"], after["max"]
            )
        return after
    if delta > 0 and before["top"] >= before["max"] - BOTTOM_TOLERANCE:
        return after  # genuinely at the bottom; nothing to correct
    if delta < 0 and before["top"] <= BOTTOM_TOLERANCE:
        return after  # genuinely at the top

    log.debug("Wheel produced no movement; using scrollTop fallback")
    try:
        scroller.evaluate("(el, d) => { el.scrollTop += d; }", delta)
    except Exception as exc:
        log.debug("scrollTop fallback failed: %s", exc)
    return scroll_metrics(scroller)


def _next_delta(metrics):
    """Chooses a forward scroll delta, stopping when the bottom is reached."""
    if metrics is None:
        return 0
    if metrics["max"] <= 0:
        return 0
    if metrics["top"] >= metrics["max"] - BOTTOM_TOLERANCE:
        return 0
    return random.randint(100, 400)


# ------------------------------------------------------------------ audio

_SILENCE_JS = """
() => {
    const media = Array.from(document.querySelectorAll('audio, video'));
    let silenced = 0;
    for (const element of media) {
        if (element.muted && element.volume === 0) continue;
        element.muted = true;
        element.volume = 0;
        silenced += 1;
    }
    return silenced;
}
"""


# Installed once per document, before the page's own scripts run. ``silence_media``
# only reaches an element on the tick after it appears, which is a moment or two
# of audible autoplay; this mutes at 'loadstart', before a frame is decoded, and
# again on any attempt to raise the volume afterwards.
#
# Scoped to the platform host: the guard is installed on the browser context,
# which is the user's own default profile, and muting the tabs they open beside
# a run would be a side effect of automating something else entirely.
_MEDIA_GUARD_JS = (
    """
(() => {
    if (window.__phantadexMediaGuard) return false;
    if (!location.hostname.endsWith('"""
    + urls.PLATFORM_HOST
    + """')) return false;
    const silence = (element) => {
        if (!(element instanceof HTMLMediaElement)) return;
        if (element.muted && element.volume === 0) return;
        element.muted = true;
        element.volume = 0;
    };
    const events = ['loadstart', 'loadedmetadata', 'canplay', 'play', 'playing',
                    'volumechange'];
    for (const type of events) {
        document.addEventListener(type, (event) => silence(event.target), true);
    }
    // Programmatic playback can start without any of those events having been
    // seen yet, so the entry point itself is covered too.
    const play = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function () {
        silence(this);
        return play.apply(this, arguments);
    };
    document.querySelectorAll('audio, video').forEach(silence);
    window.__phantadexMediaGuard = true;
    return true;
})();
"""
)


def install_media_guard(context):
    """Arms the mute guard in every document the context opens from now on."""
    try:
        context.add_init_script(script=_MEDIA_GUARD_JS)
        return True
    except Exception as exc:
        log.debug("Could not install the media guard: %s", exc)
        return False


def arm_media_guard(page):
    """Installs the mute guard into a document that is already open.

    Returns True when this call armed it. A document that already carries the
    guard, and any page off the platform, report False.
    """
    try:
        return bool(page.evaluate(_MEDIA_GUARD_JS))
    except Exception as exc:
        log.debug("Could not arm the media guard: %s", exc)
        return False


def silence_media(page):
    """Mutes every audio and video element on the page.

    Returns the number of elements that were still audible. Some readings embed
    a narration player that autoplays unmuted, which the video-only mute path
    never reached: it queries ``video`` alone, and a reading has no player for
    the handler to mute in the first place. Elements already silent are left
    untouched so a repeated call reports nothing.
    """
    try:
        silenced = page.evaluate(_SILENCE_JS)
    except Exception as exc:
        log.debug("Silencing media failed: %s", exc)
        return 0
    if silenced:
        logs.step(f"muted {silenced} autoplaying media element(s)")
    return silenced


# ---------------------------------------------------------- reading session


def reading_session(page, metadata_wait_min):
    """Scrolls through a reading for a word-count-derived duration.

    Returns ``"COMPLETED"``, ``"NAVIGATED"``, or ``"INTERRUPTED"``.
    """
    try:
        start_url = page.url
        content = page.locator("div.rc-CML, main, div[role='main']").first
        has_content = content.count() > 0
        duration_sec, anchor_x, anchor_y = _session_geometry(
            page, content, has_content, metadata_wait_min
        )

        scroller = find_scroller(page, content) if has_content else None
        if scroller is None:
            logs.warn("No scrollable container found; dwelling without scrolling.")

        start_time = time.time()
        while (time.time() - start_time) < duration_sec:
            if not urls.same_item(page.url, start_url):
                logs.bar_done()
                logs.nav("page changed; ending reading session")
                return "NAVIGATED"

            # Clear modals before scrolling so wheel events reach the content.
            modals.dismiss_all(page)
            # Re-checked every pass: a narration player can be lazily inserted
            # part-way through the dwell, after the entry mute has run.
            silence_media(page)
            if not urls.same_item(page.url, start_url):
                logs.bar_done()
                logs.nav("page changed; ending reading session")
                return "NAVIGATED"

            metrics = None
            if scroller is not None:
                metrics = scroll_metrics(scroller)
                target_x, target_y = _scroll_target(metrics, anchor_x, anchor_y)
                metrics = scroll_by(page, scroller, _next_delta(metrics), target_x, target_y)

            if not urls.same_item(page.url, start_url):
                logs.bar_done()
                logs.nav("page changed; ending reading session")
                return "NAVIGATED"

            time.sleep(jitter.duration(*READING_PAUSE_RANGE))
            elapsed = time.time() - start_time
            logs.bar(elapsed / duration_sec, _caption(metrics, duration_sec - elapsed))

        logs.bar_done()
        logs.ok("reading session complete")
        return "COMPLETED"
    except Exception as exc:
        logs.bar_done()
        log.warning("Reading session interrupted: %s", exc)
        return "INTERRUPTED"


def _caption(metrics, seconds_left):
    """Builds the progress caption, including scroll depth when it is known."""
    left = f"{int(max(0, seconds_left))}s left"
    if not metrics or metrics.get("max", 0) <= 0:
        return left
    depth = min(100, int(metrics["top"] / metrics["max"] * 100))
    return f"{left} · scrolled {depth}%"


def _session_geometry(page, content, has_content, metadata_wait_min):
    """Derives (duration_seconds, anchor_x, anchor_y) for a reading session."""
    if not has_content:
        logs.warn("Content element not found. Using default scroll area.")
        return metadata_wait_min * 60, DEFAULT_SCROLL_ANCHOR[0], DEFAULT_SCROLL_ANCHOR[1]

    text = content.inner_text()
    real_time_min = timing.estimate_read_minutes(text)
    # Cap against the metadata estimate so a two-paragraph page is not a 10m stare.
    # The floor never exceeds the caller's request, so a deliberately brief
    # dwell stays brief instead of being rounded up to a full minute.
    floor_min = min(MINIMUM_DWELL_MINUTES, metadata_wait_min)
    final_wait_min = max(min(metadata_wait_min, real_time_min * 2), floor_min)
    duration_sec = int(final_wait_min * 60)
    logs.step(
        f"dwell {final_wait_min:.1f}m (metadata {metadata_wait_min}m, {len(text.split())} words)"
    )

    box = content.bounding_box()
    if not box:
        logs.warn("Content has no geometry yet. Using default scroll area.")
        return duration_sec, DEFAULT_SCROLL_ANCHOR[0], DEFAULT_SCROLL_ANCHOR[1]
    return duration_sec, box["x"] + box["width"] / 2, box["y"] + 100


def _scroll_target(metrics, anchor_x, anchor_y):
    """Prefers a point over the live scroller, tolerating layout shifts."""
    if not metrics:
        return anchor_x, anchor_y
    return metrics["x"] + random.randint(-40, 40), metrics["y"]
