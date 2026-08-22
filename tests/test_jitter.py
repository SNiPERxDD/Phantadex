"""Tests for the shape of the humanised delays."""

import random
import statistics
import unittest
from unittest import mock

from phantadex import handlers, interaction, jitter, navigation


class DurationShapeTests(unittest.TestCase):
    """Distribution properties, driven by a seeded generator so they are exact."""

    def setUp(self):
        self.rng = random.Random(1234)
        self.draws = [jitter.duration(0.4, 0.9, self.rng) for _ in range(20000)]

    def test_the_floor_is_never_breached(self):
        self.assertGreaterEqual(min(self.draws), 0.4)

    def test_the_mass_sits_near_the_short_end(self):
        # A uniform draw over the same bounds has a median of 0.65. Landing
        # below that is the whole point: most human gaps are short ones.
        self.assertLess(statistics.median(self.draws), 0.65)
        self.assertGreater(statistics.median(self.draws), 0.4)

    def test_the_ceiling_is_soft_but_rarely_crossed(self):
        beyond = [value for value in self.draws if value > 0.9]
        share = len(beyond) / len(self.draws)
        # The occasional long pause is what stops the longest gap in a session
        # from being exactly the ceiling that was asked for.
        self.assertGreater(share, 0.01)
        self.assertLess(share, 0.10)

    def test_the_pace_of_a_run_is_not_changed(self):
        # Mean matches the uniform draw it replaces, so a course takes about as
        # long as it did; only the shape of the individual gaps differs.
        self.assertAlmostEqual(statistics.fmean(self.draws), 0.65, delta=0.03)


class DurationBoundaryTests(unittest.TestCase):
    def test_a_zero_width_range_returns_that_value(self):
        self.assertEqual(jitter.duration(2.0, 2.0), 2.0)

    def test_reversed_bounds_collapse_rather_than_raise(self):
        self.assertEqual(jitter.duration(3.0, 1.0), 3.0)

    def test_a_negative_floor_is_clamped(self):
        self.assertGreaterEqual(jitter.duration(-5.0, 0.0), 0.0)

    def test_a_run_of_overshooting_draws_still_terminates(self):
        # The fraction is re-rolled rather than clipped; an unlucky streak must
        # fall back instead of looping.
        rng = mock.Mock()
        rng.lognormvariate.return_value = 5.0
        rng.random.return_value = 0.5
        rng.uniform.return_value = 1.0

        value = jitter.duration(1.0, 2.0, rng)

        self.assertGreaterEqual(value, 1.0)
        self.assertLessEqual(value, 2.0)
        self.assertEqual(rng.lognormvariate.call_count, jitter._MAX_DRAWS)


class WiringTests(unittest.TestCase):
    """The timing sites draw from jitter; the geometry sites stay uniform."""

    def test_the_click_reaction_is_jittered(self):
        page = mock.Mock()
        locator = mock.Mock()
        locator.is_visible.return_value = True
        locator.bounding_box.return_value = {"x": 0, "y": 0, "width": 100, "height": 40}

        with (
            mock.patch.object(interaction.jitter, "duration", return_value=0.0) as duration,
            mock.patch.object(interaction.time, "sleep"),
        ):
            self.assertTrue(interaction.click(page, locator, reaction_range=(0.4, 0.9)))

        duration.assert_called_once_with(0.4, 0.9)

    def test_the_reading_pause_and_post_target_dwell_have_ranges(self):
        self.assertEqual(interaction.READING_PAUSE_RANGE, (2.5, 6.0))
        self.assertEqual(handlers.POST_TARGET_DWELL_RANGE, (2.0, 4.0))

    def test_the_settle_and_dialogue_waits_are_ranges_not_constants(self):
        # A fixed value repeated across hundreds of items is a cadence no
        # person produces; every pacing site draws from a range instead.
        for low, high in (
            navigation.SETTLE_RANGE,
            handlers.DIALOGUE_DWELL_RANGE,
            handlers.DIALOGUE_DWELL_SLICE_RANGE,
            handlers.DIALOGUE_RETRY_SECONDS_RANGE,
        ):
            self.assertLess(low, high)

    def test_click_jitter_stays_uniform(self):
        # Skewing a pixel offset would bias every click toward one edge of the
        # control, which is a pattern rather than the absence of one.
        page = mock.Mock()
        locator = mock.Mock()
        locator.is_visible.return_value = True
        locator.bounding_box.return_value = {"x": 0, "y": 0, "width": 100, "height": 40}

        with (
            mock.patch.object(interaction.random, "randint", return_value=0) as randint,
            mock.patch.object(interaction.time, "sleep"),
        ):
            interaction.click(page, locator)

        self.assertTrue(randint.called)


if __name__ == "__main__":
    unittest.main()
