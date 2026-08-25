"""Starts Google Chrome with the CDP debugging port the tool attaches to.

The tool never launches its own browser during a run: it connects to the
Chrome the user is already signed into. That browser has to be started once
with ``--remote-debugging-port``, which is what this module does, on macOS,
Windows and Linux alike.
"""

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config, logs, schema

log = logs.get_logger("chrome")

DEBUG_URL_PATH = "/json/version"
PROFILE_DIR_ENV = "PHANTADEX_CHROME_PROFILE"
CHROME_BINARY_ENV = "PHANTADEX_CHROME"
# Chrome needs a moment between the process starting and the port answering.
# Polled rather than slept through: a cold start on a loaded machine takes
# longer than any single wait short enough to be worth hard-coding.
ENDPOINT_WAIT_SECONDS = 12
# How long the holder of an occupied port is given to identify itself as a debug
# browser. A signed-in Chrome under load can miss a single probe, and refusing it
# sends the user to a second port with their session stranded on the first.
ENDPOINT_CONFIRM_SECONDS = 3
ENDPOINT_POLL_SECONDS = 0.5
ENDPOINT_PROBE_TIMEOUT = 1.0

# Windows process-creation flags. Taken from the standard library where it
# defines them, which is on Windows only; the literals let the launcher be
# named and tested from any platform.
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_BREAKAWAY_FROM_JOB = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)


def profile_dir():
    """Returns the Chrome profile directory the launcher starts Chrome with.

    A dedicated profile keeps the automated window out of the user's everyday
    one, and it lives beside the tool's other state rather than inside the
    installed package, which a wheel install makes read-only.
    """
    override = os.environ.get(PROFILE_DIR_ENV)
    if override:
        return Path(os.path.abspath(os.path.expanduser(override)))
    return Path(schema.state_dir()) / "chrome_profile"


def chrome_candidates():
    """Returns platform-native Chrome locations in likely installation order."""
    if sys.platform == "darwin":
        return [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    if sys.platform == "win32":
        candidates = []
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(variable)
            if root:
                candidates.append(Path(root) / "Google/Chrome/Application/chrome.exe")
        return candidates
    # Linux ships Chrome and Chromium under several names depending on the
    # distribution and the packaging format.
    names = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
    return [Path(found) for found in (shutil.which(name) for name in names) if found]


def chrome_binary():
    """Returns the first usable Chrome executable, or ``None``."""
    configured = os.environ.get(CHROME_BINARY_ENV)
    candidates = ([Path(configured)] if configured else []) + chrome_candidates()
    return next((path for path in candidates if path.is_file()), None)


def port_in_use(port):
    """Reports whether something already listens on the debugging port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def debug_endpoint_ready(port, timeout_seconds=ENDPOINT_PROBE_TIMEOUT):
    """Reports whether the port answers as a DevTools debugging endpoint.

    A bare TCP connect only proves that something holds the port. On Windows
    that something is regularly an OEM helper or a WebView2 host, and taking it
    for the debug browser left this command announcing success without ever
    starting Chrome -- so every later command attached to a browser with no
    course in it and reported no course tab found.

    A holder that accepts the connection and then answers with something other
    than HTTP raises past ``urllib``'s own error type, and that is exactly the
    listener this probe exists to reject, so it is caught here as well.
    """
    url = f"http://127.0.0.1:{port}{DEBUG_URL_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError) as exc:
        log.debug("Debug endpoint probe on port %d failed: %s", port, exc)
        return False
    return bool(isinstance(payload, dict) and payload.get("webSocketDebuggerUrl"))


def _await_debug_endpoint(port, deadline_seconds=ENDPOINT_WAIT_SECONDS):
    """Waits for a freshly started Chrome to answer on its debugging port."""
    deadline = time.monotonic() + deadline_seconds
    while True:
        if debug_endpoint_ready(port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(ENDPOINT_POLL_SECONDS)


def _spawn_options():
    """Returns the ``Popen`` keywords that outlive the shell Chrome started from.

    POSIX detaches by starting a new session. Windows ignores that argument
    entirely, so the equivalent has to be asked for by creation flag, or Chrome
    is killed with the terminal that launched it.
    """
    if sys.platform != "win32":
        return {"start_new_session": True}
    return {"creationflags": DETACHED_PROCESS | CREATE_BREAKAWAY_FROM_JOB}


def _launch(executable, port, target_profile):
    """Starts Chrome with the debugging port open, detached from this process."""
    command = [
        str(executable),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={target_profile}",
        # A dedicated profile directory is a fresh one the first time, and
        # Chrome greets a fresh profile with the setup wizard and the
        # default-browser prompt, both of which sit in front of the course.
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
    ]
    options = _spawn_options()
    try:
        return subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options
        )
    except OSError as error:
        if "creationflags" not in options:
            raise
        # A process already inside a job object that forbids breakaway cannot
        # ask for one, and Windows refuses the whole spawn rather than the
        # flag. Starting an attached Chrome beats starting none.
        log.debug("Detached spawn refused (%s); retrying attached", error)
        return subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=DETACHED_PROCESS,
        )


def port_owner_command(port):
    """Returns the platform's command for naming what holds a port."""
    if os.name == "nt":
        return f"netstat -ano | findstr :{port}"
    return f"lsof -nP -iTCP:{port} -sTCP:LISTEN"


def debug_port(cdp_url):
    """Extracts the port from a CDP URL, falling back to the default."""
    tail = cdp_url.rsplit(":", 1)[-1].split("/", 1)[0]
    return int(tail) if tail.isdigit() else 9222


def main(argv=None):
    """Starts the debug browser, or reports that it is already running."""
    parser = config.build_parser("Phantadex Chrome -- start the debug browser.", "chrome")
    settings = config.settings_from_args(parser.parse_args(argv))
    logs.setup(settings.log_level)

    port = debug_port(settings.cdp_url)
    debug_url = f"http://localhost:{port}{DEBUG_URL_PATH}"

    if port_in_use(port):
        if _await_debug_endpoint(port, ENDPOINT_CONFIRM_SECONDS):
            logs.ok(f"debug Chrome is already listening on port {port}")
            logs.step(f"verify at {debug_url}")
            return 0
        # Held, but not by a debug browser. Reported rather than launched
        # around: a second Chrome cannot take a port that is already taken,
        # and claiming the endpoint is ready would send every later command
        # to whatever is answering there.
        logs.error(f"port {port} is held by something that is not a debug browser")
        logs.step(f"identify it with: {port_owner_command(port)}")
        logs.tip(
            f"or move Phantadex to a free port: set {config.CDP_URL_ENV} to "
            f"http://localhost:{port + 1} and run this command again"
        )
        return 1

    executable = chrome_binary()
    if executable is None:
        logs.error(f"Chrome not found. Set {CHROME_BINARY_ENV} to the executable path.")
        return 1

    target_profile = profile_dir()
    target_profile.mkdir(parents=True, exist_ok=True)
    logs.step(f"starting Chrome with remote debugging on port {port}")
    try:
        _launch(executable, port, target_profile)
    except OSError as error:
        logs.error(f"Failed to start Chrome: {error}")
        return 1

    if not _await_debug_endpoint(port):
        # Reported as a failure rather than a warning. The command exists to
        # leave an attachable browser behind, and a caller chaining the next
        # command onto this one would otherwise proceed against a port that
        # never bound and read the attach failure as its own.
        logs.error(
            f"Chrome started, but nothing answered on port {port} within "
            f"{ENDPOINT_WAIT_SECONDS}s; check {debug_url}"
        )
        return 1

    logs.ok(f"debug endpoint ready at {debug_url}")
    logs.step(f"sign in to the course in this window, then run {config.program_name('dex')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
