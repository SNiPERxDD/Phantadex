"""Classification rules: page type, sidebar row type, filler, duration.

Everything here decides *what a thing is* from text, a URL or a title. No
element is clicked and no file is touched, so each rule is directly testable.
"""

import re

from .. import detection

# Types a pass tries to reach before it calls the course covered. Narrowed
# against the course's own outline, so naming a type the course does not
# contain costs nothing and omitting one it does contain is what hurts:
# DIALOGUE and DISCUSSION were missing, which is why the ``dialogue`` selector
# category had never been probed on any course -- the pass ended before it had
# a reason to visit one.
CORE_TYPES = [
    "VIDEO",
    "READING",
    "QUIZ",
    "ASSIGNMENT",
    "LAB",
    "UNGRADED_PLUGIN",
    "DIALOGUE",
    "DISCUSSION",
]

# Titles that mark a page as filler rather than course content. Read from the
# classifier so a page this pass skips is exactly the page a run steps past;
# the hand-written copy that used to live here had already drifted from it.
FILLER_KEYWORDS = detection.SURVEY_TITLES + ("course farewell",)


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

    # Ahead of the segment, exactly as ``detection.classify`` orders it, and for
    # the same reason: a survey is served as a supplement or a widget, so the
    # segment names its container rather than what the page asks for. Reading
    # the segment first scanned every survey as a reading -- the one answer a
    # run arriving on that same page will never give.
    if any(phrase in title for phrase in detection.SURVEY_TITLES):
        return "FILLER"

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
    ("plugin", "UNGRADED_PLUGIN"),
    ("lab", "LAB"),
    # Subtext of an AI roleplay practice item, served under the /coach/ segment.
    ("dialogue", "DIALOGUE"),
    ("discussion", "DISCUSSION"),
)

# Screen-reader prefixes Coursera puts ahead of the type in a row's aria-label.
LABEL_PREFIXES = ("selected link", "link")


def _label_type(label):
    """Returns the type field of a sidebar row's ``aria-label``.

    The label reads "<type>, <title>, <status>, <duration>", sometimes behind a
    screen-reader prefix. Only the first field names the type; the title after
    it routinely contains words that name a different one.
    """
    for field in (label or "").split(","):
        candidate = field.strip().lower()
        if candidate and candidate not in LABEL_PREFIXES:
            return candidate
    return ""


def classify_sidebar_row(subtext, label, href):
    """Classifies one sidebar row from its subtext, aria-label, then URL.

    Each source is matched on its own. The subtext is consulted first because
    it names the type by itself ("Reading", "Honors Peer-graded Assignment")
    and distinguishes graded from ungraded variants sharing a URL segment; the
    aria-label's type field covers a row whose subtext has not rendered; the
    segment is the unlocalized last resort.

    The two text sources used to be concatenated into one haystack. Because the
    aria-label carries the item's title, a reading titled "... for Module 4
    Peer Assessment" matched "peer" and was mapped ``PEER_REVIEW`` -- named in
    the course map as work to submit, when it is a page to read.
    """
    for haystack in ((subtext or "").lower(), _label_type(label)):
        for needle, item_type in SUBTEXT_TYPES:
            if needle in haystack:
                return item_type
    return detection.label_from_url(href)


def apply_filler_override(item_type, text, subtext):
    """Downgrades a row to ``FILLER`` when its *title* marks it a survey.

    The titles are :data:`detection.SURVEY_TITLES` -- the same list the
    classifier uses on the open page -- so a row the map calls ``FILLER`` is
    exactly the row a run will call ``SURVEY`` when it arrives on it. They used
    to be separate lists, and the disagreement was expensive: "Welcome! Please
    Tell Us About Yourself" was mapped ``UNGRADED_PLUGIN``, so a run picked it
    as the first thing left to do, navigated to it, and only then recognised a
    survey and stepped past it.

    Only the title is checked, which is the long-standing behaviour and the
    reason the phrases are whole ones: a row whose *subtext* says "survey", and
    a reading titled "A Survey of Reinforcement Learning", are both course
    content and keep their type.
    """
    title = (text or "").lower()
    if any(phrase in title for phrase in detection.SURVEY_TITLES):
        return "FILLER"
    return item_type


def parse_duration(full_text):
    """Pulls a human duration ("15 min", "1h 10min") out of a row, or ``""``."""
    match = DURATION_PATTERN.search((full_text or "").lower())
    return match.group(0).strip() if match else ""
