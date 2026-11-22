"""CI guard: the offline eval must have produced a baseline/delta report.

Usage: python scripts/check_eval_report.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "eval" / "report.md"
REQUIRED = ("baseline", "delta")


def main() -> int:
    if not REPORT.exists():
        print(f"missing {REPORT}; run `python eval/run_eval.py` first")
        return 1
    text = REPORT.read_text(encoding="utf-8")
    missing = [word for word in REQUIRED if word not in text]
    if missing:
        print(f"report missing {missing}")
        return 1
    print(f"eval report ok: {REPORT} ({len(text)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
