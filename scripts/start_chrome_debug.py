#!/usr/bin/env python3
"""Compatibility wrapper: the launcher now ships inside the package.

Kept so the path documented in earlier releases keeps working. ``pdex chrome``
is the supported entry point and is available wherever the package is
installed, including a wheel from an index.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phantadex.chrome import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
