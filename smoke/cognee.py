"""Cognee Cloud: remember, then recall (Brief 12, open item 2).

On Cognee Cloud the graph is built remotely with Cognee's own hosted models, so the local
SIE/OpenAI LLM settings only matter for local (non-Cloud) mode. This check reports which mode ran.
Needs the optional dependency: uv sync --extra memory
"""
import asyncio
import importlib.util
import os
import sys
import uuid
from pathlib import Path

from _common import Smoke

# This file is named cognee.py: drop smoke/ from the path so `import cognee` finds the package.
_HERE = Path(__file__).resolve().parent
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _HERE]
sys.modules.pop("cognee", None)

from data import distill  # noqa: E402

s = Smoke("cognee")


async def roundtrip(tag: str) -> list[str]:
    await distill.cognee_remember([f"[episode smoke-{tag}] The smoke-test user {tag} loves spicy dan dan noodles."],
                                  dataset="mealmatch_smoke")
    out: list[str] = []
    for _ in range(9):   # Cloud processes remember() in the background: ask again until it knows
        out = await distill.cognee_recall(f"What does the smoke-test user {tag} love to eat?",
                                          dataset="mealmatch_smoke", wait_s=90)
        if any("noodle" in t.lower() for t in out):
            break
        await asyncio.sleep(10)
    return out


if importlib.util.find_spec("cognee") is None:
    s.fail("cognee is not installed (run: uv sync --extra memory)")
elif s.need("COGNEE_API_KEY"):
    cloud = bool(os.environ.get("COGNEE_SERVICE_URL"))
    if not cloud:
        s.info("COGNEE_SERVICE_URL is not set: running Cognee locally with SIE as its LLM")
    # OpenAI is the user's own paid key: only local mode could need it, and only with --allow-openai.
    os.environ["MEALMATCH_COGNEE_MODE"] = "openai" if "--allow-openai" in sys.argv else "sie"
    tag = uuid.uuid4().hex[:6]
    try:
        out = asyncio.run(roundtrip(tag))
        if any("noodle" in t.lower() for t in out):
            s.ok(f"remember -> recall on {'Cognee Cloud (hosted models)' if cloud else 'local Cognee + SIE'}: {out[0][:80]!r}")
        else:
            s.fail(f"recall did not mention noodles: {out[:2]!r}")
    except Exception as exc:
        s.error("remember/recall", exc)
        s.info("fallback: DISTILLER=counting (Brief 9.6 cut 1)")
s.done()
