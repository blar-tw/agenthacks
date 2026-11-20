"""Shared helpers for smoke checks. Never prints secret values.

Two styles, both supported: P1's `main([(name, fn), ...])` with `check`/`Skip`,
and P2's `Smoke(name)` object with ok/fail/need/done.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

ENV_PATH = str(ROOT / ".env")
load_dotenv(ENV_PATH)

def load_env() -> None:
    load_dotenv(ENV_PATH)


def redact(text: str) -> str:
    """Remove every env value longer than 6 chars, and token-shaped strings, from a message."""
    for v in sorted({v for v in os.environ.values() if len(v) > 6}, key=len, reverse=True):
        if v in text:
            text = text.replace(v, "<redacted>")
    return re.sub(r"(sk-[A-Za-z0-9_-]{6,}|apify_api_\w{6,}|Bearer\s+\S+)", "<redacted>", text)


_redact = redact  # P1 name


class Skip(Exception):
    """Raise inside a check to report SKIP (not a failure)."""


def check(name: str, fn: Callable[[], "str | None"]) -> bool:
    """Run fn; print PASS/FAIL/SKIP. Returns False only on FAIL."""
    try:
        detail = fn()
    except Skip as s:
        print(f"SKIP {name}: {redact(str(s))}", flush=True)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"FAIL {name}: {type(e).__name__}: {redact(str(e))}", flush=True)
        return False
    print(f"PASS {name}: {redact(detail or 'ok')}", flush=True)
    return True


def main(checks: "list[tuple[str, Callable]]") -> None:
    load_env()
    results = [check(name, fn) for name, fn in checks]
    sys.exit(0 if all(results) else 1)


class Smoke:
    """P2 style: one object per script, exits non-zero if any fail()."""

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
