#!/usr/bin/env python3
"""Start Google Chrome with CDP debugging on macOS or Windows."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

DEBUG_PORT = 9222
PROFILE_DIR = Path(__file__).resolve().parent.parent / "chrome_profile"
DEBUG_URL = f"http://localhost:{DEBUG_PORT}/json/version"


def _chrome_candidates():
    """Return platform-native Chrome locations in likely installation order."""
    if sys.platform == "darwin":
        return [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    if sys.platform == "win32":
        candidates = []
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(variable)
            if root:
                candidates.append(Path(root) / "Google/Chrome/Application/chrome.exe")
        return candidates
    return []


def _chrome_binary():
    """Return the first usable Chrome executable, or ``None``."""
    configured = os.environ.get("PHANTADEX_CHROME")
    candidates = ([Path(configured)] if configured else []) + _chrome_candidates()
    return next((path for path in candidates if path.is_file()), None)


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _open_debug_page(url: str) -> None:
    print("Opening the Chrome debug endpoint in a browser tab...")
    if sys.platform == "win32":
        try:
            os.startfile(url)
            return
        except OSError:
            print(f"Unable to open a browser tab. Visit {url} manually.")
            return
    for args in (["open", "-a", "Google Chrome", url], ["open", url]):
        try:
            r = subprocess.run(
                args,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if r.returncode == 0:
                return
        except OSError:
            continue
    print(f"Unable to open a browser tab. Visit {url} manually.")


def main() -> int:
    if sys.platform not in {"darwin", "win32"}:
        print("This launcher supports macOS and Windows.")
        return 1

    chrome_bin = _chrome_binary()
    if chrome_bin is None:
        print("Chrome not found. Set PHANTADEX_CHROME to the executable path.")
        return 1

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    if _port_in_use(DEBUG_PORT):
        print(f"Port {DEBUG_PORT} is already in use.")
        _open_debug_page(DEBUG_URL)
        return 0

    print(f"Starting Chrome with remote debugging on port {DEBUG_PORT}...")
    try:
        subprocess.Popen(
            [
                str(chrome_bin),
                f"--remote-debugging-port={DEBUG_PORT}",
                f"--user-data-dir={PROFILE_DIR}",
                "--new-window",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        print(f"Failed to start Chrome: {e}")
        return 1

    time.sleep(2)

    if _port_in_use(DEBUG_PORT):
        print(f"Chrome debug endpoint is ready at {DEBUG_URL}")
        _open_debug_page(DEBUG_URL)
    else:
        print(f"Chrome started, but debug port {DEBUG_PORT} is not listening yet.")
        print(f"Wait a few seconds and check: {DEBUG_URL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
