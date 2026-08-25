"""Offline test suite. No browser or network required.

Importing this package redirects the state directory somewhere disposable. The
suite exercises entry points that write for real -- a run log, the resume
ledger, the discovered selectors -- and pointing them at the state directory a
person's own runs use meant a test run edited their data and left hundreds of
kilobytes of test output in their log folder.
"""

import atexit
import os
import shutil
import tempfile

if "PHANTADEX_STATE_DIR" not in os.environ:
    _state_dir = tempfile.mkdtemp(prefix="phantadex-tests-")
    os.environ["PHANTADEX_STATE_DIR"] = _state_dir
    atexit.register(shutil.rmtree, _state_dir, ignore_errors=True)
