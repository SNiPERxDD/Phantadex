"""Duration parsing and reading-estimate tests."""

import unittest

from phantadex import timing


class ParseTimeTests(unittest.TestCase):
    def test_minutes_seconds(self):
        self.assertEqual(timing.parse_time_to_seconds("1:30"), 90)

    def test_hours_minutes_seconds(self):
        self.assertEqual(timing.parse_time_to_seconds("1:00:30"), 3630)

    def test_unparseable_returns_zero(self):
        for value in ("", None, "--:--", "abc", "1:2:3:4"):
            self.assertEqual(timing.parse_time_to_seconds(value), 0, value)


class FormatSecondsTests(unittest.TestCase):
    def test_under_an_hour(self):
        self.assertEqual(timing.format_seconds(90), "1:30")

    def test_over_an_hour(self):
        self.assertEqual(timing.format_seconds(3630), "1:00:30")

    def test_negative_clamps_to_zero(self):
        self.assertEqual(timing.format_seconds(-5), "0:00")


class ParseSkipRangeTests(unittest.TestCase):
    def test_percentage_range(self):
        self.assertEqual(timing.parse_skip_range("97.5-98.5%", 200), (195.0, 197.0))

    def test_reversed_bounds_are_ordered(self):
        self.assertEqual(timing.parse_skip_range("98.5-97.5%", 200), (195.0, 197.0))

    def test_timestamp_range(self):
        self.assertEqual(timing.parse_skip_range("00:30-01:15", 200), (30.0, 75.0))

    def test_seconds_suffix(self):
        self.assertEqual(timing.parse_skip_range("10s-20s", 200), (10.0, 20.0))

    def test_clamped_to_duration(self):
        self.assertEqual(timing.parse_skip_range("0-500", 200), (0.0, 200.0))

    def test_disabled_returns_none(self):
        self.assertIsNone(timing.parse_skip_range("", 200))
        self.assertIsNone(timing.parse_skip_range("   ", 200))
        self.assertIsNone(timing.parse_skip_range(None, 200))

    def test_malformed_raises(self):
        with self.assertRaises(ValueError):
            timing.parse_skip_range("nonsense", 200)
        with self.assertRaises(ValueError):
            timing.parse_skip_range("50-", 200)


class ReadEstimateTests(unittest.TestCase):
    def test_scales_with_word_count(self):
        short = timing.estimate_read_minutes("word " * 100)
        long = timing.estimate_read_minutes("word " * 1000)
        self.assertLess(short, long)

    def test_floor_applies(self):
        self.assertGreaterEqual(timing.estimate_read_minutes("hi"), timing.MIN_READ_MINUTES)

    def test_empty_text(self):
        self.assertEqual(timing.estimate_read_minutes(""), 1.0)


class DwellMinutesTests(unittest.TestCase):
    """How long a reading is worth staying on."""

    def test_narration_sets_the_pace_and_ignores_the_listed_duration(self):
        # 7:48 of audio on an item Coursera lists at 10 minutes.
        self.assertAlmostEqual(timing.dwell_minutes(5.9, 10, 467.7), 7.0155)

    def test_the_dwell_ends_before_the_narration_does(self):
        # Leaving at the end of the audio is leaving after the platform has
        # already started moving the tab itself.
        self.assertLess(timing.dwell_minutes(1.0, 10, 600.0), 10.0)

    def test_without_narration_the_estimate_gets_a_settling_margin(self):
        self.assertEqual(timing.dwell_minutes(4.0, 10), 4.0 + timing.READING_SETTLE_MINUTES)

    def test_the_listed_duration_caps_a_long_estimate(self):
        self.assertEqual(timing.dwell_minutes(30.0, 10), 10)

    def test_a_short_reading_is_no_longer_doubled(self):
        # The old formula was min(listed, estimate * 2): a five-minute reading
        # became a nine-minute stare on a page listed at nine.
        self.assertLess(timing.dwell_minutes(4.5, 9), 4.5 * 2)

    def test_the_floor_never_exceeds_what_the_caller_asked_for(self):
        self.assertEqual(timing.dwell_minutes(0.1, 0.2, floor_min=1.0), 0.2)


if __name__ == "__main__":
    unittest.main()
