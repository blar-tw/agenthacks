"""Run every smoke/*.py check (or just verify env keys with --keys-only)."""
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
    "SIE_API_KEY", "SIE_BASE_URL", "FALLBACK_LLM_API_KEY", "FALLBACK_LLM_BASE_URL",
    "FALLBACK_LLM_MODEL", "LLM_PROVIDER", "TOOL_MODE",
]


def keys_only() -> int:
    load_env()
    missing = 0
    for k in KEYS:
        ok = bool(os.environ.get(k, "").strip())
        missing += not ok
        print(f"{k}: {'set' if ok else 'MISSING'}")
    return 1 if missing else 0


def run_checks() -> int:
    files = sorted(p for p in HERE.glob("*.py") if not p.name.startswith("_") and p.name != "run_all.py")
    summary: list[tuple[str, str]] = []
    for f in files:
        print(f"=== {f.name} ===", flush=True)
        try:
            rc = subprocess.run([sys.executable, str(f)], cwd=ROOT, timeout=120).returncode
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
    sys.exit(keys_only() if "--keys-only" in sys.argv else run_checks())
