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
        page.evaluate(_SEEK_JS, target)
    except Exception as exc:
        log.warning("Seek failed: %s", exc)
        return False

    logs.step(
        f"seek {timing.format_seconds(target)} / {timing.format_seconds(duration)} "
        f"({(target / duration) * 100:.1f}%)"
    )
    return True
