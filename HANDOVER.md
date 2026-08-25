# Handover

Current state, where to start, and the decisions that will bite whoever
"simplifies" them. Why a thing changed is in `CHANGELOG.md`; this file records
only what still constrains the code.

## Orientation

- Branch `refactor/package-boundaries-and-schema`, a sequence of themed commits.
  No pull request has been opened against `main` yet.
- The package is `phantadex/`. Console entry points `pdex` and `phantadex` both
  dispatch Dex, Skip, Watch, Archive, Discover, Chrome and Stop, and both
  default to Dex. `python -m phantadex` is the same interface. The root
  `phantadex_watch.py` and `phantadex_archive.py` are compatibility wrappers.
- Every course command takes an optional URL in the same position
  (`config.add_course_url_arg`), landing in `Settings.course_url` and passed to
  `BrowserSession.find_course_page`. A bare course link as `argv[1]` runs `dex`;
  `urls.is_platform_url` keeps a mistyped command from being navigated to
  instead of reported.
- The default archive root is `phantadex_archive/`, one ledger per course named
  `<course-slug>.pdex.xml`. Legacy data migrates without overwriting conflicts.
  Every ledger opens with a generator comment in its prolog naming the tool, its
  version and the repository. `ElementTree` drops comments when it parses, so
  the banner is rebuilt by `_write_tree` on every write rather than preserved --
  which is also what keeps a reopened ledger from stacking one banner per save.
- Packaging: the Chrome launcher lives in `phantadex/chrome.py` (`pdex chrome`),
  not `scripts/`, which is not in the wheel -- an index install otherwise had no
  way to start the debug browser. `scripts/start_chrome_debug.py` stays as a
  wrapper. `.github/workflows/release.yml` publishes on a `v*` tag through PyPI
  trusted publishing; the publisher is registered, and `2.3.0` is the current
  release on PyPI. A
  release is a version bump in `phantadex/__init__.py` and a
  matching `vX.Y.Z` tag -- `pyproject.toml` reads the version from that
  attribute, so there is one place to change. The workflow refuses a tag that
  disagrees with the packaged version, and PyPI refuses a version already used.

## Traps

Each of these looks like an accident and is not.

- **Tab choice is creation order.** Without a URL the first platform tab Chrome
  lists is used, and that is tab *creation* order, not recency, so it is not a
  reliable "current" tab. With more than one open the pick is warned about
  rather than made silently. A tab already inside a course outranks one merely
  on the platform: the home page, the catalogue and the enrolment list share the
  host and carry no sidebar to read.
- **Sidebar classification reads two separate haystacks**, the row subtext and
  the `aria-label`. Do not concatenate them again: the label is
  `<type>, <title>, <status>, <duration>`, so a title containing "peer", "quiz"
  or "assignment" would be read as the row's type. Only the label's first
  comma-separated field is consulted, past any "selected link" prefix.
- **Sidebar row text comes from `discovery/row_text.read_lines`**, which walks
  the element and breaks lines at block boundaries. Do not go back to
  `inner_text()`: the outline is minified, and `innerText` is specified to fall
  back to `textContent` for an element that is not being rendered, so a
  collapsed module's rows return title and subtext with no separator. A regex
  that strips the subtext off the title is not a substitute -- it leaves the
  subtext empty, which is the half the classifier needs.
- **`pdex -h` is `phantadex/overview.py`, not argparse**, and is derived so it
  cannot drift: `cli.COMMANDS` *is* `overview.COMMAND_SUMMARIES`'s key order,
  and the per-item lines come from each handler's `summary`. Adding a command or
  handler without its one-liner fails `tests/test_cli.py`. Do not hand-write
  item descriptions. The page must not print the expanded state directory; it
  names `PHANTADEX_STATE_DIR`, and a test asserts the home path never appears.
  It is ASCII only -- it prints before `logs.console_stream()` widens stdout to
  UTF-8, so a box-drawing rule would raise `UnicodeEncodeError` on a redirected
  Windows run. Section rules are markers sized once the page is built; no line
  passes `overview.MAX_PAGE_WIDTH`. `phantadex.REPOSITORY_URL` is checked
  against the `Homepage` in `pyproject.toml`.
- **`os.kill(pid, 0)` is not a liveness probe on Windows.** Signal 0 is
  `CTRL_C_EVENT` and interrupts the caller's own console group. The Win32 handle
  is read instead, with `restype`/`argtypes` declared -- ctypes otherwise
  truncates a pointer-wide HANDLE to a signed 32-bit integer and reports an
  exited process as alive.
- **`processes.is_phantadex_command` consults a file named on the line only when
  the executable is a Python interpreter.** Without that gate
  `vim phantadex_watch.py` and `git commit phantadex_watch.py` were killed
  alongside the runs. Real `ps` output for an installed run is
  `<python> <bin>/pdex watch ...`, so both the interpreter branch and the
  bare-entry-point branch have to stay. Runs are found in the process table, not
  a registry file, because a registry survives a hard kill and then lies.
- **A graded quiz's markup is absent before the attempt as well as after one.**
  `--pause-on-graded` counts absence only once the quiz has been seen; unseen,
  the wait runs until the item is left, which is what `AssignmentHandler` has
  always done. Do not bound it.
- **`_probe_element` returns the first selector that is *visible*, not the best
  one.** A probe result ranking below the effective selector in the element's
  own candidate list is discarded (`probing._ranks_below`). Without that guard a
  closed transcript panel promoted the "Transcript" toggle over `.rc-Transcript`
  and every later scrape read the player's control text.
- **A selector state entry may be a string or a list in preference order**, of
  which only the head is used. Compare through `probing._split_selectors` --
  comparing against the list object is never equal, which reported MODIFIED on
  every pass -- and write through `_with_alternatives`, which keeps the
  fallbacks behind the head.
- **`discover_selectors` is seeded from the learned state alone but returns the
  merged view.** The seed keeps a probe that merely restates a packaged default
  out of the state file; handing that seed back made every packaged default read
  as absent on the next item, so they were relabelled NEW and written out, and
  the hop lost `navigation.next_item`.
- **A page type with no `RELEVANT_CATEGORIES` entry** (WRAPUP, SURVEY, FILLER,
  UNKNOWN) scans only the common categories, never the whole schema. Scanning
  everything is how a content selector gets "verified" against an unrelated
  element, and a verified selector outranks the shipped default until the next
  run.
- **The discovery loop jumps to each missing type once**
  (`ObservationState.attempted_types`). A row's mapped type and the type its
  page reports need not agree, so a visited target can stay on the missing list;
  without the guard the loop re-issued the same `goto`, the URL-change check
  never fired again, and the poll spun in silence.
- **One item can hold several adjacent map rows.** A peer assignment is two
  sidebar rows -- the submission and the review of classmates' work -- served
  under one opaque item id, so `urls.same_item` reports them equal. That is
  correct and load-bearing: move detection, resume matching and `is_archived`
  all rest on it. `get_next_url` and `get_previous_url` therefore step over
  *every* row belonging to the current item, not just the matched one. Returning
  index+1 handed back the page already open, `goto` was a no-op, the handler
  advanced again, and a course ended by cycling on its last assignment forever.
- **A log record and an in-place progress line share one terminal line.** The
  bar and the spinner repaint with `\r` and no newline, so `logs.ConsoleHandler`
  blanks the remembered line, emits the record, and repaints -- under
  `logs._transient_lock`, which `logs.progress` also holds. Do not write a bar
  frame straight to stdout, and do not add a handler that is a plain
  `StreamHandler`: either one welds the message onto the bar's last frame and
  freezes it there. `logs.bar_done()` before a warning is no longer needed and
  is not a substitute.
- **A run log is one file per process, not one per call.** The package logger
  survives past any single run, so `logs.start_run_log` installs a
  `logs.RunLogHandler` and closes whichever one is already attached; two live
  handlers means every record is written twice. Tests that reach a real entry
  point write for real, which is why importing `tests/` sets
  `PHANTADEX_STATE_DIR` to a temporary directory -- do not remove it, or a test
  run edits the resume ledger, the learned selectors and the logs of whoever
  ran it.
- **The ledger caps stored text at `course_manager.MAX_LEDGER_CONTENT_CHARS`
  (40,000).** The `.txt` beside it is the archive and is always written in full
  -- do not move the cap into `page_ops.extract_reading`, which would truncate
  the archive itself. A supplement can embed a PDF viewer whose rendered text
  flows straight into `content.reading_body`.
- **A resumed dialogue has no `Start` control.** `DialogueHandler` checks for
  `End` before polling for `Start`; without that check a dialogue an earlier run
  left open burns the whole retry budget waiting for a control that will never
  appear. The `DIALOGUE` type is deliberately not folded into the ungraded
  widgets: a widget needs its box ticked, but Coursera marks a dialogue complete
  only after `Start Dialogue`, `End Dialogue` and the `Yes, end the Dialogue`
  confirmation. Each control renders in response to the previous click, so each
  is polled for; `Try again` in place of the transcript is the page's own signal
  that the sequence landed.
- **Do not steer a live run from a second CDP connection.** A Playwright
  client's `page.url` is not updated when a *different* client navigates the
  tab, so the running watcher never sees the move and appears to stall. Position
  the tab first, let the helper exit, then start Watch.
- **The seek script must not dispatch a `timeupdate`.** Assigning `currentTime`
  makes the browser fire seeking, seeked and timeupdate itself; a dispatched
  copy would be the only script-made event in the package. The direct script
  is also the fallback, not the primary route: the seek first clicks the
  player's progress bar at the target fraction and confirms playback moved
  before trusting the click.
- **A dialogue is held open, not started and closed.** `DialogueHandler` dwells
  a drawn 45-120 seconds with cursor drift before ending the session; closing
  seconds after opening reports a zero-turn session no person had. Nothing is
  ever composed or sent -- that line is the tool's, not the platform's.

## Design in force

- **Selector layering.** Packaged `phantadex/config.yaml` holds immutable
  defaults; what a discovery run verifies is written to a user state file
  (`PHANTADEX_STATE_DIR`, else the platform application-state directory) and
  layered over the defaults at read time. The write is atomic. Nothing is
  written into the installed package, which may be read-only in a wheel and is
  replaced on upgrade. Lookups scan candidates in the order
  `schema.selectors_for` declares, not through one comma-joined locator, which
  matched in DOM order and let a generic fallback outrank a verified selector.
  A discovery pass re-ranks what the package ships; it cannot invent a selector,
  so an element nothing matched is *named* rather than folded into "selectors
  are stable". At runtime the same condition calls `schema.report_stale`, which
  says once per run that the markup has changed and names `pdex discover`.
  Probing a video page opens the transcript panel first
  (`page_ops.open_transcript_panel`): collapsed, it leaves its toggle on screen
  and its body off it, and the probe recorded the button as the transcript.
  `rules.CORE_TYPES` is what a pass must cover before it stops, narrowed against
  the course's own outline -- naming a type the course lacks costs nothing, and
  omitting one it has is why `dialogue` had never been probed anywhere.
- **Type detection.** The URL segment decides an item's type before any on-page
  player check, and the course scanner derives its segment table from the
  classifier's, so the two cannot drift and a reading containing a `<video>` is
  not read as a lecture. Item identity is the course slug plus a recognised item
  segment, so `/learn/<course>/home/week/1` carries no item id and two courses'
  items with the same opaque id do not compare equal. The ungraded rows carry
  two map labels -- `UNGRADED_PLUGIN` for `/ungradedWidget/` and
  `/ungradedLti/`, `LAB` for `/ungradedLab/` -- collapsed onto the single plugin
  handler by `detection._LABEL_TO_TYPE`; the distinction is in the map only.
  `detection.SURVEY` is decided from a full-phrase title marker ahead of the URL
  segment, because the segment describes the container a survey is served in.
- **Pacing.** Observable delays come from `phantadex/jitter.py`, a log-normal
  over the requested span with a 4% chance of a longer pause. Its mean matches
  the uniform draw it replaced, so run pace is unchanged; percentages and pixel
  offsets stay uniform on purpose. Callers sleep on the returned value
  themselves, so tests that patch a caller's `time.sleep` still work. Every
  pacing site draws from a range -- item-transition settles, retry waits,
  resume/reload settles, transcript toggles, modal dismissals, the video watch
  tick -- because a fixed value repeated across hundreds of items is a cadence,
  not a pause. The config-driven `idle_poll_seconds` stays scalar by choice.
  Watch plays videos through by default (`--skip` restores the seek into
  `97.5-98.5%`), with the completion target sampled once per video from
  `--video-threshold` (default `98-100`); `Settings` normalises a bare number
  into a zero-width range. `--reading-minutes MIN[-MAX]` is the fallback dwell
  when an item exposes no duration.
- **Clicking.** Every automated click uses the shared approach-move,
  randomized-reaction, click path, and passes its pixel jitter as a `position`
  to the click -- passing it only to the move left every click on the element's
  exact centre. There is no explicit hover: Playwright's own click re-resolves
  the element and moves to `position` itself, so an earlier hover only produced
  several identical mouse-move events at one coordinate before the press. A
  caller may pin an element-relative `position` (the video seek does) and the
  jitter applies around that instead of the centre. Clicks are not forced by
  default: forcing skips Playwright's actionability checks, so a click
  swallowed by an overlay reported success. The modal path tries unforced
  first under a short timeout and forces only on failure. The actionability
  wait is bounded by `interaction.CLICK_TIMEOUT_MS` (5s) rather than
  Playwright's 30.
- **Media.** A guard installed on the browser context
  (`interaction.install_media_guard`, armed on open tabs by `BrowserSession`)
  runs before the page's own scripts and mutes at `loadstart`; `silence_media`
  remains the per-tick sweep. It leaves nothing findable by name: its state
  sits behind a symbol registered under a per-process random token, and its
  listeners are removed through an AbortController. Do not reattach a named
  handle to `window` and do not wrap `HTMLMediaElement.prototype.play` --
  both were removed as artifacts a page script could find (an own-property
  `toString` and a source readable through `Function.prototype.toString.call`),
  and the capture-phase mutes cover what the wrap did. The host check is exact
  (`coursera.org` or a subdomain); a bare suffix match also armed inside
  lookalike hosts such as `notcoursera.org`. `__exit__` releases it; media is
  left muted as it stands.
- **Reading how long an item is.** One reader (`video.state`) serves both
  element kinds; `narration_seconds` points it at `audio` and
  `wait_for_duration` at `video`. It picks the *longest* element that reports a
  finite duration, because a reading was measured carrying two narration tracks
  with only one of them loaded, and the unloaded one reports `NaN`. A page with
  no such element is answered without waiting, so the plain readings do not pay
  the poll the narrated ones need. `interaction` must not import `video` --
  `video` imports `interaction` -- so the handler does the read and hands the
  seconds down to `reading_session`. `timing.dwell_minutes` is the pure rule:
  narration wins outright and is left before it ends, otherwise the word-count
  estimate plus a settling constant, capped by the listed duration.
- **Who moves the tab.** Coursera advances to the next item itself a few
  seconds after one completes, and the user may click any chapter at any
  moment. Both routes out of a handler check the URL first and defer when it
  has moved: `navigation.advance` returns `ALREADY_MOVED`, and the work-based
  route in `BaseHandler.advance` skips `ctx.next_work` rather than routing from
  a `start_url` that is now stale. Whoever moved the tab is right about where
  it should be.
- **Work the run cannot finish.** Not every item ends up marked complete: a
  discussion prompt is read and archived but never posted to, so the sidebar
  lists it as work for the whole run. `Runner.attempted_items` records an item
  the run worked and left still listed, and `unfinished_items(exclude=...)`
  drops it from the routing -- without it the run walked to that item, off it
  onto a finished one, and straight back. It stays in the *report* of what is
  left; only the routing forgets it.
- **Modals.** Every in-video interrupt rule (Reflect, Poll, Question) is
  confined to `modals.IN_VIDEO_SCOPE` -- the dialog roles plus the player's
  `rc-VideoQuiz` container -- with no page fallback. Only Honor Code and the
  demographics survey, both full-page interstitials, still use the page
  fallback, and their click stays inside the heading's own container. The
  demographics survey is declined, never submitted: `MODAL_RULES` lists only
  declining controls for it. A modal whose heading is on screen but whose listed
  controls are absent is warned about once per heading (`modals._REPORTED_STALLS`)
  and left for the user.
- **Reading.** Scrolling is confined to the nearest overflowing content
  ancestor. Short pages emit no wheel input, the bottom does not trigger reverse
  movement, and an item URL change ends the old reading session before any
  completion or navigation action runs. A missing reading body never starts a
  false scroll session.
- **Archival.** Course archive mutations are serialized across processes and the
  ledger is replaced atomically on macOS, Linux and Windows. An archived item
  missing from the ledger is adopted into it (`CourseManager._adopt_item`)
  rather than logged as unmatched, because map row types and live classification
  disagree often enough -- a survey-titled supplement maps to `FILLER` but reads
  as a `READING` -- that a file could be written with nothing recording it. An
  unreadable ledger is renamed `<name>.corrupt-<timestamp>` instead of being
  rebuilt over. Transcript fallback recognizes the current Files panel, prefers
  its TXT asset, does not toggle an already-open panel closed, and deletes the
  downloaded file after reading it -- Playwright clears downloads when the
  context closes, and this context is the user's own Chrome, which does not.
- **Run bounds.** Two, in `phantadex/limits.py`. `Budget` is the deliberate one
  (`--modules N`, `--items N`, mutually exclusive), counted from where the run
  starts rather than indexed from the course's first item, because
  `resume_at_incomplete` is on by default and "the first two modules" would
  usually name modules already finished. It is consulted on arrival and before
  the item is handled, so a refusal costs nothing. `StallGuard` is the
  involuntary one: forty passes without reaching an item the run has not been on
  ends it. Do not raise the limit to paper over a cycle -- the guard exists
  because each individual way of spinning was recoverable and none was counted.
  `navigation._ledger_fallback` also refuses a destination equal to the item
  already open, which can only mean the map repeats itself.
- **Nothing left to do is a stop condition.** `Runner._resume_or_finish` asks
  `CourseManager.unfinished_items` for the rows the sidebar marks unfinished,
  minus `progress.SKIPPED_LABELS` -- graded work and the surveys the map labels
  `FILLER`, none of which the run answers or archives. The first of what remains
  is where the run resumes; when nothing remains it names what is left and
  stops, rather than walking the rest of the course with a skip prompt on every
  finished item. `--pause-on-graded` returns the graded labels to the work set
  because then the run does wait on them. Only rows explicitly marked unfinished
  count: absent is not unfinished. It is tied to `resume_at_incomplete` -- with
  `--no-resume` the run walks forward from the item it was given, as before.
- **Who a course is waiting on lives in one module.** `phantadex/progress.py`
  owns `SKIPPED_LABELS`, `GRADED_LABELS` and `SELF_SUBMITTED_LABELS`, plus the
  `split`/`summary` pair every count is printed through -- the course tree, the
  per-module header and the run's closing report. Put the rule there rather than
  beside whichever surface needs it next: it was previously half in the runner
  and half in the ledger, which is why neither of the two places printing a
  count could say which side of it a row fell on. The split is by item label and
  the ledger, never by settings: `--pause-on-graded` changes when a run reaches
  a graded item, not who answers it. A row the sidebar cannot be read for is
  counted as unreadable and declared, so the parts always sum to the total.
  `summary` names Phantadex's share even when it is zero and omits yours when it
  is -- deliberately asymmetric: the run's own share is what a reader is
  checking for, and omission would make "done" and "not started" print the same
  line. It imports only `detection` and `urls`, which is what keeps `discovery`
  able to import it.
- **A run moves between work, not between rows.** `Runner._advance_to_work` is
  handed to the handlers as `handlers.Context.next_work`, and
  `BaseHandler.advance` asks it before touching the Next button:
  `CourseManager.next_actionable` reads the sidebar at the item boundary and
  returns the next unfinished row the run would act on, preferring the ones
  ahead of the current item to the ones behind it. Rows addressing the item
  being left are never returned -- the sidebar takes a moment to record a
  completion, so the item just finished is routinely still listed as work. Two
  fallbacks matter: a scan that cannot answer returns `None` and the platform's
  Next button moves the run, and a sidebar whose only remaining work *is* the
  item just left also falls back rather than declaring the course over. Do not
  make a handler import the runner; the callable on the context is the seam.
- **The map and the open page must agree about a survey.**
  `discovery.apply_filler_override` reads `detection.SURVEY_TITLES`, the same
  list `detection.classify` uses. When the two were separate, a row the
  classifier called a survey was mapped `UNGRADED_PLUGIN`, so a run picked it as
  the first thing left to do and only recognised it on arrival. Add a survey
  phrase in `detection.SURVEY_TITLES` alone, and keep the phrases whole: a bare
  "survey" matches course content about surveys.
- **An archived item is not scraped again.** `BaseHandler.already_archived`
  gates extraction in the video, reading and discussion handlers, and
  `CourseManager.is_archived` is the predicate. Archived is not the same as
  complete, so the item is still worked -- a video the ledger holds is still
  watched. `pdex archive --force` is the way to deliberately scrape again.
- **Item types have one spelling and one colour.** `logs.TYPE_WORDS` and
  `logs.TYPE_COLOURS`, read through `logs.type_name` and `logs.type_tag`, by the
  course tree, the handlers and the archiver alike. Do not put a coloured tag in
  the right-aligned argument of `logs.item`: an escape sequence has length but
  occupies no columns, and the padding is measured from it.
- **Off-course tabs.** `urls.is_course_url` is the test. A run that starts on a
  platform page outside a course says so and stops; a run that drifts onto one
  steers back to the last item it was on and gives up after
  `Runner.OFF_COURSE_LIMIT` attempts. Without this the page classified as
  `UNKNOWN` and the loop span on it.
- **Run logs.** Every Watch run writes one timestamped DEBUG file to
  `<state directory>/logs/` (`--no-run-log` opts out, twenty kept). The package
  logger sits at DEBUG and the *console handler* carries `--log-level`, so a
  quiet console still leaves a full record. Do not set the level on the logger.
- **Failure handling.** A persistently failing handler advances after three
  attempts instead of retrying forever, and the item is recorded with
  `status="failed"` so giving up is not indistinguishable from success; an item
  that cannot be advanced past keeps its failure count. `runner._warn_unmapped`
  says once per failure that a run has no ledger -- nothing is archived and no
  navigation fallback works in that state, which used to look identical to a
  course with nothing to save. One unreadable item during discovery is reported
  and skipped rather than ending the pass. Watch retreats from a locked
  interstitial to the required previous mapped item and pauses on the required
  manual assessment instead of advancing back into the lock -- unless the map
  places the item last, because a course's final screen renders no Next control
  either and retreating from it sent the run in circles until the stall guard
  ended it.
- **Shutdown.** CDP shutdown disconnects Phantadex before Playwright stops, so
  the host Chrome stays open. Interruption output is centralized and clears the
  progress line first.
- **Output.** `-v` is DEBUG, `-q` is WARNING, `--log-level` wins when set. The
  DEBUG stream covers every action that reaches the page: clicks name the
  control and coordinates, scrolls report distance, the video path records mute,
  play, resume and seek. The discovery package logs rather than prints, so the
  level applies to it too. Dex marks every mapped row completed (`✓`),
  incomplete (`○`) or unknown (`?`) from that row's live sidebar state.
- **Cross-platform.** `logs.console_stream()` widens stdout to UTF-8 before the
  handler is attached, which is what keeps a redirected run from dying on the
  first glyph under a non-UTF-8 locale. `storage.sanitize_filename` renames the
  Windows device names on every platform, not only under `os.name == "nt"`.
  `config.program_name(subcommand)` sets each parser's `prog=`; left to argparse
  a subcommand's usage line read as the bare entry point and could not be copied
  and run.

## Start here

```bash
pdex
pdex -h
pdex watch --help
python -m phantadex --help
python run_tests.py
ruff check . && ruff format --check .
```

## Verification boundary

- Offline tests, `ruff` and CLI help run without a browser or network. Current
  gate: 795 tests pass in about a second, `ruff check .` and
  `ruff format --check .` both clean.
- Live CDP work is verified against a real signed-in Chrome: course mapping and
  Dex row states, Watch archiving transcripts and pacing to its completion
  target, retreat from a locked interstitial, the Files-panel transcript
  fallback, dialogue completion, bulk Archive over a mapped course, discovery
  terminating on its own, and `pdex stop` finding and ending a real run while
  leaving Chrome up.
- The timeline seek is verified live end to end, not only in the fakes. Against a
  real lecture player: the seekable bar is a span 1223px wide inside a 1239px
  player, a click at fraction 0.7 landed at 107.08s against a 107.18s
  destination, and `seek_via_ui` returned true without reaching the direct
  fallback. The same pass confirmed the selectors that carry the slider role
  resolve to a 10px drag handle, which is what the schema correction is based on.
- The window the watch loop can meet a player in is real: polled at 50ms after a
  fresh navigation, the element exists for one tick reporting `duration` NaN and
  `readyState` 0 before metadata arrives. It is narrow, and the ordinary path
  does not reach it because muting and seeking come first -- but a player that
  remounts mid-watch puts the loop back there, which is the case the completion
  check is written to survive rather than raise through.
- No live sample of a `Reflect` in-video interrupt has been captured. The rule
  is scoped on the same container as the Poll capture it was built from, which
  covers it whether or not Coursera wraps that variant in a dialog.
- Windows is not available locally. The `msvcrt` lock branch and the Win32
  liveness probe have unit tests; platform-neutral paths, shell-independent
  launcher invocation and the CI matrix cover the rest.
- CI runs a lint job plus Python 3.11/3.13 on Linux and Windows and 3.13 on
  macOS. The floor is 3.11 because 3.10 reaches end of life in October 2026;
  raising it means editing `requires-python`, the ruff `target-version` and the
  matrix together.

## Known open items

Deliberately deferred from the interaction-hardening pass of 2026-08-22.
Each was identified and sized; none is an accident.

- **Curve-based mouse movement, bursty wheel modelling, Gaussian click
  offsets, a drifting scroll anchor.** Mouse paths still interpolate linearly
  and the micro-event stream (wheel chunk sizes, inter-chunk gaps, pass deltas)
  is still uniform-drawn. Only pays off against platforms that run
  behavioural-biometrics defences, which this one does not currently deploy;
  the fidget during video playback also still jumps to random absolute points
  through its own raw `mouse.move`.
- **Scroller diagnosis ahead of the `scrollTop` fallback.** The JS fallback
  fires scroll events with no preceding input; it stays because some containers
  ignore synthesised wheel events and without it reading sessions sit still.
  The root-cause fix is verifying the chosen scroller responds to a probe tick.
- **UI-first mute/play everywhere.** Programmatic `play()` and `muted`/`volume`
  writes remain as fallbacks when player controls are absent, and
  `silence_media` still sweeps every reading tick (throttling risks a lazily
  inserted narration player playing aloud).
- **Optional user-authored dialogue turns.** Needs a keyboard primitive the
  package deliberately lacks, and composing content crosses the tool's stated
  line; only worth adding as opt-in with user-supplied text.
- **Ephemeral CDP port instead of fixed 9222.** Changes the launch contract
  (`--cdp-url`, manual launcher equivalents); for now the exposure while the
  debug window lives is documented in `README.md`.
