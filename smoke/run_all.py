"""Run every smoke/*.py check (or just verify env keys with --keys-only).

Extra args (other than --keys-only) are passed through to each script.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import load_env  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
KEYS = [
    # P1: agent LLM
    "SIE_API_KEY", "SIE_BASE_URL", "FALLBACK_LLM_API_KEY", "FALLBACK_LLM_BASE_URL",
    "FALLBACK_LLM_MODEL", "LLM_PROVIDER", "TOOL_MODE",
    # P2: data layer
    "APIFY_TOKEN", "SINGLESTORE_HOST", "SINGLESTORE_USER", "SINGLESTORE_PASSWORD", "COGNEE_API_KEY",
    # sponsors
    "STATSIG_SERVER_KEY", "FIREDRILL_API_KEY",
]


def keys_only() -> int:
    load_env()
    missing = 0
    for k in KEYS:
        ok = bool(os.environ.get(k, "").strip())
        missing += not ok
        print(f"{k}: {'set' if ok else 'MISSING'}")
    return 1 if missing else 0


def run_checks(extra: list[str]) -> int:
    files = sorted(p for p in HERE.glob("*.py") if not p.name.startswith("_") and p.name != "run_all.py")
    summary: list[tuple[str, str]] = []
    for f in files:
        print(f"=== {f.name} ===", flush=True)
        try:
            rc = subprocess.run([sys.executable, str(f), *extra], cwd=ROOT, timeout=120).returncode
            status = "PASS" if rc == 0 else "FAIL"
        except subprocess.TimeoutExpired:
            print("FAIL: timeout after 120s")
            status = "FAIL"
        summary.append((f.name, status))
    print("\n=== summary ===")
    for name, status in summary:
        print(f"{name:<28} {status}")
    return 1 if any(s == "FAIL" for _, s in summary) else 0


if __name__ == "__main__":
    if "--keys-only" in sys.argv:
        sys.exit(keys_only())
    sys.exit(run_checks([a for a in sys.argv[1:] if a != "--keys-only"]))
