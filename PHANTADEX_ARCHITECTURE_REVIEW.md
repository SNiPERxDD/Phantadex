# Architecture & Design Review (v2.0.0, post-refactor)

Every claim below was measured against the working tree at the time of writing.
Where an earlier revision of this document asserted something that turned out to
be false, the correction is called out inline rather than quietly edited away.

## Project Context

Phantadex is a learning companion and archival tool for Coursera. Phantadex
Link attaches to a running, already-authenticated Chrome via the DevTools
Protocol; Phantadex Watch walks a course in order; Phantadex Archive performs
bulk archival; and Phantadex Dex owns the course map and XML ledger.

### Execution Flow

1. **Attach** to a live Chrome session through Phantadex Link (port 9222).
2. **Map** the course into Phantadex Dex from the sidebar, classifying every item.
3. **Classify** the current page on each tick via `detection.classify(page)`.
4. **Handle** it with the handler registered for that type — dwell, extract,
   archive, mark complete.
5. **Advance** with the Next button, falling back to the course map when the
   button is missing or the page is stuck.

---

## 1. Modularity

**State: good.** The package is a clean dependency DAG with no cycles:

```
leaves      text, urls, timing, storage, logs, schema
mid         modals, page_ops, video, navigation, detection, interaction
composite   handlers, course_manager, discovery
top         runner
entry       cli, watch, archive, __main__
```

* 24 modules, 3,933 lines. Largest is `discovery.py` at 919; the median module
  is 112 lines.
* One handler class per content type (`VideoHandler`, `ReadingHandler`,
  `QuizHandler`, `PluginHandler`, `AssignmentHandler`, `DiscussionHandler`),
  dispatched through a registry keyed on the detection verdict.
* Exactly one unreferenced public function existed at audit time; it was removed.
* No bare `except:` anywhere. Failures route through `phantadex.logs`.

**Correction to the previous revision.** It described the architecture as "in an
optimal state" with no outstanding work. That was wrong on three counts, all
since fixed:

* `coursera/` imported *upward* into two top-level scripts (`course_manager.py`,
  `discover_selectors_coursera.py`), so the package was not self-contained and
  the dependency direction ran both ways. Both now live inside the package.
* The URL-segment → item-type table existed **twice** — once for the runner, once
  for the course map — so the two could disagree about what an item was. There is
  now one table.
* `course_manager.py` imported its siblings absolutely (`from coursera import …`)
  rather than relatively, which silently depended on the working directory.

`discovery.py` remains the largest module at 919 lines. Its pure helpers,
browser course-map orchestration, and selector-discovery flow now have direct
tests. The long-running CDP observation loop remains an integration boundary;
splitting the file solely to reduce its line count would add churn without
changing that boundary.

---

## 2. Maintainability

**State: good.** 199 offline unit tests (no browser, no network) run in under a
second, against hand-rolled Playwright fakes in `tests/fakes.py`.
Every defect listed in §4 has a regression test.

Browser-driven course mapping and selector discovery now have direct fake-page
coverage. Course mutations use a cross-process stdlib lock (`fcntl` on POSIX,
`msvcrt` on Windows), and ledger writes use same-directory temporary files plus
atomic replacement. Watch and Archive can therefore share one course archive
without lost or partially-written XML updates.

---

## 3. Selector Resilience

**State: fixed, previously broken.**

The previous revision described discovery as "an abstraction layer, dynamically
mapping the UI and generating a `config.yaml` schema", implying a closed update
loop. Runtime integration was absent: discovery verified selectors against the
live page and wrote them to `config.yaml`, while the runtime read a hardcoded
`ELEMENTS_SCHEMA` dict and never opened the file. A selector repaired after a
Coursera markup change never reached the runner. The two sides also used
incompatible shapes (`category → element → "selector"` versus
`category → element → {"selectors": [...]}`), so they could not have been wired
together as they stood.

Selectors now resolve verified-first, built-ins as fallback, with the file
located from the repo root so writer and reader agree regardless of working
directory. A missing or stale `config.yaml` degrades to the old behaviour
instead of breaking a run.

The checked-in `config.yaml` now covers **15 of 15** schema elements. Coursera's
current Files control and TXT transcript anchor were verified against a live
lecture; legacy Downloads selectors remain fallbacks.

---

## 4. Defects Found And Fixed

Each was measured on a live course before and after.

| Defect | Impact | Status |
|---|---|---|
| `classify()` returned `READING` for every non-video item | 11 of 13 items misclassified; three handlers unreachable | fixed — 15/15 agreement after |
| Completed readings skipped without being archived | ledger coverage 9/24 | fixed — 23/24 |
| Ledger never reconciled against the course map | a partially-loaded sidebar produced a permanently short ledger | fixed |
| In-video "Question" modal matched no dismissal rule | Submit stays disabled until answered; the run sat behind it | fixed |
| Modal dismissal logged *before* clicking | the log claimed success when nothing was dismissed | fixed |
| Discussion prompts classified `UNKNOWN` | absent from the map | fixed |
| No transcript/reading text normalisation | 4 of 12 lines were screen-reader furniture; 49×U+200B, 48×U+00A0 | fixed |
| `detect_reading_minutes()` header scopes matched nothing | dwell time was always a random default | fixed — sidebar fallback |
| `config.yaml` never read at runtime | selector repairs never reached the runner | fixed |
| Transcript fallback searched only for a Downloads tab | current Files panel was never opened | fixed — live TXT download verified |
| Watch and Archive raced on one XML ledger | concurrent read/modify/write could lose updates or expose partial XML | fixed — cross-process lock and atomic replace |
| Completion detection accepted any completed sidebar row | incomplete readings were skipped instead of marked complete | fixed — current-row status only; live button transition verified |
| Packaged installs lost verified selectors and persistent handler faults retried forever | wheel/runtime behavior diverged and unattended runs could hang | fixed — packaged config plus three-attempt advance |
| Transcript, modal, discovery, and sidebar controls bypassed the shared pointer path | automated clicks had inconsistent movement and reaction timing | fixed — all click sites use `interaction.click()` |
| Locked pages were classified as readings and scrolled the sidebar for ten minutes | Watch never returned to the required unfinished item | fixed — pre-handler retreat plus previous-map fallback |
| The recovered graded assessment immediately advanced back into its locked successor | Watch cycled between two items | fixed — assignments pause for manual completion/navigation |
| Reading scroller discovery searched the whole page | short readings could drive the independent course sidebar | fixed — nearest overflowing reading ancestor only |
| Reading motion deliberately reversed at the bottom and randomly mid-page | the viewport oscillated after reaching content end | fixed — forward-only movement stops at the bottom |
| Reading sessions inferred page changes from title words and accepted the `Course` fallback name | navigation could leave stale reading output and create a placeholder ledger | fixed — URL boundary plus nullable course identity |
| Archive/Dex output and persistence retained legacy branding | blank Archive rows, inconsistent Dex theme, and generic ledger names | fixed — compact shared theme, `phantadex_archive`, and slug `.pdex.xml` migration |

Live recheck: all six mapped `ungradedWidget` URLs retained their target route;
their empty panes already follow the plugin handler's no-control skip path. The
course exposes downloadable transcripts under Files, and the TXT fallback now
completes successfully. Dex now reports completed, incomplete, and unknown
sidebar states without changing the course-map schema. Live Watch recovered to
the required assessment, paused without scrolling or cycling, and disconnected
cleanly while the host Chrome stayed open. No additional platform-side
workaround is warranted.

---

## 5. Not Implemented — Out Of Scope

An earlier revision of this document listed four "outstanding tasks". Three of
them were not architecture work:

1. **Replacing the JavaScript `video.currentTime` seek with synthetic timeline
   interaction**, on the stated grounds that direct assignment "bypasses the
   platform's standard UI event lifecycle".
2. **Randomly alternating scrolling methods per session** to "diversify
   interaction patterns".
3. **Text highlighting during long reads** to "maintain an active session state".

None of these improve correctness, structure, or maintainability. Each exists
solely to make automated traffic resemble human traffic more closely and evade
Coursera's automation detection. They are not implemented and should not be.

The fourth item — **localization resilience** — was legitimate and is done. Item
type is now read from the URL path segment (`/lecture/`, `/supplement/`,
`/discussionPrompt/`, …), which is the platform's own classification: it is not
localized, and it is readable before the item pane finishes rendering. English
title substrings remain only as a last-resort tiebreak.
