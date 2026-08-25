# Phantadex

[![PyPI](https://img.shields.io/pypi/v/phantadex?style=flat-square)](https://pypi.org/project/phantadex/)
![Python](https://img.shields.io/badge/python-3.11%2B-green?style=flat-square)
![Protocol](https://img.shields.io/badge/protocol-CDP-orange?style=flat-square)
![Status](https://img.shields.io/badge/status-Research_Prototype-red?style=flat-square)

> [!WARNING]
> **Use at your own risk.** This tool relies on CSS selectors that Coursera can change at any time. Site updates may require selector changes or manual intervention.

## 1. System Overview

Phantadex runs against a Chrome instance it starts for the job: `pdex chrome`
launches Google Chrome with its remote debugging port open under a dedicated
profile, you sign in to the course in that window yourself, and every command
then attaches to it over the DevTools protocol. The tool never handles your
login and never touches your everyday browser profile.

Product vocabulary:

*   **Phantadex** — overall project.
*   **Phantadex Watch** — co-pilot and course monitor.
*   **Phantadex Archive** — bulk content archiver.
*   **Phantadex Dex** — course map and course-slug `.pdex.xml` ledger.
*   **Phantadex Link** — Chrome DevTools Protocol connection layer.

### Core Capabilities
1.  **Session Persistence:** Operates within the user's primary authenticated context.
2.  **Phantadex Dex:** Uses `phantadex/course_manager.py` to build the course
    map, show live completion state, and track archived content in a ledger such
    as `example-course.pdex.xml`.
3.  **Dual-Mode Operation:**
    *   **Phantadex Watch (`phantadex_watch.py`):** Monitors course progress and
        handles navigation and archival during playback.
    *   **Phantadex Archive (`phantadex_archive.py`):** Directly visits mapped
        videos and readings for bulk archival.
4.  **Content Archival:** Extracts transcripts and reading materials, normalises the
    text (see §5), and versions a file when its content has genuinely changed.
5.  **Item Handling:** One handler per content type, dispatched from
    `detection.classify(page)`. Videos play to a completion threshold, readings
    dwell while the pane scrolls, and everything that cannot be completed by
    watching or reading is stepped past. The flags that change this are in §5,
    the per-type constraints in §8, and `pdex -h` prints the current set.
6.  **Pointer Consistency:** Automated controls share one interaction path that
    moves through several cursor steps, hovers, pauses for a randomized reaction
    interval, and then clicks.

## 2. Technical Prerequisites

The system requires an initialized debugging interface on the host browser.

### A. Environment
*   **Python:** 3.11+
*   **Dependencies:** `playwright`, `plyer`, `PyYAML` -- all three are declared in
    `pyproject.toml`, so either installation route pulls them in.
*   **Browser:** Google Chrome. Phantadex starts and attaches to its own debug
    instance under a dedicated profile, so Playwright's bundled browsers are
    not needed and there is no `playwright install` step. Playwright is used
    only as a DevTools Protocol client.

### B. Installation

The package is on PyPI, so both platforms take one command. `pipx` keeps the
tool in its own environment and still puts `pdex` on the PATH:

```bash
pipx install phantadex        # or: python -m pip install --user phantadex
```

Check it landed, and read the command surface:

```bash
pdex --version
pdex -h
```

An upgrade is `pipx upgrade phantadex`, or `pip install --upgrade phantadex`.
Selectors learned by `pdex discover` live outside the package (§5), so an
upgrade or a reinstall does not discard them.

**Or hand the setup to a coding agent.** Paste the block below into Claude
Code, Codex, Cursor, or any agent with a shell, and it has everything it needs
to install the tool and leave a signed-in browser waiting:

```text
Install and set up Phantadex (https://github.com/SNiPERxDD/Phantadex), a CLI
that drives an already-signed-in Chrome through Coursera coursework.

1. Confirm Python 3.11 or newer is on PATH. Report the version you found.
2. Install it with `pipx install phantadex`. If pipx is missing, install pipx
   first (`python -m pip install --user pipx` then `python -m pipx ensurepath`),
   and open a new shell so the PATH change takes effect. Fall back to
   `python -m pip install --user phantadex` only if pipx cannot be installed.
3. Verify with `pdex --version` and `pdex -h`. If `pdex` is not found, the
   scripts directory is not on PATH -- say so and print the directory rather
   than working around it with a full path.
4. Do NOT run `playwright install`. Playwright is used here only as a DevTools
   Protocol client; its bundled browsers are not needed.
5. Run `pdex chrome`. It starts Google Chrome with its debugging port open on a
   dedicated profile. If it reports the port is held by something that is not a
   debug browser, set PHANTADEX_CDP_URL to http://localhost:9223 in my shell
   profile and run `pdex chrome` again. Do not kill the process holding 9222
   without asking me.
6. Stop there and tell me to sign in to Coursera in the Chrome window it
   opened, then open the course I want worked through.

Do not sign in on my behalf, and do not enter any credentials anywhere. After I
confirm I am signed in with a course open, run `pdex` to print the course tree
so we can check the attachment works, and then stop and show me the output.
```

**From a clone (how to develop, and how to run an unreleased change):**

Windows (PowerShell):
```powershell
git clone https://github.com/SNiPERxDD/Phantadex.git
cd Phantadex
python -m venv .venv

# Allows the activation script to run in this window only; a new terminal
# needs it again.
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned -Force
.\.venv\Scripts\Activate.ps1

pip install -e .
```

macOS / Linux:
```bash
git clone https://github.com/SNiPERxDD/Phantadex.git
cd Phantadex
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

`pip install -e .` pulls in `playwright`, `plyer` and `PyYAML`. Playwright's
bundled browsers are not needed: the tool drives the Chrome that `pdex chrome`
starts, not one of its own.

### C. Launch Configuration (Mandatory)

Chrome has to be started once with its debugging port open. The launcher covers
macOS, Windows and Linux:

```bash
pdex chrome
```

It reports the endpoint if a debug Chrome is already running, and otherwise
starts one on a profile kept with the tool's other state, so the courses stay
signed in between runs. Set `PHANTADEX_CHROME` if Chrome lives somewhere
unusual, `PHANTADEX_CHROME_PROFILE` to move the profile, and `--cdp-url` to use
a different port.

Port 9222 is not always free. On Windows it is often held by an OEM helper or
by a WebView2 host, and the launcher refuses to claim a port it cannot confirm
is a debug browser. Move Phantadex off it once, rather than per command:

```bash
# macOS / Linux
export PHANTADEX_CDP_URL=http://localhost:9223

# Windows (PowerShell)
$env:PHANTADEX_CDP_URL = "http://localhost:9223"
```

```powershell
# Name what is holding the port, if you would rather reclaim it
netstat -ano | findstr :9222
```

> [!NOTE]
> While this Chrome is running, its debugging port is open on localhost. Any
> process running as your user can attach to it and act in the signed-in
> session. Close the debug window (or run `pdex stop`) when you are done.

Manual equivalents, if you would rather not use the launcher:

```bash
# macOS
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --user-data-dir="/tmp/chrome_dev"

# Linux
google-chrome --remote-debugging-port=9222 --user-data-dir="/tmp/chrome_dev"
```

```powershell
# Windows
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\chrome_dev"
```

Sign in to the course in that window, then leave it open.

## 3. Execution Protocol

> [!WARNING]
> **Shared Browser Constraint**: Ledger writes are locked and atomic, but
> `pdex watch` and `pdex archive` still control the same Chrome tab. Avoid
> running them simultaneously unless each uses a separate debug browser.

1.  **Authentication:** Log in to the target platform on the debugging instance.
2.  **Initialization:** Navigate to the target module entry point.
3.  **Engagement:**

    **What each command does:** `pdex -h` prints every command, what a run does
    with each kind of course item, where files are written, and what is never
    automated. It is built from the command table and the registered handlers,
    so it cannot fall behind them.
    ```bash
    pdex -h
    # The options for one command
    pdex watch -h
    ```

    **Which tab it works in:** every command that reads a course uses the open
    course tab, so keep one course tab open and no more. With several open, the
    first one Chrome lists is used and the choice is reported rather than made
    silently. Naming a course item settles it and opens the item, as a full
    link or a `/learn/...` path:
    ```bash
    pdex https://www.coursera.org/learn/<course>/lecture/<id>/<slug>
    pdex watch /learn/<course>/home/week/1
    ```
    A run still starts at the first unfinished item; add `--no-resume` to start
    on the item named. `dex`, `skip`, `watch`, `archive` and `discover` all take
    the argument in the same position.

    **Option A: Phantadex Dex (Default)**
    Prints the active course tree without navigating or writing the ledger.
    `✓` means completed, `○` means incomplete, and `?` means the platform did
    not expose a status for that row.
    ```bash
    pdex
    # Equivalent forms
    phantadex dex
    python -m phantadex dex
    ```

    **Option B: Phantadex Skip**
    Seeks the active video once to a random point in `97.5-98.5%`, then exits.
    ```bash
    pdex skip
    pdex skip --video-skip-range "90-95%"
    ```

    **Option C: Phantadex Watch**
    Archives encountered content, watches to the completion target, and advances.
    ```bash
    pdex watch
    ```

    **Option D: Phantadex Archive (Bulk)**
    Directly navigates and scrapes all Video/Reading content defined in the course map.
    ```bash
    pdex archive
    # Optional: Force re-scrape everything
    pdex archive --force
    ```

    **Option E: Phantadex Discover**
    Walks the course through the sidebar, probes every selector on the item it
    lands on, and writes the ones that matched to the verified-selector state
    file (§5). Run it when the site's markup changes. It stops on its own once
    each content type the course contains has been verified, or once no route to
    the remaining ones is left -- naming them when it closes. Each type is
    visited once, and a selector is only recorded if it ranks no lower than the
    one already in force, so a control that happens to be on screen cannot
    displace the container it belongs to.
    ```bash
    pdex discover
    # Same pass, with every probe and hop logged
    pdex discover -v
    ```

    **Option F: Phantadex Stop**
    Ends every Phantadex process on the machine, wherever it was started from.
    Each is asked to exit first and killed only if it does not. The command
    never targets itself or the shell that launched it.
    ```bash
    pdex stop
    # See what is running without stopping it
    pdex stop --list
    ```

`phantadex` and `pdex` are identical installed commands. The root
`phantadex_watch.py` and `phantadex_archive.py` scripts remain compatibility
entry points.

## 4. Project Layout

The traversal logic lives in the `phantadex/` package; the two scripts at the repo
root are thin entry points over it.

```
pyproject.toml        # install metadata + phantadex/pdex console aliases
phantadex_watch.py    # compatibility wrapper for pdex watch
phantadex_archive.py  # compatibility wrapper for pdex archive
phantadex/
  config.yaml         # shipped selector defaults (immutable package data)
  __main__.py        # python -m phantadex
  cli.py             # dex/skip/watch/archive/discover/chrome/stop dispatch
  overview.py        # the page 'pdex -h' prints, derived from cli + handlers
  chrome.py          # pdex chrome: starts the debug browser
  watch.py           # Watch argument parsing
  archive.py         # bulk Archive mode
  config.py          # Settings dataclass + argparse wiring
  course_manager.py  # XML ledger: course map, progress, filenames
  detection.py       # classify(page) -> VIDEO|READING|QUIZ|ASSIGNMENT|PLUGIN|DISCUSSION
  discovery/         # selector discovery, split by responsibility
    rules.py         # page/row classification from text, URLs and titles
    context.py       # identity of the open item: course, module, title
    course_map.py    # the course outline read out of the sidebar
    probing.py       # schema selectors verified live, then persisted
    observation.py   # the discovery loop and its sidebar navigation
    state.py         # ObservationState: what one discovery run accumulates
  element_schema.py  # ELEMENTS_SCHEMA: the built-in selector candidates
  handlers.py        # one handler class per content type
  interaction.py     # mouse / scroll / reading-session pacing, media mute guard
  jitter.py          # human-shaped delays for the observable timing sites
  logs.py            # tree-style formatter over the stdlib logging module
  modals.py          # data-driven modal dismissal rules
  navigation.py      # advance(): Next button with ledger fallback
  page_ops.py        # transcript + reading extraction
  processes.py       # pdex stop: finds and ends running Phantadex processes
  prompt.py          # cross-platform timed y/n prompt
  runner.py          # the main traversal loop
  schema.py          # selector lookups: learned state, packaged defaults, built-ins
  session.py         # CDP attach/detach + connection diagnostics
  storage.py         # filename sanitising, exact dedupe, versioned writes
  text.py            # transcript / reading text normalisation
  timing.py          # duration parsing and reading-time estimation
  urls.py            # Coursera URL identity (item-id based)
  video.py           # player state reads and seeking
tests/               # offline unit tests (no browser required)
```

## 5. Operational Configuration

Configuration is passed on the command line; nothing needs to be edited in
source. Both entry points share the connection flags:

*   `--cdp-url` — DevTools endpoint (default `http://localhost:9222`).
    `PHANTADEX_CDP_URL` moves that default for every command at once, for a
    machine whose port 9222 belongs to something else; the flag still wins
    where it is passed.
*   `--transcript-dir` — root archive directory (default `phantadex_archive`).
    The default is relative, so `pdex watch` and `pdex archive` create it inside
    the directory they are launched from; pass an absolute path to keep one
    archive regardless of where the command runs. `pdex dex` reads the ledger there
    to tell an item it has already archived from one it has not, but writes
    nothing; `pdex skip` and `pdex discover` neither read nor write it. Verified
    selectors always go to the platform state directory (`PHANTADEX_STATE_DIR`
    overrides it).
*   `-v`, `--verbose` — log every browser action: each click (with the label of
    the control it hit and its coordinates), each scroll, each seek, each
    navigation, plus the per-selector failures that are otherwise silent. This
    is the first thing to try when the script stalls on a page.
*   `-q`, `--quiet` — warnings and errors only.
*   `--log-level {DEBUG,INFO,WARNING,ERROR}` — set the level explicitly. Takes
    precedence over `-v` and `-q`.

`pdex watch` adds:

*   `--skip` — seek each video into `97.5-98.5%` before watching, instead of
    playing it through. Off by default: a run watches the whole video. Some
    courses unlock seeking only once an item is complete; there the player takes
    the seek, plays from it for a moment, then returns the position to where it
    was. The run reports the rewind once and watches the video through.
*   `--video-skip-range` — the range `--skip` seeks into, e.g. `97.5-98.5%` or
    `00:30-01:15`. Passing it implies `--skip`.
*   `--no-video-skip` — force the seek off. This is the default; the flag is
    kept because it used to be the only way to ask for it, and it overrides
    `--skip`.
*   `--video-threshold MIN[-MAX]` — percent of a video that must elapse before
    advancing, between `0` and `100`. Fixed (`95`) or a range sampled once per
    video (`98-100`, the default), so two runs of the same video do not advance
    at the same instant.
*   `--reading-minutes MIN[-MAX]` — fallback reading dwell in whole minutes,
    either fixed (`5`) or randomized (`7-12`, the default).
*   `--pause-on-graded` — stop on a graded quiz or peer assignment and wait for
    you to complete it. Without the flag the run names the item and moves to the
    next one; nothing is ever answered or submitted either way.
*   `--no-resume` — start on whichever item the tab is showing. Without the flag
    the run jumps once, on attach, to the first item the sidebar does not mark
    complete, instead of stepping through finished work to reach it.
*   `--items N` — stop after N items, counted from where the run starts rather
    than from the top of the course. An item is one thing to sit through, so a
    peer assignment listed as two sidebar rows counts once.
*   `--modules N` — stop after N modules, counted the same way. A row the map
    does not name belongs to no module and is never the reason a run ends.

`pdex skip` accepts `--video-skip-range` and uses the same random range parser
as Watch; seeking is the whole point of that command, so it always seeks.
`pdex archive` adds `--force` to re-scrape already-archived items.

`pdex stop` accepts `--list`, which reports the running processes without
stopping any of them. It takes the shared flags above as well, so a session
spent typing `--cdp-url` does not end on a parse error.

### Selectors

Selectors resolve in three layers, in this order:

1.  **The verified-selector state file** — what a discovery run last *proved*
    against the live page. Regenerate it with `pdex discover` when Coursera
    changes its markup; the pass probes each selector on a real page and
    records the ones that matched. `python -m phantadex.discovery` runs the
    same pass without the CLI flags.
2.  **`phantadex/config.yaml`** — immutable defaults shipped inside the package.
3.  **`ELEMENTS_SCHEMA` in `phantadex/element_schema.py`** — the hand-maintained
    fallback list, tried last.

Learned state is stored outside the installed package, because a wheel's package
directory can be read-only and a reinstall would otherwise discard every repair:

| Platform | Location |
| --- | --- |
| macOS | `~/Library/Application Support/phantadex/verified_selectors.yaml` |
| Linux | `$XDG_STATE_HOME/phantadex/verified_selectors.yaml` (default `~/.local/state`) |
| Windows | `%LOCALAPPDATA%\phantadex\verified_selectors.yaml` |

Set `PHANTADEX_STATE_DIR` to override the directory. The packaged
`config.yaml` covers all 15 schema elements; a missing or stale state file
degrades to the shipped defaults rather than breaking a run.

Note that a verified selector captures the page *as it was at scan time*. A
control whose label depends on state (the mute button reads "Mute" or "Unmute")
is recorded in whichever state it was scanned in; the fallback list covers the
other, which is why layer 2 is kept rather than replaced.

### Text normalisation

Scraped transcripts and readings are normalised at extraction (`phantadex/text.py`)
so traversal, archival, the ledger, and the duplicate check all see identical
text. This strips screen-reader furniture ("Play video starting at…"), bare
timestamp lines, zero-width spaces (U+200B) and non-breaking spaces (U+00A0).

## 6. Console Output

Status is shown with a small glyph vocabulary rather than emoji:

```
▎ item    a new course item, with its id on the right
  · step  a neutral sub-step
  ✓ done  something completed
  ○ todo  an incomplete course item
  → next  moving to another item
  ! warn  recoverable problem
  ✗ error fatal problem
```

Colour and map-generation spinners are used only when output is an interactive
terminal. Set `NO_COLOR`
to disable it, or `FORCE_COLOR` to keep it when piping. Progress bars repaint in
place on a terminal and print one line per decile when redirected to a file.

## 7. Tests

The suite is offline — no browser, no network:

```bash
python run_tests.py          # unit tests
python run_tests.py -v       # verbose
python run_tests.py --live   # additionally probe a running Chrome session
```

## 8. Known Limitations & Constraints

*   **Graded Instruments:** Never answered and never submitted. By default the
    runner logs the item and advances to the next one. With `--pause-on-graded`
    it waits until the user completes or leaves the item.
*   **Discussion Prompts:** The prompt is archived; no reply is composed or
    submitted.
*   **Dialogue:** Coursera's AI roleplay practice is opened, then closed and the
    closing confirmed, which is what marks the item complete. The conversation
    itself is neither held up nor archived.
*   **Ungraded Plugin / Lab:** Both are stepped over by one handler. The map
    names them apart -- an embedded widget or external tool is an
    `UNGRADED_PLUGIN`, an ungraded lab is a `LAB` -- because Coursera's own row
    subtext does.
*   **Source Site Updates:** The script relies on CSS selectors. If Coursera
    changes its markup these break; `pdex discover` is the repair path (§5).
*   **Transcript Downloads:** The fallback supports Coursera's current Files
    panel and its legacy Downloads tab. The Files `.txt` path is live-verified;
    panel scraping remains the preferred first stage.
*   **Reading Duration:** Taken from the item header when present, otherwise
    from the active sidebar row. If neither is readable the dwell falls back to
    `--reading-minutes` (randomized over `7-12` by default).
*   **Locked/Empty Content Panes:** Locked items retreat to their required
    previous item -- unless the map places the item last, because a course's
    final screen renders no Next control either, and retreating from it sent the
    run in circles until the stall guard ended it. A missing reading body is retried through the bounded handler
    policy and never treated as a scrollable reading. Scrolling is limited to an
    overflowing ancestor of the reading body; short readings dwell without wheel
    input, stop at the bottom, and exit immediately if the active item changes.
*   **Outline:** Row titles and their subtext are read from the element tree, so
    a module that is collapsed when the map is built still yields the same
    result as an expanded one. Each text source is matched on its own: a row's
    subtext names its type by itself, and its `aria-label` names it in the
    first comma-separated field, ahead of the title. Matching the title too
    made a reading called "... for Module 4 Peer Assessment" a peer review.
*   **Surveys:** An item whose title names a questionnaire about the learner is
    stepped past without dwelling, archiving or answering. The marker is a full
    phrase, so a reading about surveys is still treated as course content.
*   **Ledger Size:** A reading can embed a PDF viewer whose rendered text runs
    to book length. The `.txt` file beside the ledger is the archive and keeps
    all of it; the ledger keeps an excerpt cut on a line break, followed by the
    character count and the name of the file holding the rest.
*   **Modals:** Interstitials are cleared through controls that decline them,
    matched by visible text or `aria-label` so an icon-only close button counts.
    The demographics survey is skipped rather than submitted, since submitting
    it would answer questions about the user without them present. A modal that
    offers no such control is reported once and left alone.
*   **Audio:** Media is muted by a guard installed ahead of the page's own
    scripts, so a player is silent from `loadstart` rather than from the next
    poll, and an attempt to raise the volume afterwards is undone. The guard is
    limited to the platform host because it is installed on the browser profile
    the user is already using, and it is removed again when the run detaches:
    the tab outlives the run, and media left under the guard would refuse to be
    unmuted by hand until the page was reloaded.
*   **Ledger Completeness:** An item that is archived but absent from the ledger
    is added to it, under the module the course map gives it or a catch-all
    module when the URL is unmapped. Row types and live page classification can
    disagree, and an archived file with no ledger entry is invisible to a later
    run.
*   **Archive Concurrency:** Course writes use a cross-process lock and atomic
    XML replacement, so Watch and Archive can share a ledger without partial or
    lost writes.
*   **Archive Migration:** The default archive root is `phantadex_archive` and
    ledgers use the course slug. A legacy default course directory and ledger
    are moved only when their Phantadex destinations do not already exist.
*   **OS/IO Locking:** Non-blocking input on Windows requires `msvcrt`. Fallback
    logic defaults to "Stay/Safe" mode where asynchronous input is unavailable.
*   **Console Encoding:** Status output is written through a UTF-8 stream so the
    glyphs survive redirection to a file on a machine whose locale encoding
    cannot represent them.
*   **Reserved Filenames:** Archived filenames avoid the Windows device names
    (`CON`, `NUL`, `COM1`...) on every platform, so an archive stays readable
    after it is copied between systems.

## 9. Disclaimer

This software is a research proof-of-concept for CDP-based automation. It is provided "as is" without warranty. Users assume all liability for its operation and adherence to platform Terms of Service.

## 10. Contributing

See `CONTRIBUTING.md` for guidelines on how to report issues and submit pull requests.

## 11. License

Distributed under the MIT License. See `LICENSE` for more information.
