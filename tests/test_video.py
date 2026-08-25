"""Reading the state of the page's media, whatever kind of player it is."""

import unittest
from unittest import mock

from phantadex import video
from tests.fakes import FakePage


class MediaStateTests(unittest.TestCase):
    """One reader serves the video player and a reading's narration alike."""

    def test_the_element_kind_is_the_readers_argument(self):
        page = FakePage(evaluate_result={"duration": 12.0})
        video.state(page)
        video.narration_state(page)
        self.assertEqual(page.evaluated, [video._STATE_JS, video._STATE_JS])

    def test_a_page_with_no_such_element_reads_as_absent(self):
        self.assertIsNone(video.state(FakePage(evaluate_result=None)))

    def test_an_unreadable_page_is_absent_rather_than_an_error(self):
        page = FakePage(evaluate_error=RuntimeError("execution context destroyed"))
        self.assertIsNone(video.state(page))

    def test_the_longest_loaded_element_is_the_item(self):
        # A reading was measured carrying two narration tracks, one of them
        # unloaded and reporting NaN. Taking the first node in document order
        # picked the empty one and the item read as silent.
        self.assertIn("longest", video._STATE_JS)
        self.assertNotIn("nodes[0]", video._STATE_JS)


class NarrationSecondsTests(unittest.TestCase):
    def test_a_narrated_reading_reports_its_audio_length(self):
        page = FakePage(evaluate_result={"duration": 467.712, "currentTime": 0})
        self.assertAlmostEqual(video.narration_seconds(page), 467.712)

    def test_a_plain_reading_answers_immediately(self):
        page = FakePage(evaluate_result=None)
        with mock.patch.object(video.time, "sleep") as sleep:
            self.assertEqual(video.narration_seconds(page), 0.0)
        sleep.assert_not_called()

    def test_a_player_that_never_loads_gives_up(self):
        page = FakePage(evaluate_result={"duration": 0, "currentTime": 0})
        with mock.patch.object(video.time, "sleep"):
            self.assertEqual(video.narration_seconds(page), 0.0)


if __name__ == "__main__":
    unittest.main()
