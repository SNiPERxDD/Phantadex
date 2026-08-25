"""Pure time/duration helpers. No Playwright, no I/O -- fully unit testable."""

WORDS_PER_MINUTE = 200.0
NON_TEXT_BUFFER = 1.2  # headers, images, code blocks
MIN_READ_MINUTES = 0.5

# Share of a narrated reading's audio the run stays for. The platform hands an
# item off a few seconds after it completes, so leaving at the tail is also
# what keeps our move and its move from racing for the same tab.
NARRATION_SHARE = 0.9
# Added to the word-count estimate. Arriving, finding the body and leaving are
# not reading, but they are time spent on the item.
READING_SETTLE_MINUTES = 0.5


def parse_time_to_seconds(time_str):
    """Parses ``M:SS`` or ``H:MM:SS`` into seconds. Returns 0 when unparseable."""
    if not time_str:
        return 0
    try:
        parts = [int(part) for part in str(time_str).strip().split(":")]
    except (ValueError, AttributeError):
        return 0
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


def format_seconds(seconds):
    """Formats seconds as ``H:MM:SS`` or ``M:SS``."""
    whole = max(0, int(round(seconds)))
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def parse_skip_bound(raw_value, duration_seconds):
    """Converts one range bound (``'98%'``, ``'01:30'``, ``'90s'``, ``'90'``) to seconds."""
    value = str(raw_value).strip()
    if not value:
        raise ValueError("empty range bound")

    if value.endswith("%"):
        return duration_seconds * (float(value[:-1].strip()) / 100.0)

    if ":" in value:
        parsed = parse_time_to_seconds(value)
        if parsed <= 0 and value not in {"0:00", "00:00", "0:0"}:
            raise ValueError(f"invalid timestamp bound: {value}")
        return float(parsed)

    if value.lower().endswith("s"):
        return float(value[:-1].strip())

    return float(value)


def parse_skip_range(range_text, duration_seconds):
    """Parses ``'START-END'`` into a clamped ``(start, end)`` second pair.

    Returns ``None`` for an empty/disabled range so callers can branch without
    unpacking a missing tuple.
    """
    if not range_text or not str(range_text).strip():
        return None

    parts = [part.strip() for part in str(range_text).split("-", 1)]
    if len(parts) != 2 or not all(parts):
        raise ValueError("expected START-END format")

    # '97.5-98.5%' -- the trailing unit applies to both bounds.
    if any(part.endswith("%") for part in parts):
        parts = [part if part.endswith("%") else f"{part}%" for part in parts]

    start = parse_skip_bound(parts[0], duration_seconds)
    end = parse_skip_bound(parts[1], duration_seconds)
    if start > end:
        start, end = end, start

    if duration_seconds > 0:
        start = max(0.0, min(start, duration_seconds))
        end = max(0.0, min(end, duration_seconds))
    return start, end


def estimate_read_minutes(text_content):
    """Estimates reading minutes for a body of text at ~200 WPM."""
    if not text_content:
        return 1.0
    word_count = len(text_content.split())
    minutes = (word_count / WORDS_PER_MINUTE) * NON_TEXT_BUFFER
    return max(MIN_READ_MINUTES, minutes)


def dwell_minutes(estimate_min, listed_min, narration_seconds=0.0, floor_min=1.0):
    """Returns how long a reading is worth staying on.

    Three sources, ordered by how much each of them actually knows.

    A narration player carries the real length of the item and wins outright,
    uncapped by the listed duration: the audio is the thing being timed, and a
    listed figure that disagrees with it is the rounder guess. Measured against
    a live course, a 1179-word reading listed at 10 minutes narrates in 7:48.

    Without narration the word count is all there is, plus a constant for the
    part of an item that is not reading. It used to be doubled instead --
    ``min(listed, estimate * 2)`` -- which is where a reading anyone finishes in
    five minutes became a nine-minute stare. The listed duration still caps it,
    so a course that says ten minutes is never exceeded on a guess.

    The floor never exceeds ``listed_min``: a caller asking for a deliberately
    brief dwell gets one rather than being rounded up.
    """
    floor = min(floor_min, listed_min)
    if narration_seconds and narration_seconds > 0:
        return max(narration_seconds / 60.0 * NARRATION_SHARE, floor)
    return max(min(listed_min, estimate_min + READING_SETTLE_MINUTES), floor)
