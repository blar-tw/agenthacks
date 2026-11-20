"""Shared helpers for smoke checks. Never prints secret values."""
from __future__ import annotations

from pathlib import Path

import os
import sys
from typing import Callable

from dotenv import load_dotenv

ENV_PATH = str(Path(__file__).resolve().parent.parent / ".env")


class Skip(Exception):
    """Raise inside a check to report SKIP (not a failure)."""


def load_env() -> None:
    load_dotenv(ENV_PATH)


def _redact(msg: str) -> str:
    for v in sorted({v for v in os.environ.values() if len(v) > 6}, key=len, reverse=True):
        if v in msg:
            msg = msg.replace(v, "<redacted>")
    return msg


def check(name: str, fn: Callable[[], "str | None"]) -> bool:
    """Run fn; print PASS/FAIL/SKIP. Returns False only on FAIL."""
    try:
        detail = fn()
    except Skip as s:
        print(f"SKIP {name}: {_redact(str(s))}", flush=True)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"FAIL {name}: {type(e).__name__}: {_redact(str(e))}", flush=True)
        return False
    print(f"PASS {name}: {_redact(detail or 'ok')}", flush=True)
    return True


def main(checks: "list[tuple[str, Callable]]") -> None:
    load_env()
    results = [check(name, fn) for name, fn in checks]
    sys.exit(0 if all(results) else 1)
