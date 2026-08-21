"""Human-shaped delays for the pacing the site can observe.

Every delay in the package used to be a uniform draw between two bounds. Human
inter-action gaps are not uniform: most fall near the short end, a few run long,
and the distribution has a tail rather than a hard ceiling. Aggregated over a
course, flat draws describe a rectangle no person produces.

Only the timing sites use this. Percentages and pixel offsets stay uniform --
nothing reads those as behaviour, and a skewed click offset would just bias
every click toward one edge of the control.
"""

import random

# Shape of the draw inside the requested bounds, as a log-normal over the
# fraction of the span. The median fraction is exp(_LOG_MU) ~= 0.41, so a
# 0.4-0.9s reaction lands near 0.6s most of the time and occasionally runs to
# the top of the range.
_LOG_MU = -0.9
_LOG_SIGMA = 0.55
# Draws above the span are re-rolled rather than clipped: clipping piles every
# overshoot onto the ceiling, which is the flat edge this module exists to
# remove. About one draw in twenty overshoots, so the loop rarely repeats.
_MAX_DRAWS = 8

# Occasionally a person stops attending to the page altogether. Without this the
# longest gap in a session is exactly the ceiling that was asked for.
LONG_PAUSE_PROBABILITY = 0.04
LONG_PAUSE_FACTOR = (1.6, 3.2)


def duration(low, high, rng=None):
    """Returns a delay in seconds, weighted toward ``low`` and rarely beyond ``high``.

    Callers sleep on the result themselves, so a caller's own ``time.sleep`` is
    what tests patch, exactly as before this module existed.
    """
    rng = random if rng is None else rng
    low = max(0.0, float(low))
    high = max(low, float(high))
    if high == low:
        return low

    delay = low + (high - low) * _skewed_fraction(rng)
    if rng.random() < LONG_PAUSE_PROBABILITY:
        delay *= rng.uniform(*LONG_PAUSE_FACTOR)
    return delay


def _skewed_fraction(rng):
    """Returns a fraction of the span in ``[0, 1)``, with its mass near zero."""
    for _ in range(_MAX_DRAWS):
        fraction = rng.lognormvariate(_LOG_MU, _LOG_SIGMA)
        if fraction < 1.0:
            return fraction
    return rng.random()
