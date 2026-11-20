"""Run every smoke/*.py and summarize. --keys-only checks only that each env var exists.

STAND-IN written by P2 while P1's smoke/run_all.py is not on main. P1's version wins.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _common import Smoke  # noqa: E402

KEYS = ["SIE_API_KEY", "APIFY_TOKEN", "SINGLESTORE_HOST", "SINGLESTORE_USER", "SINGLESTORE_PASSWORD",
        "COGNEE_API_KEY", "ANTHROPIC_API_KEY", "STATSIG_SERVER_KEY", "FIREDRILL_API_KEY"]


def main() -> None:
    if "--keys-only" in sys.argv:
        s = Smoke("keys")
        for k in KEYS:
            if os.environ.get(k, "").strip():
                s.ok(f"{k} is set")
            else:
                s.fail(f"{k} is not set")
        s.done()

    scripts = sorted(p for p in HERE.glob("*.py") if not p.name.startswith("_") and p.name != "run_all.py")
    extra = [a for a in sys.argv[1:] if a != "--keys-only"]
    results = []
    for p in scripts:
        print(f"--- {p.name}", flush=True)
        r = subprocess.run([sys.executable, str(p), *extra], cwd=HERE.parent)
        results.append((p.name, r.returncode))
    print("--- summary")
    for name, code in results:
        print(f"{'PASS' if code == 0 else 'FAIL'} {name}")
    sys.exit(1 if any(code for _, code in results) else 0)


if __name__ == "__main__":
    main()
