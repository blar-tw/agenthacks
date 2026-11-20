"""Smoke-check helper. Prints PASS/FAIL with a reason, never prints a secret,
exits non-zero on failure.

STAND-IN written by P2 while P1's smoke/_common.py is not on main. P1's version wins.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

_SECRET_NAMES = re.compile(r"KEY|TOKEN|PASSWORD|SECRET", re.I)


def redact(text: str) -> str:
    """Remove every secret env value from a message before it is printed."""
    for k, v in os.environ.items():
        if v and len(v) >= 6 and _SECRET_NAMES.search(k):
            text = text.replace(v, "[redacted]")
    return re.sub(r"(sk-[A-Za-z0-9_-]{6,}|apify_api_\w{6,}|Bearer\s+\S+)", "[redacted]", text)


class Smoke:
    def __init__(self, name: str):
        self.name = name
        self.failed = 0

    def ok(self, msg: str) -> None:
        print(f"PASS {self.name}: {redact(msg)}", flush=True)

    def fail(self, msg: str) -> None:
        print(f"FAIL {self.name}: {redact(msg)}", flush=True)
        self.failed += 1

    def info(self, msg: str) -> None:
        print(f"     {self.name}: {redact(msg)}", flush=True)

    def need(self, *names: str) -> bool:
        missing = [n for n in names if not os.environ.get(n, "").strip()]
        if missing:
            self.fail(f"missing env var(s): {', '.join(missing)}")
            return False
        return True

    def error(self, label: str, exc: BaseException) -> None:
        self.fail(f"{label}: {type(exc).__name__}: {str(exc)[:300]}")

    def done(self) -> None:
        sys.exit(1 if self.failed else 0)
