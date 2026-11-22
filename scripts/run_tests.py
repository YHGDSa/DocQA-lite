"""Run the test suite with TMP/TEMP pointed inside the repository.

Some sandboxed environments (CI containers, IDE sandboxes) hand the process a
system temp directory that pytest cannot enumerate, which shows up as a
``PermissionError`` while pytest creates its basetemp. This script keeps the
tests unchanged and moves the temp root inside ``.tmp/`` instead.

Usage:

    python scripts/run_tests.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / ".tmp"


def main() -> int:
    TMP.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "TMP": str(TMP),
        "TEMP": str(TMP),
        "TMPDIR": str(TMP),
    }
    return subprocess.call(
        [sys.executable, "-m", "pytest", "-q"], cwd=str(ROOT), env=env
    )


if __name__ == "__main__":
    raise SystemExit(main())
