"""Starts Google Chrome with the CDP debugging port the tool attaches to.

The tool never launches its own browser during a run: it connects to the
Chrome the user is already signed into. That browser has to be started once
with ``--remote-debugging-port``, which is what this module does, on macOS,
Windows and Linux alike.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import config, logs, schema

DEBUG_URL_PATH = "/json/version"
PROFILE_DIR_ENV = "PHANTADEX_CHROME_PROFILE"
CHROME_BINARY_ENV = "PHANTADEX_CHROME"
# Chrome needs a moment between the process starting and the port listening.
PORT_WAIT_SECONDS = 2


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


def _debug_port(cdp_url):
    """Extracts the port from a CDP URL, falling back to the default."""
    tail = cdp_url.rsplit(":", 1)[-1].split("/", 1)[0]
    return int(tail) if tail.isdigit() else 9222


def main(argv=None):
    """Starts the debug browser, or reports that it is already running."""
    parser = config.build_parser("Phantadex Chrome -- start the debug browser.")
    settings = config.settings_from_args(parser.parse_args(argv))
    logs.setup(settings.log_level)

    port = _debug_port(settings.cdp_url)
    debug_url = f"http://localhost:{port}{DEBUG_URL_PATH}"

    if port_in_use(port):
        logs.ok(f"debug Chrome is already listening on port {port}")
        logs.step(f"verify at {debug_url}")
        return 0

    executable = chrome_binary()
    if executable is None:
        logs.error(f"Chrome not found. Set {CHROME_BINARY_ENV} to the executable path.")
        return 1

    target_profile = profile_dir()
    target_profile.mkdir(parents=True, exist_ok=True)
    logs.step(f"starting Chrome with remote debugging on port {port}")
    try:
        subprocess.Popen(
            [
                str(executable),
                f"--remote-debugging-port={port}",
                f"--user-data-dir={target_profile}",
                "--new-window",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        logs.error(f"Failed to start Chrome: {error}")
        return 1

    time.sleep(PORT_WAIT_SECONDS)
    if port_in_use(port):
        logs.ok(f"debug endpoint ready at {debug_url}")
        logs.step("sign in to the course in this window, then run pdex dex")
    else:
        logs.warn(f"Chrome started, but port {port} is not listening yet; check {debug_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
