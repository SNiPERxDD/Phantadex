"""Page-type classification tests, driven by fake locators."""

import unittest
from unittest import mock

from phantadex import detection
from tests.fakes import FakeLocator, FakePage

# A path whose segment ("module") is not in URL_SEGMENT_TYPES, so classify()
# falls through to the text heuristics these tests are actually exercising.
NEUTRAL_URL = "/learn/demo/module/abc/item"


def page_with(selectors=None, title="Item | Coursera", url=NEUTRAL_URL):
    """Builds a FakePage where the given selectors match one element each."""
    locators = {selector: FakeLocator(count=1) for selector in (selectors or [])}
    return FakePage(url=url, title=title, locators=locators)


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(detection.schema, "first_visible", return_value=None)
        self.mock_first_visible = patcher.start()
        self.addCleanup(patcher.stop)

    def test_video_detected_from_player(self):
        page = page_with([detection.VIDEO_SELECTORS])
        self.assertEqual(detection.classify(page), detection.VIDEO)

    def test_video_wins_over_quiz_markup(self):
        # A lecture page can carry quiz markup in its sidebar; video must win.
        page = page_with([detection.VIDEO_SELECTORS, detection.QUIZ_SELECTORS])
        self.assertEqual(detection.classify(page), detection.VIDEO)

    def test_quiz_detected_from_markup(self):
        page = page_with([detection.QUIZ_SELECTORS])
        self.assertEqual(detection.classify(page), detection.QUIZ)

    def test_quiz_detected_from_title(self):
        page = page_with(title="Module 1 Quiz | Coursera")
        self.assertEqual(detection.classify(page), detection.QUIZ)

    def test_peer_assignment_from_title(self):
        page = page_with(title="Peer-graded Assignment: Build a model | Coursera")
        self.assertEqual(detection.classify(page), detection.ASSIGNMENT)

    def test_honors_assignment_from_title(self):
        page = page_with(title="Honors Assignment | Coursera")
        self.assertEqual(detection.classify(page), detection.ASSIGNMENT)

    def test_plugin_detected_from_main_text(self):
        page = FakePage(url=NEUTRAL_URL, locators={"main|Ungraded Plugin": FakeLocator(count=1)})
        self.assertEqual(detection.classify(page), detection.PLUGIN)

    def test_reading_detected_from_body(self):
        self.mock_first_visible.return_value = FakeLocator(count=1)
        self.assertEqual(detection.classify(page_with()), detection.READING)

    def test_unknown_when_nothing_matches(self):
        self.assertEqual(detection.classify(page_with()), detection.UNKNOWN)

    def test_survives_title_failure(self):
        page = page_with()
        page.title = mock.Mock(side_effect=RuntimeError("detached"))
        self.assertEqual(detection.classify(page), detection.UNKNOWN)


class UrlSegmentTests(unittest.TestCase):
    """The URL path segment is the platform's own answer; it outranks text.

    Regression: the text heuristics classified every plugin, discussion and
    assignment page as READING, because the unscoped "Reading -- N min" pill
    check matched the course sidebar on every page.
    """

    def setUp(self):
        patcher = mock.patch.object(detection.schema, "first_visible", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _classify(self, path):
        return detection.classify(FakePage(url=f"https://www.coursera.org{path}"))

    def test_supplement_is_reading(self):
        self.assertEqual(self._classify("/learn/c/supplement/aaa/intro"), detection.READING)

    def test_lecture_is_video_without_a_player(self):
        # The player may not have rendered yet; the route still says lecture.
        self.assertEqual(self._classify("/learn/c/lecture/aaa/welcome"), detection.VIDEO)

    def test_discussion_prompt(self):
        self.assertEqual(
            self._classify("/learn/c/discussionPrompt/aaa/discuss"), detection.DISCUSSION
        )

    def test_ungraded_widget_is_plugin(self):
        self.assertEqual(self._classify("/learn/c/ungradedWidget/aaa/ex1"), detection.PLUGIN)

    def test_exam_is_quiz(self):
        self.assertEqual(self._classify("/learn/c/exam/aaa/final"), detection.QUIZ)

    def test_assignment_submission_is_assignment(self):
        self.assertEqual(
            self._classify("/learn/c/assignment-submission/aaa/check"), detection.ASSIGNMENT
        )

    def test_query_string_is_ignored(self):
        self.assertEqual(self._classify("/learn/c/supplement/aaa/x?utm=1"), detection.READING)

    def test_unrecognised_segment_falls_through(self):
        self.assertEqual(self._classify("/learn/c/module/aaa/outline"), detection.UNKNOWN)

    def test_the_segment_outranks_an_embedded_player(self):
        # VIDEO_SELECTORS matches a bare <video> tag anywhere on the page, so a
        # reading that embeds a clip used to be handed to the video handler --
        # which archived no reading text and watched a player that was not the
        # item. The platform's own segment decides.
        page = FakePage(
            url="/learn/c/supplement/aaa/notes",
            locators={detection.VIDEO_SELECTORS: FakeLocator(count=1)},
        )
        self.assertEqual(detection.classify(page), detection.READING)

    def test_a_player_still_classifies_an_unrecognised_segment(self):
        page = FakePage(
            url="/learn/c/module/aaa/outline",
            locators={detection.VIDEO_SELECTORS: FakeLocator(count=1)},
        )
        self.assertEqual(detection.classify(page), detection.VIDEO)


class IsGradedTests(unittest.TestCase):
    def test_graded_from_title(self):
        self.assertTrue(detection.is_graded(FakePage(title="Graded Quiz | Coursera")))

    def test_graded_from_body_text(self):
        page = FakePage(title="Quiz | Coursera")
        page.locators["main"] = FakeLocator(count=1, text="This is a Graded Assignment.")
        self.assertTrue(detection.is_graded(page))

    def test_practice_quiz_is_not_graded(self):
        page = FakePage(title="Practice Quiz | Coursera")
        page.locators["main"] = FakeLocator(count=1, text="Practice quiz, ungraded.")
        self.assertFalse(detection.is_graded(page))


class SegmentLabelTests(unittest.TestCase):
    def test_map_labels_are_finer_than_handler_types(self):
        # One segment table feeds both, so the map and the runner cannot drift.
        self.assertEqual(detection.label_from_url("/learn/c/ungradedLab/x/y"), detection.LAB)
        self.assertEqual(detection.type_from_url("/learn/c/ungradedLab/x/y"), detection.PLUGIN)
        self.assertEqual(detection.label_from_url("/learn/c/peer/x/y"), detection.PEER_REVIEW)
        self.assertEqual(detection.type_from_url("/learn/c/peer/x/y"), detection.ASSIGNMENT)

    def test_every_segment_resolves_in_both_tables(self):
        for segment in detection.URL_SEGMENT_LABELS:
            url = f"/learn/course/{segment}/abc/title"
            self.assertNotEqual(detection.label_from_url(url), detection.UNKNOWN, segment)
            self.assertNotEqual(detection.type_from_url(url), detection.UNKNOWN, segment)

    def test_segments_resolve_whatever_their_casing(self):
        # The tables are written in Coursera's camelCase and urls.item_id
        # already lowers before its own lookup. Matching exact case here let the
        # same URL be recognised by one module and not the other.
        for segment in detection.URL_SEGMENT_LABELS:
            for variant in (segment.lower(), segment.upper()):
                url = f"/learn/course/{variant}/abc/title"
                self.assertNotEqual(detection.type_from_url(url), detection.UNKNOWN, variant)
                self.assertEqual(
                    detection.type_from_url(url),
                    detection.type_from_url(f"/learn/course/{segment}/abc/title"),
                    variant,
                )

    def test_unrecognised_segment_is_unknown(self):
        self.assertEqual(detection.label_from_url("/learn/c/module/x/y"), detection.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
