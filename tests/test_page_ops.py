"""Transcript extraction, including the download path the live UI rarely offers."""

import os
import tempfile
import unittest
from unittest import mock

from phantadex import page_ops
from tests.fakes import FakeLocator, FakePage


class FakeDownload:
    def __init__(self, path):
        self._path = path

    def path(self):
        return self._path


class FakeDownloadContext:
    def __init__(self, download):
        self.value = download

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class ExtractTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.page = FakePage(url="/learn/c/lecture/aaa/one")

    def test_panel_scrape_is_cleaned(self):
        raw = "Play video starting at ::5 and follow transcript\n0:05\n​Some speech.\xa0More."
        with mock.patch.object(page_ops, "_transcript_from_panel", return_value=raw):
            text, method = page_ops.extract_transcript(self.page)
        self.assertEqual(method, "UI_Scrape")
        self.assertEqual(text, "0:05\nSome speech. More.")

    def test_download_fallback_runs_when_the_panel_is_empty(self):
        with mock.patch.object(
            page_ops, "_transcript_from_panel", return_value=None
        ), mock.patch.object(page_ops, "_transcript_from_download", return_value="Body."):
            text, method = page_ops.extract_transcript(self.page)
        self.assertEqual((text, method), ("Body.", "File_Download"))

    def test_both_stages_failing_reports_failure(self):
        with mock.patch.object(
            page_ops, "_transcript_from_panel", return_value=None
        ), mock.patch.object(page_ops, "_transcript_from_download", return_value=None):
            self.assertEqual(page_ops.extract_transcript(self.page), (None, "FAILED"))


class TranscriptDownloadTests(unittest.TestCase):
    """The Downloads tab is absent on some courses; the code must not assume it."""

    def setUp(self):
        self.page = FakePage(url="/learn/c/lecture/aaa/one")
        handle, self.tmp = tempfile.mkstemp(suffix=".txt")
        os.close(handle)
        self.addCleanup(os.unlink, self.tmp)

    def _with_link(self, link):
        self.page.locators = {
            selector: link
            for selector in page_ops.schema.selectors_for("transcript", "transcript_download_link")
        }

    def test_no_downloads_tab_returns_none_without_clicking(self):
        with mock.patch.object(page_ops.schema, "first_visible", return_value=None):
            self.assertIsNone(page_ops._transcript_from_download(self.page))

    def test_downloaded_file_is_read(self):
        with open(self.tmp, "w", encoding="utf-8") as handle:
            handle.write("  Downloaded transcript body.  ")
        tab = FakeLocator(count=1)
        link = FakeLocator(count=1)
        self.page.expect_download = lambda **_kw: FakeDownloadContext(FakeDownload(self.tmp))
        with mock.patch.object(
            page_ops.schema, "first_visible", return_value=tab
        ), mock.patch.object(
            page_ops, "_first_visible_transcript_download", side_effect=[None, link]
        ), mock.patch.object(page_ops.time, "sleep"):
            self.assertEqual(
                page_ops._transcript_from_download(self.page), "Downloaded transcript body."
            )
        self.assertEqual(tab.clicked, 1)

    def test_files_and_transcript_clicks_move_the_pointer_first(self):
        with open(self.tmp, "w", encoding="utf-8") as handle:
            handle.write("Downloaded transcript body.")
        tab = FakeLocator(count=1, events=self.page.pointer_events)
        link = FakeLocator(count=1, events=self.page.pointer_events)
        self.page.expect_download = lambda **_kw: FakeDownloadContext(FakeDownload(self.tmp))

        with mock.patch.object(
            page_ops.schema, "first_visible", return_value=tab
        ), mock.patch.object(
            page_ops, "_first_visible_transcript_download", side_effect=[None, link]
        ), mock.patch("phantadex.interaction.time.sleep"):
            self.assertEqual(
                page_ops._transcript_from_download(self.page), "Downloaded transcript body."
            )

        self.assertEqual(self.page.pointer_events, ["move", "click", "move", "click"])

    def test_an_already_open_files_panel_is_not_toggled_closed(self):
        with open(self.tmp, "w", encoding="utf-8") as handle:
            handle.write("Downloaded transcript body.")
        tab = FakeLocator(count=1)
        self._with_link(FakeLocator(count=1))
        self.page.expect_download = lambda **_kw: FakeDownloadContext(FakeDownload(self.tmp))

        with mock.patch.object(
            page_ops.schema, "first_visible", return_value=tab
        ), mock.patch.object(page_ops.time, "sleep"):
            self.assertEqual(
                page_ops._transcript_from_download(self.page), "Downloaded transcript body."
            )

        self.assertEqual(tab.clicked, 0)

    def test_an_unrelated_transcript_link_is_not_used_before_files_opens(self):
        with open(self.tmp, "w", encoding="utf-8") as handle:
            handle.write("Downloaded transcript body.")
        unrelated = FakeLocator(count=1)
        transcript = FakeLocator(count=1)

        class OpeningFilesTab(FakeLocator):
            def click(tab_self, **_kwargs):
                super().click(**_kwargs)
                self.page.locators["a[download='transcript.txt']"] = transcript

        self.page.locators = {
            "a:has-text('Transcript')": unrelated,
        }
        self.page.expect_download = lambda **_kw: FakeDownloadContext(FakeDownload(self.tmp))

        with mock.patch.object(
            page_ops.schema, "first_visible", return_value=OpeningFilesTab(count=1)
        ), mock.patch.object(page_ops.time, "sleep"):
            self.assertEqual(
                page_ops._transcript_from_download(self.page), "Downloaded transcript body."
            )

        self.assertEqual(unrelated.clicked, 0)
        self.assertEqual(transcript.clicked, 1)

    def test_an_empty_download_is_not_returned(self):
        tab = FakeLocator(count=1)
        self._with_link(FakeLocator(count=1))
        self.page.expect_download = lambda **_kw: FakeDownloadContext(FakeDownload(self.tmp))
        with mock.patch.object(
            page_ops.schema, "first_visible", return_value=tab
        ), mock.patch.object(page_ops.time, "sleep"):
            self.assertIsNone(page_ops._transcript_from_download(self.page))

    def test_a_missing_file_on_disk_is_not_returned(self):
        tab = FakeLocator(count=1)
        self._with_link(FakeLocator(count=1))
        self.page.expect_download = lambda **_kw: FakeDownloadContext(FakeDownload("/no/such/file"))
        with mock.patch.object(
            page_ops.schema, "first_visible", return_value=tab
        ), mock.patch.object(page_ops.time, "sleep"):
            self.assertIsNone(page_ops._transcript_from_download(self.page))

    def test_an_invisible_link_is_skipped(self):
        tab = FakeLocator(count=1)
        self._with_link(FakeLocator(count=1, visible=False))
        with mock.patch.object(
            page_ops.schema, "first_visible", return_value=tab
        ), mock.patch.object(page_ops.time, "sleep"):
            self.assertIsNone(page_ops._transcript_from_download(self.page))


class _SidebarRows:
    """A locator over sidebar links, each with its own href and text."""

    def __init__(self, rows):
        self._rows = rows

    def count(self):
        return len(self._rows)

    def nth(self, index):
        href, text = self._rows[index]
        return FakeLocator(count=1, text=text, attributes={"href": href})


class _SidebarLinks:
    """A locator whose links can carry distinct completion state."""

    def __init__(self, links):
        self._links = links

    def all(self):
        return self._links


class CompletionStateTests(unittest.TestCase):
    @staticmethod
    def _nav_controls(previous, next_item):
        """Patches schema lookups so only the navigation controls are configurable."""

        def lookup(_page, category, element_name):
            if (category, element_name) == ("navigation", "previous_item"):
                return previous
            if (category, element_name) == ("navigation", "next_item"):
                return next_item
            return None

        return mock.patch.object(page_ops.schema, "first_visible", side_effect=lookup)

    def test_previous_control_without_a_way_forward_is_a_locked_item(self):
        with self._nav_controls(FakeLocator(count=1), None):
            self.assertTrue(page_ops.is_locked_item(FakePage()))

    def test_the_ordinary_pager_is_not_a_locked_item(self):
        # Regression: Coursera's normal pager carries both controls, and
        # ":has-text('Previous Item')" matches its label case-insensitively.
        # Keying on the Previous control alone marked every item after the
        # first as locked, so the runner never processed any content.
        with self._nav_controls(FakeLocator(count=1), FakeLocator(count=1)):
            self.assertFalse(page_ops.is_locked_item(FakePage()))

    def test_normal_item_is_not_locked(self):
        with self._nav_controls(None, FakeLocator(count=1)):
            self.assertFalse(page_ops.is_locked_item(FakePage()))

    def test_another_completed_row_does_not_complete_the_current_item(self):
        active = FakeLocator(
            count=1,
            attributes={
                "href": "/learn/demo/supplement/abc/notes",
                "aria-label": "Reading, Notes, Not submitted, 5 min",
            },
        )
        other = FakeLocator(
            count=1,
            attributes={
                "href": "/learn/demo/lecture/zzz/other",
                "aria-label": "Video, Other, Completed, 4 min",
            },
        )
        page = FakePage(
            url="https://www.coursera.org/learn/demo/supplement/abc/notes",
            locators={
                "a[href*='/learn/']": _SidebarLinks([active, other]),
                "a[aria-label*='Completed']": other,
            },
        )

        with mock.patch.object(page_ops.time, "sleep"):
            self.assertFalse(page_ops.is_already_completed(page))

    def test_current_rows_completed_aria_label_is_recognised(self):
        active = FakeLocator(
            count=1,
            attributes={
                "href": "/learn/demo/supplement/abc/notes",
                "aria-label": "Reading, Notes, Completed, 5 min",
            },
        )
        page = FakePage(
            url="https://www.coursera.org/learn/demo/supplement/abc/notes",
            locators={"a[href*='/learn/']": _SidebarLinks([active])},
        )

        with mock.patch.object(page_ops.time, "sleep"):
            self.assertTrue(page_ops.is_already_completed(page))


class ReadingMinutesTests(unittest.TestCase):
    def test_falls_back_to_the_active_sidebar_row(self):
        # Regression: every header scope returns zero matches on the courses
        # measured, so the duration was always a random default.
        page = FakePage(
            url="https://www.coursera.org/learn/demo/supplement/abc/notes",
            locators={
                "a[href*='/learn/']": _SidebarRows(
                    [
                        ("/learn/demo/lecture/zzz/other", "Other\nVideo | 4 min"),
                        ("/learn/demo/supplement/abc/notes", "Notes\nReading | 18 min"),
                    ]
                )
            },
        )
        self.assertEqual(page_ops.detect_reading_minutes(page), (18, "sidebar"))

    def test_another_items_duration_is_not_used(self):
        page = FakePage(
            url="https://www.coursera.org/learn/demo/supplement/abc/notes",
            locators={
                "a[href*='/learn/']": _SidebarRows(
                    [("/learn/demo/lecture/zzz/other", "Other\nVideo | 4 min")]
                )
            },
        )
        minutes, source = page_ops.detect_reading_minutes(page, (7, 7))
        self.assertEqual((minutes, source), (7, "default"))

    def test_header_duration_wins_over_the_sidebar(self):
        page = FakePage(
            url="https://www.coursera.org/learn/demo/supplement/abc/notes",
            locators={
                "main header": FakeLocator(count=1, text="Notes\nReading | 3 min"),
                "a[href*='/learn/']": _SidebarRows(
                    [("/learn/demo/supplement/abc/notes", "Notes\nReading | 18 min")]
                ),
            },
        )
        self.assertEqual(page_ops.detect_reading_minutes(page), (3, "header"))


if __name__ == "__main__":
    unittest.main()
