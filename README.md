# Phantadex

![Version](https://img.shields.io/badge/version-2.0.0-blue?style=flat-square)
![Python](https://img.shields.io/badge/python-3.11%2B-green?style=flat-square)
![Protocol](https://img.shields.io/badge/protocol-CDP-orange?style=flat-square)
![Status](https://img.shields.io/badge/status-Research_Prototype-red?style=flat-square)

> [!WARNING]
> **Use at your own risk.** This tool relies on CSS selectors that Coursera can change at any time. Site updates may require selector changes or manual intervention.

## 1. System Overview

Phantadex attaches to an existing, authenticated Chrome session through the
remote debugging protocol and provides two course workflows.

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
    `detection.classify(page)`:
    *   **Video:** Watches to the configured completion threshold, or to the
        player's own `ended` event.
    *   **Reading:** Dwells for the item's listed duration while scrolling the
        content pane, then marks complete.
    *   **Discussion:** Archives the prompt and advances. No reply is composed
        or submitted.
    *   **Plugins / LTI:** Detected and stepped over.
    *   **Graded work:** Detected and handed back to you (see §8).
6.  **Pointer Consistency:** Automated controls share one interaction path that
    moves through several cursor steps, hovers, pauses for a randomized reaction
    interval, and then clicks.

## 2. Technical Prerequisites

The system requires an initialized debugging interface on the host browser.

### A. Environment
*   **Python:** 3.11+
*   **Dependencies:** `playwright`, `plyer`, `PyYAML` -- all three are declared in
    `pyproject.toml`, so `pip install -e .` pulls them in.
*   **Browser:** Google Chrome. The tool attaches to the browser you already use
    and never launches its own, so Playwright's bundled browsers are not needed.

### B. Installation

Once the package is on PyPI, both platforms take one command. `pipx` keeps the
tool in its own environment and still puts `pdex` on the PATH:

```bash
pipx install phantadex        # or: python -m pip install --user phantadex
```

**From a clone (current method, and how to develop):**

Windows (PowerShell):
```powershell
git clone https://github.com/SNiPERxDD/phantadex.git
cd phantadex
python -m venv .venv

# Allows the activation script to run in this window only; a new terminal
# needs it again.
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned -Force
.\.venv\Scripts\Activate.ps1

pip install -e .
```

macOS / Linux:
```bash
git clone https://github.com/SNiPERxDD/phantadex.git
cd phantadex
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

`pip install -e .` pulls in `playwright`, `plyer` and `PyYAML`. Playwright's
bundled browsers are not needed: the tool attaches to the Chrome you already
use and never launches one of its own.

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
    each content type the course contains has been verified.
    ```bash
    pdex discover
    # Same pass, with every probe and hop logged
    pdex discover -v
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
  cli.py             # dex/skip/watch/archive/discover/chrome dispatch
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
  interaction.py     # mouse / scroll / reading-session pacing
  logs.py            # tree-style formatter over the stdlib logging module
  modals.py          # data-driven modal dismissal rules
  navigation.py      # advance(): Next button with ledger fallback
  page_ops.py        # transcript + reading extraction
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
*   `--transcript-dir` — root archive directory (default `phantadex_archive`).
    The default is relative, so `pdex watch` and `pdex archive` create it inside
    the directory they are launched from; pass an absolute path to keep one
    archive regardless of where the command runs. `pdex dex`, `pdex skip` and
    `pdex discover` write nothing there — verified selectors always go to the
    platform state directory (`PHANTADEX_STATE_DIR` overrides it).
*   `-v`, `--verbose` — log every browser action: each click (with the label of
    the control it hit and its coordinates), each scroll, each seek, each
    navigation, plus the per-selector failures that are otherwise silent. This
    is the first thing to try when the script stalls on a page.
*   `-q`, `--quiet` — warnings and errors only.
*   `--log-level {DEBUG,INFO,WARNING,ERROR}` — set the level explicitly. Takes
    precedence over `-v` and `-q`.

`pdex watch` adds:

*   `--video-skip-range` — seek videos into this range before watching, e.g.
    `97.5-98.5%` or `00:30-01:15` (default `97.5-98.5%`).
*   `--no-video-skip` — disable the pre-watch seek and play videos through.
*   `--video-threshold` — percent of a video that must elapse before advancing,
    between `0` and `100` (default `100`). This value is the target; playback
    variation comes from `--video-skip-range`, which is randomized by design.
*   `--reading-minutes MIN[-MAX]` — fallback reading dwell in whole minutes,
    either fixed (`5`) or randomized (`7-12`, the default).

`pdex skip` accepts `--video-skip-range` and uses the same random range parser
as Watch. `pdex archive` adds `--force` to re-scrape already-archived items.

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

240 tests, all offline — no browser, no network:

```bash
python run_tests.py          # unit tests
python run_tests.py -v       # verbose
python run_tests.py --live   # additionally probe a running Chrome session
```

## 8. Known Limitations & Constraints

*   **Graded Instruments:** The runner pauses on graded quizzes and assignments
    until the user completes or leaves the item; it never submits them.
*   **Discussion Prompts:** The prompt is archived; no reply is composed or
    submitted.
*   **Source Site Updates:** The script relies on CSS selectors. If Coursera
    changes its markup these break; `pdex discover` is the repair path (§5).
*   **Transcript Downloads:** The fallback supports Coursera's current Files
    panel and its legacy Downloads tab. The Files `.txt` path is live-verified;
    panel scraping remains the preferred first stage.
*   **Reading Duration:** Taken from the item header when present, otherwise
    from the active sidebar row. If neither is readable the dwell falls back to
    `--reading-minutes` (randomized over `7-12` by default).
*   **Locked/Empty Content Panes:** Locked items retreat to their required
    previous item. A missing reading body is retried through the bounded handler
    policy and never treated as a scrollable reading. Scrolling is limited to an
    overflowing ancestor of the reading body; short readings dwell without wheel
    input, stop at the bottom, and exit immediately if the active item changes.
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
