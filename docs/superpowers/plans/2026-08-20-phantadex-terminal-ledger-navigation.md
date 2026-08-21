# Phantadex Terminal, Ledger, and Watch Recovery Implementation Plan

**Goal:** Unify terminal output, rebrand and safely migrate archive storage, and recover Watch from locked or missing content without regressions.

**Architecture:** Keep `phantadex.logs` as the presentation boundary, `CourseManager` as the archive identity/migration boundary, and `Runner` as the pre-handler recovery boundary. Reuse existing navigation polling, map order, locking, atomic replacement, and fake Playwright objects; add no dependency.

**Tech Stack:** Python 3.8+, stdlib logging/threading/XML/filesystem APIs, Playwright sync API, `unittest`, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-20-phantadex-terminal-ledger-navigation-design.md`

## Global Constraints

- Keep `Coursera` only for real host/URL/selector/migration/history references.
- Default archive root is `phantadex_archive`; custom `--transcript-dir` roots are never migrated.
- Ledger name is `<course-slug>.pdex.xml`, falling back to `course.pdex.xml`.
- Never overwrite when legacy and new paths both exist.
- Preserve Python 3.8, macOS, and Windows support.
- Add no dependency.

---

### Task 1: Compact Archive, Spinner, and Themed Dex

**Files:**
- Modify: `phantadex/logs.py`
- Modify: `phantadex/archive.py`
- Modify: `phantadex/runner.py`
- Modify: `phantadex/cli.py`
- Modify: `phantadex/discovery.py`
- Create: `tests/test_logs.py`
- Modify: `tests/test_discovery.py`
- Modify: `tests/test_branding.py`

**Interfaces:**
- Produces: `logs.item(title, tag="", spaced=True)` and `logs.spinner(message)`.
- Produces: `discovery.print_course_map(...)` through shared log functions, with no box art.

- [x] **Step 1: Write failing presentation tests**

```python
def test_compact_items_do_not_insert_blank_rows():
    logs.item("One", "1/2", spaced=False)
    logs.item("Two", "2/2", spaced=False)
    assert "\n\n" not in captured


def test_redirected_spinner_emits_one_static_step():
    with logs.spinner("generating course map"):
        pass
    assert captured.count("generating course map") == 1


def test_dex_uses_phantadex_theme_without_box_art():
    discovery.print_course_map(course_map, "Demo", completion_status)
    assert "Phantadex Dex" in captured
    assert "✓" in captured and "○" in captured and "?" in captured
    assert "╔" not in captured
```

- [x] **Step 2: Run the focused tests and confirm failures identify missing compact mode, spinner, and themed rendering**

Run: `python -m unittest tests.test_logs tests.test_discovery.CompletionStatusTests tests.test_branding.RuntimeVocabularyTests`

- [x] **Step 3: Implement the minimum shared rendering changes**

Add `spaced=True` to `logs.item`, a stdlib TTY-only spinner context manager, and rebuild Dex from `logs.banner`, `logs.item`, `logs.ok`, `logs.step`, and `logs.warn`. Archive passes `spaced=False`; Watch remains unchanged. Wrap only map generation in the spinner.

- [x] **Step 4: Run focused tests until green**

Run: `python -m unittest tests.test_logs tests.test_discovery.CompletionStatusTests tests.test_branding.RuntimeVocabularyTests`

---

### Task 2: Phantadex Archive Root and Slug Ledger Migration

**Files:**
- Modify: `phantadex/config.py`
- Modify: `phantadex/urls.py`
- Modify: `phantadex/course_manager.py`
- Modify: `tests/test_urls.py`
- Modify: `tests/test_course_manager.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_branding.py`

**Interfaces:**
- Produces: `urls.course_slug(value) -> str`.
- Produces: `CourseManager.xml_path` ending in `<slug>.pdex.xml`.
- Preserves: custom archive roots and every existing content filename.

- [x] **Step 1: Write failing slug, default-root, and migration tests**

```python
def test_course_slug_from_item_url():
    assert urls.course_slug("/learn/example-course/lecture/a/b") == "example-course"

def test_ledger_uses_course_slug():
    assert manager.xml_path.endswith("example-course.pdex.xml")

def test_legacy_default_course_directory_and_ledger_are_migrated():
    # Build old tree with XML content, instantiate manager, then assert the new
    # directory/ledger exists with identical content and the old path is absent.

def test_existing_destination_is_never_overwritten():
    # Create both paths with distinct sentinels and assert both survive.
```

- [x] **Step 2: Run focused tests and confirm expected failures**

Run: `python -m unittest tests.test_urls tests.test_course_manager tests.test_cli tests.test_branding`

- [x] **Step 3: Implement safe identity and migration**

Change the config default to `phantadex_archive`. Derive the slug from mapped `/learn/<slug>/...` URLs. Before creating the new course directory, atomically move only the matching legacy default course directory when the destination is absent. Under the course lock, atomically rename `course_content.xml` only when the slug ledger is absent. Warn and retain both paths on conflicts.

- [x] **Step 4: Run focused tests until green**

Run: `python -m unittest tests.test_urls tests.test_course_manager tests.test_cli tests.test_branding`

---

### Task 3: Locked-Item Retreat and Missing-Reading Guard

**Files:**
- Modify: `phantadex/config.yaml`
- Modify: `phantadex/schema.py`
- Modify: `phantadex/page_ops.py`
- Modify: `phantadex/course_manager.py`
- Modify: `phantadex/navigation.py`
- Modify: `phantadex/runner.py`
- Modify: `phantadex/handlers.py`
- Modify: `tests/fakes.py`
- Modify: `tests/test_page_ops.py`
- Modify: `tests/test_course_manager.py`
- Modify: `tests/test_navigation.py`
- Modify: `tests/test_runner.py`
- Modify: `tests/test_handlers.py`

**Interfaces:**
- Produces: `page_ops.is_locked_item(page) -> bool`.
- Produces: `CourseManager.get_previous_url(current_url) -> str | None`.
- Produces: `navigation.retreat(page, manager, start_url=None)`, mirroring `advance` outcomes.

- [x] **Step 1: Write failing recovery tests**

```python
def test_locked_item_retreats_before_classification():
    # Visible Previous Item control; assert handler lookup is never reached and
    # retreat is called once.

def test_retreat_falls_back_to_previous_mapped_url_after_inert_click():
    # Keep URL fixed through click; assert page.goto(previous URL).

def test_missing_reading_body_never_starts_reading_session():
    # extract_reading returns None; assert reading_session is not called and the
    # handler raises so Runner's bounded failure policy owns the retry.
```

- [x] **Step 2: Run focused tests and confirm failures**

Run: `python -m unittest tests.test_page_ops tests.test_course_manager tests.test_navigation tests.test_runner tests.test_handlers`

- [x] **Step 3: Implement the pre-handler guard and inverse navigation**

Add verified Previous Item selectors. In `_tick`, after map sync and before classification, retreat when the visible locked control exists. Reuse `_wait_for_move`; map fallback uses `get_previous_url`. In `ReadingHandler`, reject an absent extracted body before duration detection or scrolling.

- [x] **Step 4: Run focused tests until green**

Run: `python -m unittest tests.test_page_ops tests.test_course_manager tests.test_navigation tests.test_runner tests.test_handlers`

---

### Task 4: Clean Interruption and Safe CDP Disconnect

**Files:**
- Modify: `phantadex/logs.py`
- Modify: `phantadex/session.py`
- Modify: `phantadex/watch.py`
- Modify: `phantadex/archive.py`
- Modify: `phantadex/cli.py`
- Modify: `tests/test_logs.py`
- Modify: `tests/test_branding.py`

**Interfaces:**
- Produces: `logs.interrupted(message="Stopped by user.")`.
- Preserves: live Chrome process, contexts, tabs, and authentication.

- [x] **Step 1: Write failing interruption tests**

```python
def test_interrupted_clears_progress_and_prints_one_line():
    logs.interrupted()
    assert output.endswith("Stopped by user.\n")

def test_session_disconnects_before_stopping_playwright():
    # Assert documented Browser.close then Playwright.stop ordering using a fake
    # connected browser; never close a context owned by the user.
```

- [x] **Step 2: Run tests and confirm failures**

Run: `python -m unittest tests.test_logs tests.test_branding`

- [x] **Step 3: Implement and verify against disposable Chrome**

Centralize interruption output. Use the documented connected-browser close call to disconnect before stopping Playwright, guarded for already-disconnected targets. Launch a temporary debug Chrome profile on a non-production port, disconnect, and verify its process/tab survives before live use.

- [x] **Step 4: Run focused tests until green**

Run: `python -m unittest tests.test_logs tests.test_branding`

---

### Task 5: Rebrand Hygiene, Documentation, and Full Verification

**Files:**
- Modify: `README.md`
- Rename: `COURSERA_ARCHITECTURE_REVIEW.md` to `PHANTADEX_ARCHITECTURE_REVIEW.md`
- Modify: `HANDOVER.md`
- Modify: `CHANGELOG.md`
- Modify: touched code/tests only where a vendor name has no technical meaning

**Interfaces:**
- Preserves: real platform host/URL/selector names and historical records.

- [x] **Step 1: Audit vendor-name occurrences and classify each as required integration/history or unnecessary branding**

Run: `rg -n -i "coursera" --glob '!deleted/**'`

- [x] **Step 2: Apply surgical renames and one consolidated changelog entry**

Update defaults/examples/output and current docs. Do not rewrite historical changelog facts or obscure the supported platform.

- [x] **Step 3: Run the complete local gate**

```text
python run_tests.py
python -m ruff check --select E9,F63,F7,F82,I phantadex tests
python -m compileall -q phantadex tests
python -m build --wheel
git diff --check
```

- [x] **Step 4: Run live smoke tests**

Verify `pdex`, `pdex archive`, and `pdex watch` on the active course. Confirm compact Archive rows, themed Dex, migrated path/ledger, locked-page retreat to the previous assessment, no false reading scroll, clean Ctrl+C, and that Chrome remains open.

- [x] **Step 5: Report exact evidence and remaining platform boundary**

State test count, lint/build results, live outcomes, migration result, Windows CI coverage, and any upstream Playwright behavior that remains. Make no commit.
