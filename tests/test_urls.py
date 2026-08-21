"""URL normalization and item-identity tests."""

import unittest

from phantadex import detection, urls


class NormalizePathTests(unittest.TestCase):
    def test_strips_origin_query_and_fragment(self):
        self.assertEqual(
            urls.normalize_path("https://www.coursera.org/learn/x/lecture/AbC/hi?skip=1#top"),
            "/learn/x/lecture/AbC/hi",
        )

    def test_strips_trailing_slash(self):
        self.assertEqual(urls.normalize_path("/learn/x/lecture/AbC/hi/"), "/learn/x/lecture/AbC/hi")

    def test_adds_leading_slash(self):
        self.assertEqual(urls.normalize_path("learn/x"), "/learn/x")

    def test_empty_input(self):
        self.assertEqual(urls.normalize_path(""), "")
        self.assertEqual(urls.normalize_path(None), "")


class ItemIdTests(unittest.TestCase):
    def test_extracts_item_id(self):
        self.assertEqual(urls.item_id("/learn/course/lecture/Ab3Cd/welcome"), "Ab3Cd")

    def test_missing_item_id(self):
        self.assertEqual(urls.item_id("/learn/course"), "")
        self.assertEqual(urls.item_id("/about"), "")


class CourseSlugTests(unittest.TestCase):
    def test_extracts_course_slug_from_item_url(self):
        self.assertEqual(
            urls.course_slug("https://www.coursera.org/learn/example-course/lecture/a/b"),
            "example-course",
        )

    def test_missing_course_slug_is_empty(self):
        self.assertEqual(urls.course_slug("/about"), "")
        self.assertEqual(urls.course_slug(""), "")


class SameItemTests(unittest.TestCase):
    def test_absolute_matches_relative(self):
        self.assertTrue(
            urls.same_item(
                "https://www.coursera.org/learn/x/lecture/AbC/hi?utm=1",
                "/learn/x/lecture/AbC/hi",
            )
        )

    def test_same_id_different_slug(self):
        self.assertTrue(
            urls.same_item("/learn/x/lecture/AbC/intro", "/learn/x/lecture/AbC/introduction")
        )

    def test_rejects_substring_collision(self):
        # The old `a in b or b in a` check matched these as the same item.
        self.assertFalse(
            urls.same_item("/learn/x/lecture/AbC/intro", "/learn/x/lecture/ZzZ/intro-part-two")
        )

    def test_rejects_prefix_collision(self):
        self.assertFalse(urls.same_item("/learn/x/lecture/AbC/a", "/learn/x/lecture/AbC2/a"))

    def test_empty_never_matches(self):
        self.assertFalse(urls.same_item("", "/learn/x/lecture/A/b"))
        self.assertFalse(urls.same_item("/learn/x/lecture/A/b", None))


class AbsoluteUrlTests(unittest.TestCase):
    def test_builds_from_path(self):
        self.assertEqual(
            urls.absolute_url("/learn/x/lecture/A/b"),
            "https://www.coursera.org/learn/x/lecture/A/b",
        )

    def test_passes_through_absolute(self):
        target = "https://www.coursera.org/learn/x"
        self.assertEqual(urls.absolute_url(target), target)


class ItemSegmentTableTests(unittest.TestCase):
    """The id parser and the classifier must agree on what an item URL is."""

    def test_every_classified_segment_carries_an_item_id(self):
        # item_id() reads segment 4 only for these; a segment the classifier
        # knows but this table does not would silently lose item identity.
        unknown = {
            segment
            for segment in detection.URL_SEGMENT_LABELS
            if segment.lower() not in urls.ITEM_TYPE_SEGMENTS
        }
        self.assertEqual(unknown, set())

    def test_a_navigational_path_has_no_item_id(self):
        self.assertEqual(urls.item_id("/learn/x/home/week/1"), "")
        self.assertEqual(urls.item_id("/learn/x/lecture/AbCd/intro"), "AbCd")

    def test_a_coach_item_has_an_item_id(self):
        # Without the segment the id read back empty, so two roleplay items
        # compared equal and the run could not tell it had moved between them.
        self.assertEqual(urls.item_id("/learn/x/coach/gMS8D/practice-a"), "gMS8D")
        self.assertFalse(
            urls.same_item("/learn/x/coach/gMS8D/practice-a", "/learn/x/coach/tepja/practice-b")
        )


if __name__ == "__main__":
    unittest.main()
