"""Scraped-text normalisation."""

import unittest

from phantadex import text

RAW = (
    "Play video starting at ::5 and follow transcript\n"
    "0:05\n"
    "​Having a strong grasp of your finances helps.\xa0​It really does.\n"
    "Play video starting at :1:42 and follow transcript\n"
    "1:42\n"
    "​You will explore two key statements.\n"
)


class CleanTranscriptTests(unittest.TestCase):
    def test_cue_buttons_are_dropped(self):
        self.assertNotIn("Play video starting at", text.clean_transcript(RAW))

    def test_timestamps_are_kept_by_default(self):
        self.assertIn("0:05", text.clean_transcript(RAW).splitlines())

    def test_timestamps_can_be_dropped(self):
        cleaned = text.clean_transcript(RAW, keep_timestamps=False)
        self.assertNotIn("0:05", cleaned.splitlines())
        self.assertIn("You will explore two key statements.", cleaned)

    def test_invisible_characters_are_removed(self):
        cleaned = text.clean_transcript(RAW)
        self.assertNotIn("​", cleaned)
        self.assertNotIn("\xa0", cleaned)

    def test_speech_survives_intact(self):
        self.assertIn(
            "Having a strong grasp of your finances helps. It really does.",
            text.clean_transcript(RAW),
        )

    def test_hour_length_timestamps_are_recognised(self):
        self.assertEqual(text.clean_transcript("1:02:33\nWords here."), "1:02:33\nWords here.")

    def test_a_timestamp_with_no_phrase_does_not_double_up(self):
        cleaned = text.clean_transcript("0:05\n0:09\nOnly phrase.")
        self.assertEqual(cleaned, "0:09\nOnly phrase.")

    def test_trailing_timestamps_are_trimmed(self):
        self.assertEqual(text.clean_transcript("0:05\nPhrase.\n9:99\n"), "0:05\nPhrase.")

    def test_a_number_in_speech_is_not_mistaken_for_a_cue(self):
        self.assertIn(
            "It cost 5:00 rupees", text.clean_transcript("0:05\nIt cost 5:00 rupees each.")
        )

    def test_empty_input_passes_through(self):
        self.assertEqual(text.clean_transcript(""), "")
        self.assertIsNone(text.clean_transcript(None))


class CleanReadingTests(unittest.TestCase):
    def test_invisible_characters_are_removed(self):
        self.assertEqual(text.clean_reading("​Hello\xa0world"), "Hello world")

    def test_blank_line_runs_collapse(self):
        self.assertEqual(text.clean_reading("One\n\n\n\nTwo"), "One\n\nTwo")

    def test_cue_buttons_are_not_stripped_from_readings(self):
        # Only transcripts carry them; a reading that quotes the phrase keeps it.
        body = "Play video starting at the top of the module."
        self.assertEqual(text.clean_reading(body), body)

    def test_empty_input_passes_through(self):
        self.assertEqual(text.clean_reading(""), "")
        self.assertIsNone(text.clean_reading(None))


class AppendLinksTests(unittest.TestCase):
    def test_a_body_with_no_links_is_untouched(self):
        self.assertEqual(text.append_links("Body.", []), "Body.")

    def test_a_link_is_listed_under_its_label(self):
        body = text.append_links(
            "Course-Diagram\nPPTX File", [("Course-Diagram PPTX File", "https://cdn/d.pptx")]
        )
        self.assertEqual(
            body,
            "Course-Diagram\nPPTX File\n\nLinks:\nCourse-Diagram PPTX File -- https://cdn/d.pptx",
        )

    def test_a_label_spanning_lines_is_flattened(self):
        body = text.append_links("x", [("Course-Diagram\nPPTX File", "https://cdn/d.pptx")])
        self.assertIn("Course-Diagram PPTX File -- https://cdn/d.pptx", body)

    def test_a_url_already_written_out_is_not_repeated(self):
        body = "Read https://example.com/paper for more."
        self.assertEqual(
            text.append_links(body, [("https://example.com/paper", "https://example.com/paper")]),
            body,
        )

    def test_the_same_url_twice_is_listed_once(self):
        body = text.append_links("x", [("Home", "https://a.com"), ("Home again", "https://a.com")])
        self.assertEqual(body.count("https://a.com"), 1)

    def test_a_link_with_no_label_is_listed_bare(self):
        self.assertEqual(
            text.append_links("x", [("", "https://a.com")]), "x\n\nLinks:\nhttps://a.com"
        )

    def test_an_empty_body_yields_the_block_alone(self):
        self.assertEqual(
            text.append_links("", [("Home", "https://a.com")]), "Links:\nHome -- https://a.com"
        )


if __name__ == "__main__":
    unittest.main()
