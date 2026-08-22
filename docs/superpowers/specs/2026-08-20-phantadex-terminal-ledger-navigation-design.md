# Phantadex Terminal, Ledger, and Watch Recovery Design

## Goal

Make Archive and Dex share one compact terminal theme, replace vendor-branded
archive paths with Phantadex names, and prevent Watch from treating a locked or
missing reading pane as scrollable content.

## Scope

- Remove the blank line before every Archive item.
- Add one dependency-free, TTY-only spinner around course-map generation.
- Render Dex with the existing Phantadex banner, item, step, success, warning,
  colour, `NO_COLOR`, and redirected-output conventions.
- Change the default archive root from `coursera_transcripts` to
  `phantadex_archive`.
- Name each ledger from the stable course URL slug, for example
  `example-course.pdex.xml`.
- Migrate the legacy default course directory and `course_content.xml` without
  overwriting an existing destination.
- Detect Coursera's locked-item interstitial before content classification,
  return to the previous mapped item, and verify that navigation occurred.
- Prevent a missing reading body from starting a synthetic dwell/scroll pass.
- Clear active progress output on Ctrl+C and disconnect Playwright without
  closing the user's Chrome.
- Remove vendor naming from user-visible Phantadex paths, headings, internal
  helper names, and current documentation when it carries no technical meaning.

Platform hostnames, URLs, selector descriptions, migration constants, and
historical changelog entries retain `Coursera` where removing it would obscure
the real integration or falsify history.

## Terminal Rendering

`phantadex.logs` remains the only terminal presentation boundary. Archive will
use the existing item renderer in compact mode so consecutive items have no
blank rows. Watch keeps spaced item sections because the space separates full
handler lifecycles.

Dex will stop printing fixed-width box art. Its output will be:

```text
Phantadex Dex
  Introduction to Data Analysis
  · 35 items · 11 complete · 24 remaining

▎ Lesson 1. Introduction
  ✓ Welcome · video · 3 min
  ○ Profitability ratios · reading · 5 min
  ? Completion unavailable · lab · 15 min
```

Completed rows use the existing green success form, incomplete rows use a dim
pending form, and unknown rows use yellow. Long titles rely on terminal-width
truncation rather than a fixed 68-column frame.

The spinner uses only the standard library, animates only on an interactive
terminal, clears its line before subsequent output, and degrades to one static
step when output is redirected. It is limited to course-map generation so it
cannot compete with Watch's reading/video progress bar.

## Archive and Ledger Identity

The configured archive root remains overridable with `--transcript-dir`; only
the default changes to `phantadex_archive`. The course directory continues to
use the sanitized display title so existing text filenames and their relative
layout remain stable.

The ledger basename comes from the first valid course-map path:

```text
/learn/example-course/lecture/... -> example-course.pdex.xml
```

The platform slug is preferable to a title acronym because it is already short,
stable across display-title edits, and collision-resistant. If no slug can be
derived, the safe fallback is `course.pdex.xml`.

Migration runs before ledger initialization:

1. When the new default course directory is absent and the matching legacy
   directory exists, atomically move the whole course directory to
   `phantadex_archive`.
2. Under the existing per-course lock, when the slug ledger is absent and
   `course_content.xml` exists, atomically rename the legacy ledger.
3. If both source and destination exist, never overwrite or merge implicitly;
   retain both, use the configured destination, and emit an actionable warning.
4. Custom `--transcript-dir` values are never moved automatically.

## Watch Recovery

The screenshot's behavior has two causes: URL-segment classification identifies
the locked `/supplement/` route as a reading, and the generic scroller then picks
the sidebar when the main content pane is absent. This produces a false scroll
percentage and a ten-minute dwell.

Before classification, Watch will detect the locked interstitial through a
visible configured Previous Item control. It will:

1. Log that the item is locked.
2. Use the shared pointer-move click path on `Go to Previous Item`.
3. Poll for an item URL change.
4. Fall back to the previous mapped URL when the click is unavailable or inert.
5. Return to the loop, allowing the existing graded-item handler to pause for
   manual completion when the previous item is an assessment.

`CourseManager` gains the inverse of `get_next_url()` for this fallback. A
normal reading whose body remains absent is treated as a handler failure, not a
reading session; existing bounded retries then advance rather than scrolling a
wrong container forever.

## Interruption Safety

Every CLI KeyboardInterrupt path clears the progress line before printing one
concise interruption message. Browser disconnection will use only documented
Playwright lifecycle calls and will be verified first against a disposable
Chrome debugging profile. It must leave the user's Chrome process, contexts,
tabs, and authenticated state open.

## Compatibility and Verification

- No new runtime dependency.
- Python 3.8+, macOS, and Windows remain supported.
- Existing archive text files, XML content, course-map tuples, CLI commands,
  completion semantics, and custom archive roots remain valid.
- Each behavior is implemented test-first with a witnessed failing test:
  compact Archive rows, non-TTY spinner fallback, themed Dex states, course slug
  derivation, safe directory/ledger migration, previous-URL lookup, locked-page
  recovery, missing-reading rejection, and clean interruption.
- Final verification includes the complete offline suite, correctness/import
  Ruff gate, compilation, wheel contents, whitespace checks, disposable-browser
  disconnect verification, and live Dex/Watch/Archive smoke tests.
