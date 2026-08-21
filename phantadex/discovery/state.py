"""State carried across one discovery session.

Replaces the module-level ``GLOBAL_REQUIRED_TYPES`` and the attributes that
used to be hung off the mapping function: both made the session implicit, so
two passes in one process shared results without saying so.
"""

from dataclasses import dataclass, field


@dataclass
class ObservationState:
    """Everything one discovery run accumulates as it walks a course."""

    selectors: dict = field(default_factory=dict)
    discovered_types: set = field(default_factory=set)
    required_types: set = field(default_factory=set)
    course_map: dict = None
    mapped: bool = False
