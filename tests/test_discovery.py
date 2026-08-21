"""Course-map row parsing. Pure functions -- no browser needed."""

import ast
import re
import unittest
from pathlib import Path
from unittest import mock

from phantadex import discovery, element_schema, schema
from phantadex.discovery import context, course_map, observation, probing, rules
from phantadex.discovery.state import ObservationState
from tests.fakes import FakeLocator, FakePage, capture_console


class ClassifySidebarRowTests(unittest.TestCase):
    def test_subtext_wins_over_the_url_segment(self):
        # The segment says lecture, but the row says graded quiz.
        self.assertEqual(
            discovery.classify_sidebar_row("graded quiz | 30 min", "", "/learn/c/lecture/a/b"),
            "QUIZ",
        )

    def test_falls_back_to_the_url_segment_when_the_row_is_bare(self):
        # A row that has not finished rendering still classifies correctly.
        self.assertEqual(
            discovery.classify_sidebar_row("", "", "/learn/c/discussionPrompt/a/b"), "DISCUSSION"
        )

    def test_aria_label_is_consulted_when_subtext_is_empty(self):
        self.assertEqual(discovery.classify_sidebar_row("", "Reading: intro", "/x"), "READING")

    def test_review_your_peers_beats_bare_peer(self):
        # Order is load-bearing: "peer" would otherwise swallow this row.
        self.assertEqual(
            discovery.classify_sidebar_row("review your peers", "", "/x"), "REVIEW_PEERS"
        )

    def test_assignment_beats_quiz(self):
        # Regression: peer-graded assignments were mislabeled QUIZ.
        self.assertEqual(
            discovery.classify_sidebar_row("graded assignment quiz", "", "/x"), "ASSIGNMENT"
        )

    def test_unrecognised_row_is_unknown(self):
        self.assertEqual(discovery.classify_sidebar_row("", "", "/learn/c/module/a/b"), "UNKNOWN")


class FillerOverrideTests(unittest.TestCase):
    def test_a_survey_in_the_title_is_downgraded_to_filler(self):
        self.assertEqual(
            discovery.apply_filler_override("READING", "End of course survey", "reading"), "FILLER"
        )

    def test_only_the_title_is_checked_for_the_survey_marker(self):
        # Documents existing behaviour, not an endorsement of it: a row whose
        # *subtext* says "survey" but whose title does not is left as its content
        # type, so it is still dwelled on and archived.
        self.assertEqual(
            discovery.apply_filler_override("READING", "Please tell us about yourself", "survey"),
            "READING",
        )

    def test_a_congratulations_video_stays_a_video(self):
        # It carries a filler keyword but still counts for discovery.
        self.assertEqual(
            discovery.apply_filler_override("VIDEO", "Course Farewell", "video | 2 min"), "VIDEO"
        )

    def test_an_ordinary_row_is_untouched(self):
        self.assertEqual(
            discovery.apply_filler_override("VIDEO", "Balance Sheets", "video"), "VIDEO"
        )


class ParseDurationTests(unittest.TestCase):
    def test_minutes(self):
        self.assertEqual(discovery.parse_duration("Intro\nVideo | 15 min"), "15 min")

    def test_hours_and_minutes(self):
        self.assertEqual(discovery.parse_duration("Exam\n1 h 10 min"), "1 h 10 min")

    def test_absent_duration_is_empty(self):
        self.assertEqual(discovery.parse_duration("Discussion Prompt"), "")

    def test_none_is_safe(self):
        self.assertEqual(discovery.parse_duration(None), "")


class CourseNameTests(unittest.TestCase):
    def test_item_heading_is_not_used_as_the_course_name(self):
        page = FakePage(locators={"h1": FakeLocator(count=1, text="Lesson title")})

        self.assertIsNone(discovery.get_robust_course_name(page))


class ConsoleRoutingTests(unittest.TestCase):
    """Discovery output must go through the logger so it honours --log-level."""

    def test_the_package_holds_no_raw_print_calls(self):
        package = Path(discovery.__file__).parent
        offenders = [
            f"{module.name}:{number}: {line.strip()}"
            for module in sorted(package.glob("*.py"))
            for number, line in enumerate(module.read_text().splitlines(), start=1)
            if re.search(r"(?<![\w.])print\s*\(", line)
        ]
        self.assertEqual(offenders, [])


class SeedFindingsTests(unittest.TestCase):
    def test_untouched_categories_survive_the_rewrite(self):
        # Regression: findings collected only the categories relevant to the
        # current page type and were then dumped over config.yaml wholesale, so
        # scanning a video page erased every reading and quiz selector on file.
        existing = {
            "content": {"reading_body": "div.rc-CML"},
            "video_controls": {"play_button": "button[aria-label='Play']"},
        }
        seeded = probing._seed_findings(existing)
        self.assertEqual(seeded, existing)

    def test_the_seed_does_not_alias_the_input(self):
        existing = {"content": {"reading_body": "div.rc-CML"}}
        seeded = probing._seed_findings(existing)
        seeded["content"]["reading_body"] = "div.changed"
        self.assertEqual(existing["content"]["reading_body"], "div.rc-CML")

    def test_empty_and_none_are_safe(self):
        self.assertEqual(probing._seed_findings({}), {})
        self.assertEqual(probing._seed_findings(None), {})


class CategoryFilterTests(unittest.TestCase):
    def test_a_video_page_skips_the_reading_category(self):
        # Scanning every category everywhere let video-player text match a
        # reading-body selector.
        self.assertNotIn("content", discovery.categories_to_scan("VIDEO"))
        self.assertIn("video_controls", discovery.categories_to_scan("VIDEO"))

    def test_a_reading_page_skips_the_video_category(self):
        self.assertNotIn("video_controls", discovery.categories_to_scan("READING"))
        self.assertIn("content", discovery.categories_to_scan("READING"))

    def test_graded_types_scan_only_the_common_categories(self):
        # There is no "quiz" category in the schema; the old table named one,
        # so graded pages silently scanned nothing but the common set anyway.
        for page_type in ("QUIZ", "ASSIGNMENT"):
            self.assertEqual(discovery.categories_to_scan(page_type), probing._COMMON, page_type)

    def test_every_category_key_is_a_type_the_detector_can_return(self):
        # "PEER_REVIEW" and "REVIEW_PEERS" were keyed here but detect_page_type
        # returns "ASSIGNMENT" for /peer/, so those entries never applied.
        detectable = {page_type for page_type, _ in rules.URL_SEGMENT_MARKERS}
        detectable |= {page_type for page_type, _ in rules.TITLE_MARKERS}
        for page_type in probing.RELEVANT_CATEGORIES:
            self.assertIn(page_type, detectable, page_type)

    def test_an_lti_lab_is_recognised_rather_than_left_unknown(self):
        # An UNKNOWN type falls back to scanning every category on the page.
        page = FakePage(url="https://www.coursera.org/learn/c/ungradedLti/abc/tool")
        self.assertEqual(discovery.detect_page_type(page), "LAB")

    def test_every_named_category_exists_in_the_schema(self):
        # Regression: transcript elements sat in "content", which is only
        # scanned on reading pages, so they could never be verified at all.
        for page_type, categories in probing.RELEVANT_CATEGORIES.items():
            for category in categories:
                self.assertIn(
                    category, element_schema.ELEMENTS_SCHEMA, f"{page_type} -> {category}"
                )

    def test_a_video_page_verifies_the_transcript_elements(self):
        self.assertIn("transcript", discovery.categories_to_scan("VIDEO"))

    def test_every_page_type_verifies_the_sidebar_icon(self):
        for page_type in probing.RELEVANT_CATEGORIES:
            self.assertIn("sidebar", discovery.categories_to_scan(page_type), page_type)

    def test_an_unknown_page_type_scans_everything(self):
        self.assertEqual(
            set(discovery.categories_to_scan("UNKNOWN")), set(element_schema.ELEMENTS_SCHEMA)
        )


class HrefMatchTests(unittest.TestCase):
    def test_relative_href_matches_the_absolute_url(self):
        self.assertTrue(
            context._href_matches(
                "/learn/demo/lecture/abc/one", "https://www.coursera.org/learn/demo/lecture/abc/one"
            )
        )

    def test_a_different_item_does_not_match(self):
        self.assertFalse(
            context._href_matches(
                "/learn/demo/lecture/zzz/two", "https://www.coursera.org/learn/demo/lecture/abc/one"
            )
        )

    def test_non_learn_and_empty_hrefs_are_rejected(self):
        self.assertFalse(context._href_matches("/about", "https://x/learn/demo/lecture/a/b"))
        self.assertFalse(context._href_matches(None, "https://x/learn/demo/lecture/a/b"))


class _OrderedSidebar:
    """Playwright-like locator preserving mixed header/link document order."""

    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]

    def all(self):
        return self.items


class BrowserCourseMapTests(unittest.TestCase):
    def test_browser_rows_are_grouped_and_classified(self):
        rows = _OrderedSidebar(
            [
                FakeLocator(count=1, text="Module 1", tag="BUTTON"),
                FakeLocator(
                    count=1,
                    text="Welcome\nVideo | 3 min",
                    tag="A",
                    attributes={"href": "/learn/demo/lecture/aaa/welcome"},
                ),
                FakeLocator(
                    count=1,
                    text="Notes\nReading | 5 min",
                    tag="A",
                    attributes={"href": "/learn/demo/supplement/bbb/notes"},
                ),
            ]
        )
        page = FakePage(locators={course_map.SIDEBAR_ITEM_SELECTOR: rows})

        with mock.patch("time.sleep"):
            outline = discovery.get_detailed_course_map(page)

        self.assertEqual(
            outline,
            {
                "Module 1": [
                    ("Welcome", "VIDEO", "/learn/demo/lecture/aaa/welcome", "3 min"),
                    ("Notes", "READING", "/learn/demo/supplement/bbb/notes", "5 min"),
                ]
            },
        )


class CompletionStatusTests(unittest.TestCase):
    def test_sidebar_rows_are_mapped_to_completed_booleans(self):
        completed = FakeLocator(
            count=1,
            tag="A",
            attributes={
                "href": "/learn/demo/lecture/aaa/welcome",
                "aria-label": "Video, Welcome, Completed, 3 min",
            },
        )
        incomplete = FakeLocator(
            count=1,
            tag="A",
            attributes={
                "href": "/learn/demo/supplement/bbb/notes",
                "aria-label": "Reading, Notes, Not submitted, 5 min",
            },
        )
        page = FakePage(locators={"a[href*='/learn/']": _OrderedSidebar([completed, incomplete])})

        self.assertEqual(
            discovery.get_completion_status(page),
            {
                "/learn/demo/lecture/aaa/welcome": True,
                "/learn/demo/supplement/bbb/notes": False,
            },
        )

    def test_tree_prints_completed_and_incomplete_markers(self):
        outline = {
            "Module 1": [
                ("Welcome", "VIDEO", "/learn/demo/lecture/aaa/welcome", "3 min"),
                ("Notes", "READING", "/learn/demo/supplement/bbb/notes", "5 min"),
            ]
        }
        with capture_console() as output:
            discovery.print_course_map(
                outline,
                "Demo Course",
                {
                    "/learn/demo/lecture/aaa/welcome": True,
                    "/learn/demo/supplement/bbb/notes": False,
                },
            )

        rendered = output.getvalue()
        self.assertIn("Phantadex Dex", rendered)
        self.assertIn("✓ Welcome · video · 3 min", rendered)
        self.assertIn("○ Notes · reading · 5 min", rendered)
        self.assertNotIn("╔", rendered)

    def test_tree_marks_missing_sidebar_status_as_unknown(self):
        outline = {"Module 1": [("Notes", "READING", "/learn/demo/supplement/bbb/notes", "5 min")]}
        with capture_console() as output:
            discovery.print_course_map(outline, "Demo Course", {})

        self.assertIn("? Notes · reading · 5 min", output.getvalue())


class PointerNavigationTests(unittest.TestCase):
    def test_auto_hop_moves_before_clicking_next(self):
        page = FakePage()
        button = FakeLocator(count=1, events=page.pointer_events)
        with (
            mock.patch.object(probing, "find_element_in_frames", return_value=(button, "main")),
            mock.patch("time.sleep"),
            mock.patch("phantadex.interaction.time.sleep"),
        ):
            self.assertTrue(
                discovery.auto_hop_next(page, {"navigation": {"next_item": ["button.next"]}})
            )
        self.assertEqual(page.pointer_events, ["move", "click"])

    def test_sidebar_expansion_moves_before_clicking(self):
        page = FakePage()
        button = FakeLocator(count=1, events=page.pointer_events)
        page.locators["button.cds-AccordionHeader-button[aria-expanded='false']"] = button
        with mock.patch("time.sleep"), mock.patch("phantadex.interaction.time.sleep"):
            self.assertTrue(discovery.expand_sidebar(page))
        self.assertEqual(page.pointer_events, ["move", "click"])


class LiveSelectorContractTests(unittest.TestCase):
    def test_files_and_txt_download_selectors_precede_legacy_fallbacks(self):
        files = element_schema.ELEMENTS_SCHEMA["transcript"]["downloads_tab"]["selectors"]
        transcripts = element_schema.ELEMENTS_SCHEMA["transcript"]["transcript_download_link"][
            "selectors"
        ]

        self.assertEqual(files[0], "[data-testid='item-tool-panel-button-files']")
        self.assertEqual(transcripts[0], "a[download='transcript.txt']")


class SelectorDiscoveryFlowTests(unittest.TestCase):
    def test_video_page_probes_and_saves_relevant_live_selectors(self):
        selectors = {
            details["selectors"][0]: FakeLocator(count=1, text=name)
            for category in discovery.categories_to_scan("VIDEO")
            for name, details in element_schema.ELEMENTS_SCHEMA[category].items()
        }
        page = FakePage(locators=selectors)

        state = ObservationState()
        with mock.patch.object(probing, "_save_findings") as save, mock.patch("time.sleep"):
            findings = probing.discover_selectors(page, state)

        self.assertEqual(state.discovered_types, {"VIDEO"})
        self.assertIs(state.selectors, findings)
        self.assertEqual(
            findings["transcript"]["downloads_tab"],
            "[data-testid='item-tool-panel-button-files']",
        )
        save.assert_called_once_with(findings)


class PackageBoundaryTests(unittest.TestCase):
    """The split is only worth having if the dependencies stay one-way."""

    @staticmethod
    def _imported_modules(module):
        tree = ast.parse(Path(module.__file__).read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names.add(node.module or "")
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
        return names

    def test_the_runtime_schema_does_not_import_the_discovery_tool(self):
        # schema.py used to reach into discovery for ELEMENTS_SCHEMA, which put
        # the whole discovery tool behind every runtime selector lookup.
        self.assertNotIn("discovery", self._imported_modules(schema))
        self.assertIn("element_schema", self._imported_modules(schema))

    def test_the_rules_are_free_of_the_layers_above_them(self):
        for forbidden in ("context", "course_map", "probing", "observation"):
            self.assertNotIn(forbidden, self._imported_modules(rules))

    def test_only_the_observation_loop_navigates(self):
        for module in (rules, context, course_map, probing):
            self.assertNotIn("observation", self._imported_modules(module))

    def test_the_facade_exports_no_private_names(self):
        self.assertFalse([name for name in discovery.__all__ if name.startswith("_")])


class ObservationStateTests(unittest.TestCase):
    """Session state is explicit, not hidden on module or function attributes."""

    def test_the_module_is_read_from_the_map_it_is_given(self):
        state = ObservationState(
            course_map={"Module 2": [("Welcome", "VIDEO", "/learn/demo/lecture/aaa/welcome", "")]}
        )
        self.assertEqual(
            context._module_from_cached_map(state.course_map, "/learn/demo/lecture/aaa/welcome"),
            "Module 2",
        )

    def test_no_map_yields_no_module(self):
        self.assertIsNone(context._module_from_cached_map(None, "/learn/demo/lecture/aaa/x"))

    def test_mapping_records_the_types_the_course_actually_contains(self):
        outline = {
            "Module 1": [
                ("Welcome", "VIDEO", "/learn/demo/lecture/aaa/welcome", "3 min"),
                ("Notes", "READING", "/learn/demo/supplement/bbb/notes", "5 min"),
            ]
        }
        state = ObservationState()
        page = FakePage()
        with (
            mock.patch.object(
                observation.course_map, "get_detailed_course_map", return_value=outline
            ),
            mock.patch.object(observation.context, "get_robust_course_name", return_value="Demo"),
            capture_console(),
        ):
            targets = observation.get_sidebar_targets(page, state)

        self.assertEqual(state.required_types, {"VIDEO", "READING"})
        self.assertEqual(targets["VIDEO"], "/learn/demo/lecture/aaa/welcome")
        self.assertIs(state.course_map, outline)
        self.assertTrue(state.mapped)

    def test_the_map_is_printed_once_per_session(self):
        state = ObservationState()
        with (
            mock.patch.object(observation.course_map, "get_detailed_course_map", return_value={}),
            mock.patch.object(observation.context, "get_robust_course_name", return_value="Demo"),
            mock.patch.object(observation.course_map, "print_course_map") as printed,
            capture_console(),
        ):
            observation.get_sidebar_targets(FakePage(), state)
            observation.get_sidebar_targets(FakePage(), state)

        self.assertEqual(printed.call_count, 1)


class SmartHopTests(unittest.TestCase):
    def test_a_relative_sidebar_href_is_made_absolute_before_the_jump(self):
        # The jump used to concatenate a hardcoded origin onto the href.
        state = ObservationState(required_types={"READING"})
        page = FakePage()
        with (
            mock.patch.object(
                observation,
                "get_sidebar_targets",
                return_value={"READING": "/learn/demo/supplement/bbb/notes"},
            ),
            capture_console(),
        ):
            self.assertTrue(observation.auto_hop_smart(page, {}, state))

        self.assertEqual(
            page.goto_calls, ["https://www.coursera.org/learn/demo/supplement/bbb/notes"]
        )

    def test_nothing_missing_ends_the_session(self):
        state = ObservationState(required_types={"VIDEO"}, discovered_types={"VIDEO"})
        with (
            mock.patch.object(observation, "get_sidebar_targets", return_value={}),
            capture_console(),
        ):
            self.assertFalse(observation.auto_hop_smart(FakePage(), {}, state))


if __name__ == "__main__":
    unittest.main()
