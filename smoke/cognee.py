"""Cognee: remember, then recall. Reports whether SIE works as Cognee's LLM, or the
OpenAI fallback is needed. Needs the optional dependency: uv sync --extra memory
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


async def roundtrip(tag: str) -> str:
    import cognee
    if os.environ.get("COGNEE_API_KEY"):
        await cognee.serve(url=os.environ.get("COGNEE_SERVICE_URL") or None, api_key=os.environ["COGNEE_API_KEY"])
    await cognee.remember(f"[episode smoke-{tag}] The smoke-test user {tag} loves spicy dan dan noodles.")
    res = await cognee.recall(f"What does the smoke-test user {tag} love to eat?")
    return str(res)


def attempt(mode: str) -> bool:
    used = distill.configure_cognee(use_sie=(mode == "sie"))
    if used != mode:
        s.info(f"{mode}: not configured (missing key)")
        return False
    tag = uuid.uuid4().hex[:6]
    try:
        out = asyncio.run(roundtrip(tag))
    except Exception as exc:
        s.info(f"{mode}: {type(exc).__name__}: {str(exc)[:200]}")
        return False
    if "noodle" in out.lower():
        s.ok(f"remember -> recall works with {mode.upper()} as Cognee's LLM")
        return True
    s.info(f"{mode}: recall did not mention noodles: {out[:160]!r}")
    return False


if importlib.util.find_spec("cognee") is None:
    s.fail("cognee is not installed (run: uv sync --extra memory)")
elif s.need("COGNEE_API_KEY"):
    # OpenAI is the user's own paid key: try it only when asked (--allow-openai).
    if attempt("sie"):
        s.info("SIE works as Cognee's LLM: keep DISTILLER=cognee, no OpenAI key needed")
    elif "--allow-openai" not in sys.argv:
        s.fail("SIE did not work as Cognee's LLM. OpenAI fallback not tried (needs --allow-openai); "
               "or use DISTILLER=counting (Brief 9.6 cut 1)")
    elif attempt("openai"):
        s.info("SIE does NOT work as Cognee's LLM; OpenAI fallback works (Brief 12, open item 2)")
    else:
        s.fail("neither SIE nor OpenAI worked for Cognee; use DISTILLER=counting (Brief 9.6 cut 1)")
s.done()
