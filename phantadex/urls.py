"""Coursera URL normalization and safe item identity comparison.

The previous implementation compared URLs with raw substring containment
(``a in b or b in a``), which happily matched unrelated items whose slugs shared
a prefix. Identity here is anchored on the opaque item id Coursera puts at
``/learn/<course>/<type>/<item-id>/<slug>``.
"""

import urllib.parse

PLATFORM_HOST = "coursera.org"
PLATFORM_ORIGIN = f"https://www.{PLATFORM_HOST}"

# Only these path segments carry an opaque item id. Reading segment 4 of any
# five-segment ``/learn/`` path treated ``/learn/<course>/home/week/1`` as an
# item whose id was "week", so every week page of every course compared equal.
# ``tests/test_urls.py`` keeps this in sync with ``detection.URL_SEGMENT_LABELS``.
ITEM_TYPE_SEGMENTS = frozenset(
    {
        "lecture",
        "supplement",
        "discussionprompt",
        "ungradedwidget",
        "ungradedlti",
        "ungradedlab",
        "coach",
        "quiz",
        "exam",
        "assignment",
        "assignment-submission",
        "programming",
        "peer",
        "wrapup",
    }
)


def normalize_path(url_or_path):
    """Reduces an absolute or relative Coursera URL to a bare path.

    Strips scheme, host, query string, fragment and any trailing slash.
    """
    if not url_or_path:
        return ""

    parsed = urllib.parse.urlparse(url_or_path)
    if parsed.scheme or parsed.netloc:
        path = parsed.path
    else:
        path = url_or_path.split("?", 1)[0].split("#", 1)[0]

    if not path.startswith("/"):
        path = f"/{path}"
    normalized = path.rstrip("/")
    return normalized or "/"


def absolute_url(url_or_path):
    """Returns a fully qualified Coursera URL for a path or passthrough URL."""
    if not url_or_path:
        return ""
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        return url_or_path
    return f"{PLATFORM_ORIGIN}{normalize_path(url_or_path)}"


def item_id(url_or_path):
    """Extracts Coursera's opaque per-item id, or ``""`` when absent.

    ``/learn/ml-course/lecture/AbCd3/welcome`` -> ``AbCd3``
    """
    parts = normalize_path(url_or_path).split("/")
    # ['', 'learn', '<course>', '<type>', '<item-id>', '<slug>'...]
    if len(parts) >= 5 and parts[1] == "learn" and parts[3].lower() in ITEM_TYPE_SEGMENTS:
        return parts[4]
    return ""


def course_slug(url_or_path):
    """Extracts the stable course slug from ``/learn/<course>/...``."""
    parts = normalize_path(url_or_path).split("/")
    if len(parts) >= 3 and parts[1] == "learn":
        return parts[2]
    return ""


def same_item(left, right):
    """Reports whether two URLs address the same course item.

    Exact path match wins; otherwise both sides must expose the *same* item id.
    Never falls back to substring containment.
    """
    left_path = normalize_path(left)
    right_path = normalize_path(right)
    if not left_path or not right_path:
        return False
    if left_path == right_path:
        return True

    left_id = item_id(left_path)
    right_id = item_id(right_path)
    if left_id and right_id:
        # The course slug is part of the identity: item ids are only unique
        # within a course, so two courses' items could otherwise compare equal.
        return left_id == right_id and course_slug(left_path) == course_slug(right_path)

    # Relative ledger paths vs absolute page URLs: compare on segment boundaries.
    # ``normalize_path`` always prefixes "/", so the leading empty segment is
    # dropped before the tail comparison -- keeping it made this branch
    # unreachable for every input of unequal length.
    left_parts = [part for part in left_path.split("/") if part]
    right_parts = [part for part in right_path.split("/") if part]
    shorter, longer = sorted((left_parts, right_parts), key=len)
    return len(shorter) >= 3 and longer[-len(shorter) :] == shorter
