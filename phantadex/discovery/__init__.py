"""Selector discovery: what is on this page, and where does the course go next.

Split by responsibility, each layer depending only on the ones above it:

``rules``       classification from text, URLs and titles; no page state.
``context``     identity of the open item: course, module, title, page type.
``course_map``  the outline read out of the sidebar, and its completion state.
``probing``     schema selectors verified against the live page, and persisted.
``observation`` the loop that ties those together and navigates the course.

The names re-exported here are the package's public surface; everything else is
internal to the module that owns it.
"""

from .context import get_page_metadata, get_robust_course_name
from .course_map import (
    expand_sidebar,
    get_completion_status,
    get_detailed_course_map,
    print_course_map,
)
from .observation import (
    auto_hop_next,
    auto_hop_smart,
    get_sidebar_targets,
    start_dynamic_observation,
)
from .probing import categories_to_scan, discover_selectors, find_element_in_frames
from .rules import apply_filler_override, classify_sidebar_row, detect_page_type, parse_duration
from .state import ObservationState

__all__ = [
    "ObservationState",
    "apply_filler_override",
    "auto_hop_next",
    "auto_hop_smart",
    "categories_to_scan",
    "classify_sidebar_row",
    "detect_page_type",
    "discover_selectors",
    "expand_sidebar",
    "find_element_in_frames",
    "get_completion_status",
    "get_detailed_course_map",
    "get_page_metadata",
    "get_robust_course_name",
    "get_sidebar_targets",
    "parse_duration",
    "print_course_map",
    "start_dynamic_observation",
]
