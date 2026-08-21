"""Classification rules: page type, sidebar row type, filler, duration.

Everything here decides *what a thing is* from text, a URL or a title. No
element is clicked and no file is touched, so each rule is directly testable.
"""

import re

from .. import detection

# Core types we aim to verify, but we'll filter this based on actual course content
CORE_TYPES = ["VIDEO", "READING", "QUIZ", "ASSIGNMENT", "LAB"]

# Items to ignore when marking types as 'discovered'
FILLER_KEYWORDS = [
    "how was the course",
    "course farewell",
    "please tell us about yourself",
    "survey",
]


def _segment_markers():
    """Groups the classifier's segment table into (label, path fragments) pairs.

    Derived rather than restated: the hand-written copy that used to live here
    had drifted -- it looked for "/discussion/" where Coursera's segment is
    "discussionPrompt", and knew nothing of the assignment segments -- so those
    items scanned as UNKNOWN and every schema category was tried against them.
    """
    grouped = {}
    for segment, label in detection.URL_SEGMENT_LABELS.items():
        # The map's finer PEER_REVIEW label has no separate selector set here.
        if label == detection.PEER_REVIEW:
            label = "ASSIGNMENT"
        grouped.setdefault(label, []).append(f"/{segment.lower()}/")
    # Course wrap-ups carry no item selectors of their own; the scanner only
    # needs to recognise one to know it has reached the end of a module.
    grouped["WRAPUP"] = ["/wrapup/"]
    return tuple((label, tuple(segments)) for label, segments in grouped.items())


# Ordered because the first match wins; within a label the fragments are
# alternatives, and no two of them can match the same path.
URL_SEGMENT_MARKERS = _segment_markers()

TITLE_MARKERS = (
    ("QUIZ", ("quiz", "exam")),
    ("ASSIGNMENT", ("assignment", "review your peers")),
    ("DISCUSSION", ("forum",)),
    ("WRAPUP", ("congratulations", "course farewell")),
    ("SURVEY", ("survey", "how was the course")),
)


def detect_page_type(page):
    """Returns the coarse page type used to pick which selector categories to scan."""
    url = page.url.lower()
    title = page.title().lower()

    # Core technical markers based on the URL segment, tried before the title.
    for page_type, segments in URL_SEGMENT_MARKERS:
        if any(segment in url for segment in segments):
            return page_type

    # Secondary markers based on the page title.
    for page_type, phrases in TITLE_MARKERS:
        if any(phrase in title for phrase in phrases):
            return page_type

    # Check for filler content last
    for kw in FILLER_KEYWORDS:
        if kw in title:
            return "FILLER"

    return "UNKNOWN"


DURATION_PATTERN = re.compile(r"(\d+\s*h\s*\d+\s*min|\d+\s*h|\d+\s*min|\d+\s*m)")

# Substring -> map label, tried in this order. Order is load-bearing:
# "review your peers" must precede "peer", and "assignment" must precede "quiz"
# or peer-graded items get mislabeled QUIZ.
SUBTEXT_TYPES = (
    ("review your peers", "REVIEW_PEERS"),
    ("peer", "PEER_REVIEW"),
    ("honors", "PEER_REVIEW"),
    ("video", "VIDEO"),
    ("reading", "READING"),
    ("assignment", "ASSIGNMENT"),
    ("quiz", "QUIZ"),
    ("plugin", "LAB"),
    ("lab", "LAB"),
    # Subtext of an AI roleplay practice item, served under the /coach/ segment.
    ("dialogue", "LAB"),
    ("discussion", "DISCUSSION"),
)

# A filler keyword only overrides a real content type for an explicit survey --
# a "Congratulations Video" is still a video and must count for discovery.
FILLER_OVERRIDES = ("survey", "how was the course")


def classify_sidebar_row(subtext, label, href):
    """Classifies one sidebar row from its subtext, aria-label, then URL.

    The visible subtext is consulted first because it distinguishes graded from
    ungraded variants that share a URL path segment; the segment is the
    unlocalized fallback when the row has not finished rendering.
    """
    haystack = f"{subtext} {label}".lower()
    for needle, item_type in SUBTEXT_TYPES:
        if needle in haystack:
            return item_type
    return detection.label_from_url(href)


def apply_filler_override(item_type, text, subtext):
    """Downgrades a row to ``FILLER`` when its *title* marks it a survey.

    Only the title is checked for the survey marker, which is the long-standing
    behaviour: a row whose subtext alone says "survey" keeps its content type.
    """
    blob = f"{text} {subtext}".lower()
    if not any(keyword in blob for keyword in FILLER_KEYWORDS):
        return item_type
    if any(marker in text.lower() for marker in FILLER_OVERRIDES):
        return "FILLER"
    return item_type


def parse_duration(full_text):
    """Pulls a human duration ("15 min", "1h 10min") out of a row, or ``""``."""
    match = DURATION_PATTERN.search((full_text or "").lower())
    return match.group(0).strip() if match else ""
