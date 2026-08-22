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
    # Types already jumped to. A sidebar row's type and the type the page
    # reports need not agree, so a target can be visited without ever clearing
    # itself off the missing list. Without this, the run jumps to the same item
    # forever: the second hop lands on the URL already open, the loop's
    # change check never fires again, and the poll spins in silence.
    attempted_types: set = field(default_factory=set)
