# Handover

## Current state

- `pdex -h` renders `phantadex/overview.py`, not argparse. The page is derived
  from two sources so it cannot drift: `cli.COMMANDS` *is*
  `overview.COMMAND_SUMMARIES`'s key order, and the per-item lines come from each
  handler's `summary` attribute. Adding a command or a handler without adding its
  one-liner fails `tests/test_cli.py`. Do not hand-write item descriptions into
  the page. The page must not print the expanded state directory: it names
  `PHANTADEX_STATE_DIR` instead, and a test asserts the home path never appears.
- `config.program_name(subcommand)` decides the `prog=` of every parser. Left to
  argparse, each subcommand's usage line read as the bare entry point, so none of
  them could be copied and run. A name that is not one of the two console entry
  points is a compatibility wrapper, and its own filename is the honest usage.
- The ungraded rows carry two map labels: `UNGRADED_PLUGIN` for
  `/ungradedWidget/` and `/ungradedLti/`, `LAB` for `/ungradedLab/`. Both are
  collapsed onto the single plugin handler by `detection._LABEL_TO_TYPE`, so this
  is a naming distinction in the map only -- do not add a second handler for it.

- Branch: `refactor/package-boundaries-and-schema`.
- Package renamed from `rotomdex/` to `phantadex/`; imports use `phantadex`.
- Package commands are `phantadex` and `pdex`; both dispatch Dex, Skip, Watch,
  Archive, Discover, Chrome, and Stop. With no subcommand, both default to Dex.
- `pdex stop` ends every Phantadex process on the machine. Runs are found in the
  process table (`phantadex/processes.py`), not a registry file, so a process
  from an older build or a compatibility script is found too. Note that
  `os.kill(pid, 0)` is not a liveness probe on Windows -- signal 0 is
  `CTRL_C_EVENT` and interrupts the caller's own console group; the Win32
  handle is read instead.
- Observable delays are drawn from `phantadex/jitter.py`, a log-normal over the
  requested span with a 4% chance of a longer pause, rather than a uniform
  draw. Its mean matches the uniform draw it replaced, so run pace is
  unchanged. Percentages and pixel offsets stay uniform on purpose. Callers
  sleep on the returned value themselves, so tests that patch a caller's
  `time.sleep` still work.
- Watch plays videos through by default (`--skip` restores the seek into
  `97.5-98.5%`), and its completion target is sampled once per video from
  `--video-threshold`, which defaults to `98-100`. `Settings` normalises a bare
  number into a range of zero width, so callers passing a scalar still work.
  `pdex skip` still always seeks.
- Media is silenced by a guard installed on the browser context
  (`interaction.install_media_guard`, armed on already-open tabs by
  `BrowserSession`), which runs before the page's own scripts and mutes at
  `loadstart`. `silence_media` is kept as the per-tick sweep. The guard checks
  the platform host first: the context is the user's own Chrome profile, and
  muting their unrelated tabs would be a side effect of automating this one.
- An archived item missing from the ledger is adopted into it
  (`CourseManager._adopt_item`) rather than logged as `ledger unmatched`. Map
  row types and live page classification disagree often enough -- a
  survey-titled supplement maps to `FILLER` but reads as a `READING` -- that a
  file could be written with nothing recording it.
- Sidebar row text comes from `discovery/row_text.read_lines`, which walks the
  element and breaks lines at block boundaries. Do not go back to `inner_text()`
  here: the outline is minified, and `innerText` is specified to fall back to
  `textContent` for an element that is not being rendered, so a collapsed
  module's rows return their title and subtext with no separator between them.
  A regex that strips the subtext off the title is not a substitute -- it leaves
  the subtext empty, which is the half the classifier actually needs.
- A survey item is skipped: `detection.SURVEY` is decided from a full-phrase
  title marker ahead of the URL segment (the segment describes the container a
  survey is served in), and `handlers.SurveyHandler` advances without dwelling
  or archiving.
- The media guard is released by `BrowserSession.__exit__`
  (`interaction.release_media_guard`), which restores
  `HTMLMediaElement.prototype.play` and drops the listeners. Media is left
  muted as it stands; the user can raise the volume and it holds. The wrapper
  reports the native `name` and `toString` while installed.
- The demographics survey is declined, never submitted: `MODAL_RULES` lists
  only declining controls for it. A modal whose heading is on screen but whose
  listed controls are absent is warned about once per heading
  (`modals._REPORTED_STALLS`) and left for the user. The Honor Code rule still
  clicks `Continue`, which is the same click the user makes to open the item.
- `python -m phantadex` provides the same interface. The root Watch and Archive
  scripts remain compatibility wrappers.
- Runtime output names Phantadex Watch, Archive, Dex, and Link.
- Archive rows are compact, course-map generation has a TTY-only spinner, and
  Dex uses the same Phantadex status theme instead of fixed-width box art.
- Transcript fallback recognizes the current Files panel, prefers its TXT
  asset, and does not toggle an already-open panel closed.
- Course archive mutations are serialized across processes and the XML ledger
  is replaced atomically on macOS/Linux and Windows.
- Packaged `phantadex/config.yaml` holds immutable selector defaults; selectors
  a discovery run verifies are written to a user state file
  (`PHANTADEX_STATE_DIR`, else the platform application-state directory) and
  layered over the defaults at read time. The write is atomic. Nothing is
  written into the installed package, which may be read-only in a wheel install
  and is replaced on upgrade.
- Persistent item-handler failures advance after three attempts instead of
  retrying forever, and the item is recorded in the ledger with
  `status="failed"` so a giving-up skip is not indistinguishable from success. Every automated UI click now uses the shared pointer-move,
  hover, randomized-reaction, and click path.
- The Chrome launcher supports macOS and Windows, dependencies have compatible
  bounds, and CI covers Python 3.11/3.13 on Linux, Windows and macOS.
- Watch accepts `--reading-minutes MIN[-MAX]` for a fixed or randomized fallback
  when an item exposes no reading duration; the active range is shown at startup.
- Dex marks every mapped row as completed (`✓`), incomplete (`○`), or unknown
  (`?`) from that row's live sidebar state. Completion checks no longer
  accept a success marker belonging to another lesson.
- The default root is `phantadex_archive/`; each ledger uses the course slug as
  `<slug>.pdex.xml`. Legacy default data migrates without overwriting conflicts.
- Watch retreats from a locked interstitial to the required previous mapped
  item. It pauses on the required manual assessment instead of advancing back
  into the lock. A missing reading body never starts a false scroll session.
- Reading scrolling is confined to the nearest overflowing content ancestor.
  Short pages emit no wheel input, the bottom does not trigger reverse movement,
  and an item URL change ends the old reading session before any completion or
  navigation action runs. Transient course-name placeholders are ignored.
- CDP shutdown disconnects Phantadex before Playwright stops, leaving the host
  Chrome open; interruption output is centralized and clears progress first.
- Item identity is anchored on the course slug plus a recognised item segment,
  so `/learn/<course>/home/week/1` carries no item id and two courses' items
  with the same opaque id no longer compare equal.
- The URL segment decides an item's type before any on-page player check, and
  the course scanner derives its segment table from the classifier's, so a
  reading or assignment page containing a `<video>` is no longer read as a
  lecture and the two tables cannot drift apart.
- Element lookups scan candidates in the order `schema.selectors_for` declares
  rather than through one comma-joined locator, which was matched in DOM order
  and let a generic fallback outrank a verified selector.
- A cleared graded quiz is advanced past after two consecutive absent readings;
  an unreadable ledger is renamed to `<name>.corrupt-<timestamp>` instead of
  being rebuilt over; skipped discussions are archived; and an item that fails
  repeatedly and cannot be advanced past keeps its failure count.
- Selector discovery is seeded from the learned state alone, so a probe that
  merely restates a packaged default is not frozen into the state file.
- Every in-video interrupt rule (Reflect, Poll, Question) is confined to
  `modals.IN_VIDEO_SCOPE` -- the dialog roles plus the player's `rc-VideoQuiz`
  container -- and no longer falls back to the page. Only the two full-page
  interstitials, Honor Code and the demographics survey, still use the page
  fallback, and their click stays inside the heading's own container.
- `interaction.click` passes its pixel jitter as a `position` to both hover and
  click. Passing it only to the cursor move left every click landing on the
  element's exact centre.
- `interaction.click` no longer forces clicks by default. Forcing skips
  Playwright's actionability checks, so a click swallowed by an overlay was
  reported as a success; the modal path still forces deliberately. The
  actionability wait is bounded by `interaction.CLICK_TIMEOUT_MS` (5s) so one
  stuck control cannot stall a run on Playwright's 30-second default.
- The seek script no longer dispatches a `timeupdate`. Assigning `currentTime`
  makes the browser fire seeking, seeked and timeupdate itself; the dispatched
  copy was redundant and was the only script-made event in the package.
- Commits: `7d1012e` (package split) and `3b46a37` (archive, selector, and
  traversal defects). The review fixes above are uncommitted at the time of
  writing; nothing has been pushed.

- Coursera's AI roleplay practice (`/coach/`, row subtext "Dialogue") has its
  own `DIALOGUE` type and `DialogueHandler`. It is deliberately *not* folded in
  with the ungraded widgets: a widget only needs its box ticked, but Coursera
  marks a dialogue complete only after `Start Dialogue`, `End Dialogue`, and
  the `Yes, end the Dialogue` confirmation in the prompt that follows. The
  `Try again` control appearing in place of the transcript is the page's own
  signal that the sequence landed, and is what `dialogue.finished` matches.
  Each control renders in response to the previous click rather than with the
  page, so each is polled for. A dialogue an earlier run left open has no
  `Start` control any more, so the handler checks for `End` before polling for
  `Start` -- without that check a resumed dialogue burns the whole retry budget
  waiting for a control that will never appear.
- The ledger stores at most `course_manager.MAX_LEDGER_CONTENT_CHARS` (40,000)
  characters per item. The `.txt` file beside it is the archive and is always
  written in full -- do not move the cap into `page_ops.extract_reading`, which
  would truncate the archive itself. The cause is that a supplement can embed a
  PDF viewer whose rendered text flows straight into `content.reading_body`, so
  a book-length PDF was already being extracted and stored inline.

## Start here

```bash
pdex
pdex skip
pdex watch --help
pdex archive --help
python -m phantadex --help
python run_tests.py
ruff check --select E9,F63,F7,F82,I phantadex/course_manager.py phantadex/page_ops.py phantadex/discovery phantadex/navigation.py phantadex/cli.py phantadex/__main__.py phantadex/watch.py phantadex/archive.py phantadex_watch.py phantadex_archive.py tests/test_course_manager.py tests/test_discovery.py tests/test_navigation.py tests/test_page_ops.py tests/test_branding.py tests/test_cli.py
```

`tests/test_branding.py` covers both compatibility launchers and the five
product names. `tests/test_cli.py` covers default Dex, explicit Dex, Skip ranges,
  Watch defaults, Archive forwarding, package module execution, and Dex status
  wiring.
`tests/test_course_manager.py` covers atomic replacement, lock use, contention,
and the Windows lock branch. `tests/test_discovery.py` directly covers browser
course mapping and selector-discovery orchestration.

## Verification boundary

- Offline tests and CLI help can run without a browser or network.
- Live CDP smoke testing attached through Phantadex Link, classified the active
  page as `READING`, resolved its Lesson 6 metadata, and regenerated a 35-item
  Dex map: 7 videos, 17 readings, 6 labs, 2 discussions, and 3 assignments.
- Watch archived a completed lecture, sought within its configured range,
  reached both the default 100%/ended target and a custom 99% target, then
  advanced. Bulk Archive extracted 23 of 24 mapped video/reading items into an
  isolated temporary root; the remaining Congratulations reading is locked by
  the platform until the preceding graded assessment is completed.
- A live lecture exposed Files resources including a TXT transcript; the forced
  fallback downloaded and read 2,843 characters, then restored the original
  reading page.
- The live Next steps reading exposed Mark as completed. The shared completion
  action removed the button and changed its sidebar row from Not submitted to
  Completed. Dex then reported 11 completed and 24 incomplete rows.
- The dialogue sequence was run against a live roleplay item through
  `DialogueHandler.handle`: it classified as `DIALOGUE`, completed in about ten
  seconds, and the sidebar row moved from "Not submitted" to "Completed".
- All six mapped `ungradedWidget` routes retained their target URL during a
  direct-navigation probe. Their empty panes already take the existing plugin
  skip path.
- The checkout is installed editable in the active Python environment, making
  `phantadex` and `pdex` available as shell commands.
- Live Dex rendered all 35 rows with one `✓`/`○` state marker. Live Archive
  rendered 24 compact rows without inserted blank lines and reused the migrated
  `example-course.pdex.xml` ledger. The remaining legacy archive directories and
  generic ledgers were moved to `phantadex_archive/` and slug `.pdex.xml` names;
  no destination conflict occurred.
- Live Watch retreated from Congratulations to End of course assessment and
  paused there without scrolling or cycling. Ctrl+C produced one line, no
  unhandled future, and Chrome 151 remained reachable on the debug port.
- Windows was not available locally. The Windows `msvcrt` lock branch has a
  direct unit test; Python 3.11 syntax, shell-independent launcher invocation,
  and platform-neutral paths cover the remaining changed surface.
- Latest gate: 349 offline tests pass; `ruff check phantadex tests` is clean
- Packaging: the launcher moved from `scripts/start_chrome_debug.py` into
  `phantadex/chrome.py` (`pdex chrome`) because `scripts/` is not in the wheel,
  so an index install had no way to start the debug browser; the script path
  stays as a wrapper. `.github/workflows/release.yml` publishes on a `v*` tag
  through PyPI trusted publishing, which has to be configured on PyPI once
  before the first release.
- Cross-platform: `logs.console_stream()` widens stdout to UTF-8 before the
  handler is attached, which is what keeps a redirected run from dying on the
  first glyph under a non-UTF-8 locale; `storage.sanitize_filename` renames the
  Windows device names on every platform, not only under `os.name == "nt"`.
  under the `pyproject.toml` configuration, and the wheel builds. The wheel
  contains `phantadex/config.yaml`, both console entry points, and no legacy
  package namespace.
- CI runs a lint job plus a test matrix of Python 3.11/3.13 on Linux and
  Windows, and 3.13 on macOS. The floor is 3.11 because 3.10 reaches end of life
  in October 2026; raising it further means editing `requires-python`, the ruff
  `target-version` and this matrix together.

## Verification notes

- The first Watch test fake omitted `BrowserSession.reclaim`, causing a test
  harness error. The fake was corrected and the full red phase was rerun before
  production changes.
- Unscoped Ruff reports pre-existing findings across the legacy browser code;
  the scoped command above covers the changed surface.
- A first live Files fallback probe ran while the active tab was a reading, so
  no Files control existed. The follow-up selected a mapped lecture, verified
  the fallback, and restored the reading URL.
- An isolated alias-install probe was rejected before execution because its
  temporary-directory cleanup command was blocked. The editable install was
  verified directly in the active Python environment instead.
- A live Watch run appeared to stall in the assignment handler after the tab was
  navigated from a second Playwright client over the same CDP endpoint. A
  targeted probe showed the cause: a Playwright client's `page.url` is not
  updated when a *different* CDP client navigates the tab, so the running
  watcher never saw the move. Position the tab first, let the helper script
  exit, then start Watch -- do not steer a live run from a second connection.
- The last live run covered eleven items on one course: transcripts archived and
  the 25% video target honoured, two sidebar-complete items skipped, a
  discussion archived, and the run paused on the required peer assignment
  without cycling.
- A read-only capture of an open in-video poll recorded its markup: the heading
  sits inside `role="dialog" aria-modal="true"` (`cds-Dialog-dialog`) nested in
  the player's `rc-VideoQuiz` container, with `Submit` and `Skip` buttons. That
  capture is what the in-video scope is based on. Dismissal was then verified
  against the same open poll: one button click, playback resumed from 92s.
  No live sample of a `Reflect` interrupt has been captured yet; the rule is
  scoped on the same container, which covers it whether or not Coursera wraps
  that variant in a dialog.
- Turning off forced clicking was verified live before being kept: a full Watch
  run at DEBUG covered twelve items -- transcript panel clicks, mute and play
  controls, sidebar skips, and item advances -- and logged no `Click failed`
  entry and no warning. Seeks landed at 97.9% and 98.2% without the removed
  synthetic event.
- Verbosity is now `-v` (DEBUG), `-q` (WARNING) and `--log-level`, the last of
  which wins when set. The DEBUG stream covers every action that reaches the
  page: clicks name the control they hit and their coordinates, scrolls report
  the distance travelled, the video path records mute, play, resume and seek,
  and the archive and home navigations log their targets. The discovery
  package no longer prints; its output goes through the logger, so the level
  applies to it as well. Verified live: a `pdex watch -v` run logged six clicks by label, two
  seeks and both play calls, with no click failure and no warning.
- `discovery.py` is now the package `phantadex/discovery/`, split by
  responsibility: `rules` (classification from text, URLs and titles),
  `context` (identity of the open item), `course_map` (the outline read from
  the sidebar), `probing` (schema selectors verified live and persisted) and
  `observation` (the loop, and the only module that navigates). `__init__.py`
  re-exports the public names; `tests/test_discovery.py` asserts the
  dependencies stay one-way.
- `ELEMENTS_SCHEMA` moved to `phantadex/element_schema.py`, a leaf module with
  no imports. `phantadex/schema.py` read it out of the discovery tool through a
  deferred import, which put the whole tool behind every runtime lookup.
- Session state is an explicit `ObservationState`. It replaces the module-level
  `GLOBAL_REQUIRED_TYPES` and the `_printed` / `_course_map` attributes that
  were hung off `get_sidebar_targets`, and `discover_selectors` no longer
  navigates -- the loop decides where to go next.
- The platform host lives in `phantadex/urls.py` (`PLATFORM_HOST`,
  `PLATFORM_ORIGIN`); `session.py` held a second copy and the smart hop built
  its target by string concatenation instead of `urls.absolute_url`.
- `NODE_OPTIONS=--no-deprecation` is set by `session.quiet_node_driver()` at
  attach time rather than as an import-time side effect of the discovery module.
- Selector discovery is reachable as `pdex discover`, which takes the shared
  flags (`--cdp-url`, `-v`, `-q`, `--log-level`). It previously had no CLI
  entry point at all: `python -m phantadex.discovery` was the only way in, and
  it accepted no arguments. That module path still works.
