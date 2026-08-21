"""Classification of the currently loaded course item.

The original code carried five independent boolean flags mutated across ~70
lines of interleaved ``try`` blocks, with priority expressed as a chain of
``not is_quiz and not is_video and ...`` guards. This states the precedence once,
in order, and returns a single verdict.
"""

from . import logs, schema

log = logs.get_logger("detection")

VIDEO = "VIDEO"
QUIZ = "QUIZ"
ASSIGNMENT = "ASSIGNMENT"
PLUGIN = "PLUGIN"
READING = "READING"
DISCUSSION = "DISCUSSION"
SURVEY = "SURVEY"
DIALOGUE = "DIALOGUE"
UNKNOWN = "UNKNOWN"

# Coursera routes each item type through its own URL path segment. The segment
# is the platform's own classification, is not localized, and is readable before
# the item pane finishes rendering -- so it outranks every text heuristic below.
#
# These are the fine-grained labels the course map displays. The runner needs a
# coarser answer -- it has one handler for every kind of embedded tool -- so
# ``URL_SEGMENT_TYPES`` below collapses them. Keeping one segment table rather
# than two stops the map and the runner disagreeing about what an item is.
LAB = "LAB"
PEER_REVIEW = "PEER_REVIEW"

URL_SEGMENT_LABELS = {
    "lecture": VIDEO,
    "supplement": READING,
    "discussionPrompt": DISCUSSION,
    "ungradedWidget": LAB,
    "ungradedLti": LAB,
    "ungradedLab": LAB,
    # Coursera's AI roleplay practice ("Practice: ...", subtext "Dialogue"). It
    # is ungraded like the widgets above, but it is driven rather than dwelled
    # on -- a dialogue has to be started and then explicitly ended before the
    # item counts as complete -- so it keeps its own label and handler.
    "coach": DIALOGUE,
    "quiz": QUIZ,
    "exam": QUIZ,
    "assignment": ASSIGNMENT,
    "assignment-submission": ASSIGNMENT,
    "programming": ASSIGNMENT,
    "peer": PEER_REVIEW,
}

# Labels the map distinguishes but that share a single handler.
_LABEL_TO_TYPE = {LAB: PLUGIN, PEER_REVIEW: ASSIGNMENT}

URL_SEGMENT_TYPES = {
    segment: _LABEL_TO_TYPE.get(label, label) for segment, label in URL_SEGMENT_LABELS.items()
}

QUIZ_SELECTORS = ".rc-QuizApp, .rc-FormPartsQuestion, [data-testid='quiz-submit-button']"
VIDEO_SELECTORS = ".rc-VideoControlsContainer, video"
PLUGIN_PHRASES = ("Ungraded Plugin", "Ungraded External Tool")
ASSIGNMENT_TITLES = (
    "peer-graded",
    "peer assessment",
    "case study",
    "honors assignment",
    "review your peers",
)
GRADED_PHRASES = ("Graded Assignment", "weighted heavily")

# Titles that identify a questionnaire about the learner rather than course
# content. Each is a full phrase: a bare "survey" would also match a reading
# titled "A Survey of Reinforcement Learning", which is course content and is
# archived like any other. Surveys are served under the same URL segments as
# readings and plugins, so the title is the only thing that separates them.
SURVEY_TITLES = (
    "demographics survey",
    "course survey",
    "entry survey",
    "exit survey",
    "pre-course survey",
    "post-course survey",
    "how was the course",
    "tell us about yourself",
)


def classify(page):
    """Returns one of the module-level page-type constants.

    Order: the URL path segment wins, then an on-page video player, then the
    text heuristics. The segment is the platform's own answer and is readable
    before the item pane renders. It outranks the player check because
    ``VIDEO_SELECTORS`` matches a bare ``<video>`` tag anywhere on the page:
    a reading or quiz with an embedded clip was handed to the video handler,
    which then hunted for a transcript and watched a player that was not the
    item.

    The one thing checked ahead of the segment is a survey title; see below.

    Text heuristics are the last resort. They used to run first and classified
    every plugin, discussion and assignment on this account's course as
    ``READING`` -- see :func:`_looks_like_reading`.
    """
    title = _safe_title(page)
    title_lower = title.lower()

    # Ahead of the segment, which is the one exception to the order above: a
    # survey is served as a supplement or a widget, so the segment names its
    # container rather than what it asks for.
    if any(phrase in title_lower for phrase in SURVEY_TITLES):
        return SURVEY

    from_url = type_from_url(_safe_url(page))
    if from_url != UNKNOWN:
        return from_url

    if _count(page, VIDEO_SELECTORS) > 0:
        return VIDEO

    if _count(page, QUIZ_SELECTORS) > 0 or "quiz" in title_lower:
        return QUIZ

    if any(phrase in title_lower for phrase in ASSIGNMENT_TITLES):
        return ASSIGNMENT

    if _has_plugin_marker(page):
        return PLUGIN

    if "assignment" in title_lower:
        return ASSIGNMENT

    if _looks_like_reading(page):
        return READING

    return UNKNOWN


def is_graded(page):
    """Reports whether the current quiz/assignment counts toward the grade."""
    try:
        if "Graded Quiz" in page.title():
            return True
    except Exception as exc:
        log.debug("Title read failed during grade check: %s", exc)

    try:
        text = page.locator("main").first.inner_text()
        return any(phrase in text for phrase in GRADED_PHRASES)
    except Exception as exc:
        log.debug("Main-body read failed during grade check: %s", exc)
        return False


def _safe_title(page):
    """Returns the page title, or ``""`` when it cannot be read."""
    try:
        return page.title() or ""
    except Exception as exc:
        log.debug("Title read failed: %s", exc)
        return ""


def _count(page, selector):
    """Counts matches for a raw selector, tolerating detached frames."""
    try:
        return page.locator(selector).count()
    except Exception as exc:
        log.debug("Count failed for %r: %s", selector, exc)
        return 0


def _has_plugin_marker(page):
    """Detects the LTI / external-tool markers, scoped to <main>."""
    for phrase in PLUGIN_PHRASES:
        try:
            if page.locator("main", has_text=phrase).count() > 0:
                return True
        except Exception as exc:
            log.debug("Plugin phrase %r check failed: %s", phrase, exc)
    return False


def _looks_like_reading(page):
    """Detects a rendered reading body.

    This deliberately does not fall back to the "Reading -- N min" metadata
    pill. That check was unscoped, and the course sidebar lists every item's
    type and duration, so on a course with any reading at all it matched dozens
    of sidebar rows and returned true on every page -- making ``READING`` a
    catch-all. Items with no reading body now fall through to ``UNKNOWN``,
    which the URL segment above resolves for every real Coursera item type.
    """
    return schema.first_visible(page, "content", "reading_body") is not None


def _segment(url, table):
    """Returns the table entry for the first recognised path segment.

    Matched case-insensitively. The segment tables are written in Coursera's
    camelCase, and ``urls.item_id`` already lowers before its own lookup; an
    exact match here meant the same URL could be recognised by one module and
    not the other if Coursera ever changed the casing.
    """
    folded = {key.lower(): value for key, value in table.items()}
    for part in (url or "").split("?")[0].split("/"):
        entry = folded.get(part.lower())
        if entry is not None:
            return entry
    return UNKNOWN


def type_from_url(url):
    """Classifies an item from its URL path segment, or ``UNKNOWN``."""
    return _segment(url, URL_SEGMENT_TYPES)


def label_from_url(url):
    """Like :func:`type_from_url`, but keeps the map's finer labels."""
    return _segment(url, URL_SEGMENT_LABELS)


def _safe_url(page):
    """Returns the current URL, or ``""`` when the page is mid-navigation."""
    try:
        return page.url or ""
    except Exception as exc:
        log.debug("URL read failed: %s", exc)
        return ""
