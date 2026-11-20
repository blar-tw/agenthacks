"""Check that a chat LLM returns a valid tool call (SIE and fallback gateway)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import Skip, main  # noqa: E402

import httpx  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402
from pydantic import BaseModel  # noqa: E402

PROMPT = (
    "Find me spicy Chinese food for two under $40. I prefer noodles. I do not like mushrooms. "
    "I want the cheapest option that arrives within 40 minutes."
)
DEFAULT_SIE_MODEL = "Qwen/Qwen3.8-27B"


class Constraints(BaseModel):
    cuisine: str
    people: int
    budget_total: float
    max_eta_min: int
    hard_excludes: list[str]


def _env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise Skip(f"{name} not set")
    return v


def _invoke(base_url: str, key: str, model: str) -> str:
    llm = ChatOpenAI(model=model, base_url=base_url, api_key=key, temperature=0, max_tokens=200, timeout=60)
    t0 = time.time()
    try:
        msg = llm.bind_tools([Constraints], tool_choice="Constraints").invoke(PROMPT)
    except Exception as e:  # noqa: BLE001
        print(f"  forced tool_choice failed ({type(e).__name__}); retrying with auto", flush=True)
        msg = llm.bind_tools([Constraints], tool_choice="auto").invoke(PROMPT)
    ms = int((time.time() - t0) * 1000)
    if not msg.tool_calls:
        raise RuntimeError(f"no tool calls in response (content len {len(str(msg.content))})")
    if os.environ.get("LLM_PROVIDER") == "fallback":
        from agent import llm as agent_llm
        agent_llm.record_fallback_cost(msg)
    c = Constraints.model_validate(msg.tool_calls[0]["args"])
    if c.people != 2 or c.budget_total != 40:
        raise RuntimeError(f"unexpected values: {c.model_dump()}")
    return f"model={model} {c.model_dump()} latency={ms}ms"


def sie() -> str:
    key = _env("SIE_API_KEY")
    base = _env("SIE_BASE_URL").rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    model = os.environ.get("SIE_CHAT_MODEL", "").strip()
    if not model:
        try:
            r = httpx.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=20)
            r.raise_for_status()
            ids = [m.get("id", "") for m in r.json().get("data", [])]
            hits = [i for i in ids if "qwen" in i.lower() and "27b" in i.lower()]
            model = hits[0] if hits else DEFAULT_SIE_MODEL
            print(f"  discovered {len(ids)} models", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  model listing failed ({type(e).__name__}); using default", flush=True)
            model = DEFAULT_SIE_MODEL
    print(f"  SIE model id: {model}", flush=True)
    return _invoke(base, key, model)


def fallback() -> str:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from agent import llm as agent_llm  # spend cap lives there

    if agent_llm.fallback_over_budget():
        raise Skip(f"OpenRouter cap reached: ${agent_llm.fallback_spent():.2f} of ${agent_llm.fallback_budget():.2f}")
    os.environ["LLM_PROVIDER"] = "fallback"  # so the cost is recorded against the cap
    result = _invoke(_env("FALLBACK_LLM_BASE_URL"), _env("FALLBACK_LLM_API_KEY"), _env("FALLBACK_LLM_MODEL"))
    return f"{result} spent=${agent_llm.fallback_spent():.4f}/${agent_llm.fallback_budget():.0f}"


if __name__ == "__main__":
    main([("sie", sie), ("fallback", fallback)])
