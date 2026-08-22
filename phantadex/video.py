"""HTML5 video state reads and playback control.

Player state is fetched in one ``page.evaluate`` round trip instead of the four
separate calls the old watch loop issued every second.
"""

import random
import time

from . import interaction, logs, schema, timing

log = logs.get_logger("video")

_STATE_JS = """
() => {
    const video = document.querySelector('video');
    if (!video) return null;
    return {
        duration: Number.isFinite(video.duration) ? video.duration : 0,
        currentTime: Number.isFinite(video.currentTime) ? video.currentTime : 0,
        paused: video.paused,
        ended: video.ended,
        muted: video.muted,
    };
}
"""

# Share of the player's width a candidate progress bar must span. The real
# timeline covers nearly all of it; the drag handle inside it covers under 1%.
MIN_BAR_SHARE_OF_PLAYER = 0.5

# Share of the distance to the clicked point that playback must cover for a
# progress-bar click to count as a seek. Below 1.0 because a click lands on a
# pixel, not a timestamp: on a long video one pixel is several seconds, and the
# player also plays on while the arrival is being polled for.
SEEK_ARRIVAL_FRACTION = 0.8

_MUTE_JS = """
() => {
    const video = document.querySelector('video');
    if (!video) return false;
    video.muted = true;
    video.volume = 0;
    return true;
}
"""

_PLAY_JS = """
() => {
    const video = document.querySelector('video');
    if (!video) return false;
    const result = video.play();
    if (result && typeof result.catch === 'function') result.catch(() => {});
    return true;
}
"""

_SEEK_JS = """
(targetSeconds) => {
    const video = document.querySelector('video');
    if (!video) return false;
    // Assigning currentTime makes the browser fire seeking, seeked and
    // timeupdate itself. An extra dispatched timeupdate was redundant, and
    // being script-made it was the one event on the page with isTrusted false.
    video.currentTime = targetSeconds;
    const result = video.play();
    if (result && typeof result.catch === 'function') result.catch(() => {});
    return true;
}
"""


def state(page):
    """Returns the player state dict, or ``None`` when no <video> is present."""
    try:
        return page.evaluate(_STATE_JS)
    except Exception as exc:
        log.debug("Video state read failed: %s", exc)
        return None


def wait_for_duration(page, attempts=10, interval=0.5):
    """Polls until the player reports a duration. Returns seconds, or 0.0."""
    for _ in range(attempts):
        snapshot = state(page)
        if snapshot and snapshot["duration"] > 0:
            return float(snapshot["duration"])
        time.sleep(interval)
    return 0.0


def mute_and_play(page):
    """Mutes the player and starts playback."""
    try:
        page.evaluate(_MUTE_JS)
        interaction.silence_media(page)

        # Only click an explicit "Mute" control -- clicking "Unmute" would undo it.
        mute_button = schema.first_visible(page, "video_controls", "mute_button")
        if mute_button is not None:
            label = (mute_button.get_attribute("aria-label") or "").strip().lower()
            if label == "mute":
                interaction.click(page, mute_button, reaction_range=(0.2, 0.5))
                page.evaluate(_MUTE_JS)
        logs.step("muted")
    except Exception as exc:
        log.debug("Mute sequence failed: %s", exc)

    try:
        play_button = schema.first_visible(page, "video_controls", "play_button")
        if play_button is not None:
            log.debug("Starting playback through the player's play control")
            interaction.click(page, play_button, reaction_range=(0.2, 0.5))
        else:
            log.debug("No play control found; calling video.play() directly")
            page.evaluate(_PLAY_JS)
    except Exception as exc:
        log.debug("Play failed: %s", exc)


def resume_if_paused(page):
    """Restarts playback if the player has stalled. Returns True when resumed."""
    snapshot = state(page)
    if snapshot is None or not snapshot["paused"] or snapshot["ended"]:
        return False
    try:
        page.evaluate(_PLAY_JS)
        log.debug("Resumed playback at %.1fs", snapshot["currentTime"])
        return True
    except Exception as exc:
        log.debug("Auto-resume failed: %s", exc)
        return False


def pause_if_playing(page):
    """Clicks the player's pause control when playback is running.

    Leaving a video playing while the run moves on reads as an abandoned
    session; a person stops playback before leaving the item. When no pause
    control is on screen the player is left as it is.
    """
    snapshot = state(page)
    if snapshot is None or snapshot["paused"] or snapshot["ended"]:
        return False
    button = schema.first_visible(page, "video_controls", "pause_button")
    if button is None:
        log.debug("No pause control found; leaving playback running.")
        return False
    return interaction.click(page, button, reaction_range=(0.2, 0.5))


def _reveal_controls(page):
    """Moves the cursor over the player so the auto-hiding control bar shows.

    Returns the player's box, or None if it could not be measured. The caller
    uses it to tell a timeline apart from the controls sitting on top of one.
    """
    try:
        box = page.locator("video").first.bounding_box()
    except Exception as exc:
        log.debug("Player geometry read failed: %s", exc)
        return None
    if box:
        interaction.move(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    return box


def seek_via_ui(page, fraction):
    """Seeks by clicking the progress bar at ``fraction`` of its width.

    A seek made through the control produces the pointer events a person's
    timeline drag would; assigning ``currentTime`` directly produces none. The
    click point is verified to have moved playback before True is reported, so
    a bar that matched but does not seek falls back to the direct route.
    """
    before = state(page)
    if before is None or before["duration"] <= 0:
        return False

    # The control bar auto-hides, so the cursor has to be over the player
    # before the bar can be found or measured. Revealing afterwards left the
    # lookup returning None on exactly the pages the reveal exists for, and
    # measured a box that the reveal then moved.
    player = _reveal_controls(page)
    bar = schema.first_visible(page, "video_controls", "progress_bar")
    if bar is None:
        return False
    try:
        box = bar.bounding_box()
    except Exception as exc:
        log.debug("Progress bar geometry read failed: %s", exc)
        return False
    if not box or box["width"] <= 2:
        return False
    # A timeline spans its player; the drag handle riding on it is a small
    # square. Only the width tells them apart -- both are visible, both are
    # inside the control bar, and a click at a fraction of the handle's width
    # lands in the handle. The schema no longer names the handle, but a
    # selector learned into the state file could still resolve to it.
    if player and player["width"] > 0 and box["width"] / player["width"] < MIN_BAR_SHARE_OF_PLAYER:
        log.debug(
            "Progress bar candidate is %.0fpx against a %.0fpx player; too narrow.",
            box["width"],
            player["width"],
        )
        return False

    before_position = before["currentTime"]
    destination = before["duration"] * fraction
    if destination <= before_position + 1.0:
        # Already at or past the point the click would land on; a seek here
        # cannot be told apart from playing on.
        return False
    x = min(max(box["width"] * fraction, 1.0), box["width"] - 1)
    if not interaction.click(
        page, bar, position={"x": x, "y": box["height"] / 2}, reaction_range=(0.3, 0.8)
    ):
        return False

    # Playback is already running, so "the position advanced" is true of every
    # video whether or not the click seeked anything -- it would report success
    # for a bar that matched and did nothing. What distinguishes a seek is
    # arriving near the point clicked, so the destination is what gets checked.
    span = destination - before_position
    for _ in range(6):
        time.sleep(0.5)
        snapshot = state(page)
        if snapshot is None:
            continue
        covered = (snapshot["currentTime"] - before_position) / span
        if covered >= SEEK_ARRIVAL_FRACTION:
            return True
    log.debug("Progress bar click did not land near the point clicked.")
    return False


def seek_into_range(page, range_text):
    """Seeks the player to a random point inside ``range_text``.

    Returns True when a seek was performed. No-ops when the range is disabled,
    the duration is unknown, or playback is already past the range.
    """
    if not range_text:
        return False

    duration = wait_for_duration(page)
    if duration <= 0:
        logs.warn("Seek unavailable: duration not loaded.")
        return False

    try:
        bounds = timing.parse_skip_range(range_text, duration)
    except ValueError as exc:
        log.warning("Invalid video skip range %r: %s", range_text, exc)
        return False
    if bounds is None:
        return False

    target = random.uniform(*bounds)
    snapshot = state(page)
    current = snapshot["currentTime"] if snapshot else 0.0
    if target <= current:
        logs.step(
            f"Seek skipped; position {timing.format_seconds(current)} is already at/past range."
        )
        return False

    try:
        log.debug("Seeking from %.1fs to %.1fs", current, target)
        if not seek_via_ui(page, target / duration):
            log.debug("Player control unavailable; using the direct seek")
            page.evaluate(_SEEK_JS, target)
    except Exception as exc:
        log.warning("Seek failed: %s", exc)
        return False

    logs.step(
        f"seek {timing.format_seconds(target)} / {timing.format_seconds(duration)} "
        f"({(target / duration) * 100:.1f}%)"
    )
    return True
