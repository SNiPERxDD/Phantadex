"""Who a course is still waiting on: the run, or the person running it.

"62 of 86 complete" says nothing about whether the 24 left are a morning's work
for the run or a morning's work for you -- and on a real course they are almost
never the same kind of thing. Every rule that decides which is which already
existed, scattered between the traversal's skip list and the ledger's, so they
are gathered here and read from one place by the course tree, by the run's
closing report, and by anything else that counts.

The split is by label, not by settings. ``--pause-on-graded`` changes *when* a
run reaches a graded item, never who answers it: the run stops and waits, and
the answering is still yours.
"""

from typing import NamedTuple

from . import detection, urls

# Sidebar row labels the traversal names and steps past: graded work, which is
# left to the user, and the surveys the map has already identified as being
# about the learner rather than the course. Neither is answered and neither is
# archived, so an item carrying one of these is not work the run still has to do
# -- which is how it can tell a course with nothing left in it from one it has
# merely not walked yet. "REVIEW_PEERS" is the reviewing half of a peer
# assignment, which the map labels separately from the submission, and "FILLER"
# is the map's own label for a survey.
SKIPPED_LABELS = frozenset(
    {detection.QUIZ, detection.ASSIGNMENT, detection.PEER_REVIEW, "REVIEW_PEERS", "FILLER"}
)
# The graded subset of the above. "REVIEW_PEERS" belongs here as much as the
# submission it accompanies: both halves are answered by the user and neither is
# archived, so a run told to wait on graded work must wait on both. Leaving it
# out meant --pause-on-graded targeted the submission and walked past the review.
GRADED_LABELS = frozenset(
    {detection.QUIZ, detection.ASSIGNMENT, detection.PEER_REVIEW, "REVIEW_PEERS"}
)

# Types the run reads and archives but can never get marked complete: the
# platform counts a discussion only once *you* post to it. One of these is the
# run's work until the ledger holds it, and yours forever after.
SELF_SUBMITTED_LABELS = frozenset({detection.DISCUSSION})


class Split(NamedTuple):
    """How a course's rows divide by who can still act on them."""

    total: int
    complete: int
    ours: int
    yours: int
    unknown: int


def owner(label, archived=False):
    """Returns ``"ours"`` or ``"yours"`` for an unfinished row with this label."""
    if label in SKIPPED_LABELS:
        return "yours"
    if label in SELF_SUBMITTED_LABELS and archived:
        return "yours"
    return "ours"


def split(rows, completion_status, is_archived=None):
    """Counts ``rows`` by who is left to act on them.

    ``rows`` is any iterable of course-map rows. ``is_archived`` is an optional
    predicate taking an href; without it a discussion counts as the run's work,
    which is right until the ledger holds it.
    """
    counts = {"complete": 0, "ours": 0, "yours": 0, "unknown": 0}
    total = 0
    for _title, label, href, _duration in rows:
        total += 1
        state = completion_status.get(urls.normalize_path(href))
        if state is True:
            counts["complete"] += 1
        elif state is None:
            counts["unknown"] += 1
        else:
            # The ledger is read only for the one label it can settle. Asking
            # for every row re-parsed the whole file per row -- and the tree
            # counts each row twice, once for its module and once for the
            # course -- to answer a question that could not change the answer.
            archived = label in SELF_SUBMITTED_LABELS and _archived(is_archived, href)
            counts[owner(label, archived)] += 1
    return Split(total, counts["complete"], counts["ours"], counts["yours"], counts["unknown"])


def _archived(is_archived, href):
    """Runs the ledger predicate, treating a failure to read it as "not held"."""
    if is_archived is None:
        return False
    try:
        return bool(is_archived(href))
    except Exception:
        return False


def summary(counts, noun="items"):
    """Returns the one-line count, naming both sides of what is left.

    Phantadex's side is named even when it is empty. A line that simply stops
    after "25 complete" is the same line a course with work left would print,
    and the difference between the two is the whole reason to read it -- so
    having nothing left to do is said outright rather than shown by omission.
    Your side is left off when it is empty, because there is no such ambiguity:
    it is only ever an addition to what the run reported about itself.
    """
    parts = [f"{counts.total} {noun}", f"{counts.complete} complete"]
    parts.append(f"{counts.ours} left to Phantadex" if counts.ours else "nothing left to Phantadex")
    if counts.yours:
        parts.append(f"{counts.yours} left to you")
    if counts.unknown:
        parts.append(f"{counts.unknown} unreadable")
    return " · ".join(parts)


def tag(counts):
    """Returns the same reading shortened for a right-aligned module header.

    The header shares its line with the module title and pushes it aside, so the
    wording is abbreviated rather than repeated from :func:`summary`; a module
    with nothing unfinished says only how much of it is done.
    """
    label = f"{counts.complete}/{counts.total} complete"
    if not (counts.ours or counts.yours or counts.unknown):
        return label
    label += f" · {counts.ours} pdex"
    if counts.yours:
        label += f" · {counts.yours} you"
    if counts.unknown:
        label += f" · {counts.unknown} ?"
    return label
