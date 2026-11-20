"""Chat LLM client and the 3 P1 LLM jobs (parse, explain, read feedback).

LLM_PROVIDER=sie       -> Qwen on Superlinked SIE (doc 5.2)
LLM_PROVIDER=fallback  -> OpenAI-compatible gateway (OpenRouter, Claude Sonnet)
LLM_PROVIDER=none      -> no model; rule-based parsing and templated text (tests)

Every LLM job has a deterministic fallback, so a model outage never stops a run.
Only request text and offer facts go to the model: never keys, cookies or addresses.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from agent.prompts import explain_prompt, feedback_prompt, parse_prompt
from contracts import Constraints

_LLM_OVERRIDE: Any = None


def set_llm(llm: Any) -> None:
    """Tests inject a fake chat model here; None restores env-based selection."""
    global _LLM_OVERRIDE
    _LLM_OVERRIDE = llm
    _env_llm.cache_clear()


def _v1(url: str) -> str:
    url = url.strip().strip('"').rstrip("/")
    return url if url.endswith("/v1") else url + "/v1"


@lru_cache(maxsize=4)
def _env_llm(provider: str) -> Any:
    from langchain_openai import ChatOpenAI

    if provider == "sie":
        return ChatOpenAI(base_url=_v1(os.environ["SIE_BASE_URL"]), api_key=os.environ["SIE_API_KEY"],
                          model=os.getenv("SIE_CHAT_MODEL", "Qwen/Qwen3.8-27B-FP8"),
                          temperature=0, timeout=45, max_retries=1)
    if provider == "fallback":
        return ChatOpenAI(base_url=os.environ["FALLBACK_LLM_BASE_URL"], api_key=os.environ["FALLBACK_LLM_API_KEY"],
                          model=os.environ["FALLBACK_LLM_MODEL"], temperature=0, timeout=45, max_retries=1)
    return None


def get_llm() -> Any:
    if _LLM_OVERRIDE is not None:
        return _LLM_OVERRIDE
    provider = os.getenv("LLM_PROVIDER", "sie").lower()
    if provider == "none":
        return None
    if provider == "fallback" and fallback_over_budget():
        return None  # over the OpenRouter cap: rules and templates only
    try:
        return _env_llm(provider)
    except KeyError:
        return None


def provider_name() -> str:
    return "override" if _LLM_OVERRIDE is not None else os.getenv("LLM_PROVIDER", "sie").lower()


# ---------------------------------------------------------------- fallback spend cap
# OpenRouter costs real money: stop calling it once FALLBACK_LLM_BUDGET_USD is spent.
# Spend is kept in .mealmatch/fallback_spend.json (gitignored) so it survives restarts.
SPEND_FILE = Path(__file__).resolve().parent.parent / ".mealmatch" / "fallback_spend.json"
# USD per token for anthropic/claude-sonnet-5.5 on OpenRouter (2026-11-20: $2 in / $10 out per 1M)
FALLBACK_PRICE_IN = float(os.getenv("FALLBACK_LLM_PRICE_IN", "0.000002"))
FALLBACK_PRICE_OUT = float(os.getenv("FALLBACK_LLM_PRICE_OUT", "0.00001"))


def fallback_budget() -> float:
    return float(os.getenv("FALLBACK_LLM_BUDGET_USD", "20"))


def fallback_spent() -> float:
    try:
        return float(json.loads(SPEND_FILE.read_text())["usd"])
    except Exception:
        return 0.0


def record_fallback_cost(msg: Any) -> None:
    """Add one OpenRouter call's cost from its token usage."""
    if provider_name() != "fallback":
        return
    usage = getattr(msg, "usage_metadata", None) or {}
    cost = usage.get("input_tokens", 0) * FALLBACK_PRICE_IN + usage.get("output_tokens", 0) * FALLBACK_PRICE_OUT
    SPEND_FILE.parent.mkdir(exist_ok=True)
    SPEND_FILE.write_text(json.dumps({"usd": round(fallback_spent() + cost, 6), "budget": fallback_budget()}))


def fallback_over_budget() -> bool:
    return fallback_spent() >= fallback_budget()


# ---------------------------------------------------------------- parse_request


class ExtractConstraints(BaseModel):
    """Extract the food order constraints stated in the user's request."""

    cuisine: Optional[str] = Field(None, description="Cuisine, e.g. Chinese")
    dish_query: str = Field("", description="Short dish description to search, e.g. 'spicy noodles'")
    people: int = Field(1, description="Number of people to feed")
    budget_total: Optional[float] = Field(None, description="Max total in USD including fees")
    max_eta_min: Optional[int] = Field(None, description="Max delivery time in minutes")
    hard_excludes: list[str] = Field(default_factory=list,
                                     description="Foods the user says they will not eat or are allergic to")
    soft_prefs: list[str] = Field(default_factory=list, description="Things the user prefers, e.g. noodles")


_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_CUISINES = ["chinese", "thai", "indian", "japanese", "korean", "mexican", "italian", "vietnamese", "pizza", "sushi"]


_NOT_FOOD = {"thanks", "thank", "more", "problem", "worries", "way", "mind", "rush", "hurry", "need",
             "longer", "one", "idea", "clue", "matter", "delivery", "fees", "fee", "tip"}


def _is_food_term(term: str) -> bool:
    """Reject captures like 'no thanks' or 'no more spice' -> 'thanks' / 'more spice'."""
    words = term.strip().split()
    return bool(words) and words[0] not in _NOT_FOOD and len(words) <= 3


def rules_parse(text: str) -> Constraints:
    t = text.lower()
    budget = re.search(r"\$\s*(\d+(?:\.\d+)?)", t)
    eta = re.search(r"(\d+)\s*(?:min|minutes)", t)
    people = re.search(r"for\s+(\d+|one|two|three|four|five|six)\b", t)
    n = 1
    if people:
        g = people.group(1)
        n = int(g) if g.isdigit() else _NUM_WORDS[g]
    excludes = re.findall(
        r"(?:\bno\s+|do not like\s+|don't like\s+|dislike\s+|allergic to\s+|without\s+)([a-z][a-z\s-]*?)(?=[.,;!]|\band\b|$)", t)
    prefs = re.findall(r"(?:i prefer|i like|i love)\s+([a-z][a-z\s-]*?)(?=[.,;!]|$)", t)
    cuisine = next((c.title() for c in _CUISINES if c in t), None)
    query_words = [w for w in ("spicy", "noodles", "noodle", "soup", "rice", "dumplings", "curry") if w in t]
    return Constraints(raw_text=text, cuisine=cuisine, dish_query=" ".join(query_words) or (cuisine or ""),
                       people=n, budget_total=float(budget.group(1)) if budget else None,
                       max_eta_min=int(eta.group(1)) if eta else None,
                       hard_excludes=[e.strip() for e in excludes if _is_food_term(e)],
                       soft_prefs=[p.strip() for p in prefs if p.strip()])


def _stated(term: str, text: str) -> bool:
    """A hard rule must come from the user's own words (doc 8.4 warning)."""
    t = text.lower()
    term = term.lower().strip()
    return bool(term) and (term in t or term.rstrip("s") in t)


def _call_tool(llm: Any, messages: Any, tool: type[BaseModel]) -> BaseModel:
    """Structured output through one Pydantic tool. Forced tool_choice first (SIE);
    OpenRouter rejects forcing, so retry with "auto". Raises if no valid call comes back."""
    try:
        msg = llm.bind_tools([tool], tool_choice=tool.__name__).invoke(messages)
    except Exception:
        msg = llm.bind_tools([tool], tool_choice="auto").invoke(messages)
    record_fallback_cost(msg)
    return tool.model_validate(msg.tool_calls[0]["args"])


def extract_constraints(text: str, memory: dict | None = None) -> tuple[Constraints, str]:
    """Returns (constraints, parser) where parser is 'llm' or 'rules'."""
    llm = get_llm()
    if llm is not None:
        mem = memory or {}
        known = {"hard_rules": mem.get("hard_rules", []), "soft_prefs": mem.get("soft_prefs", []),
                 "recent_orders": len(mem.get("orders", []))}
        try:
            got = _call_tool(llm, parse_prompt.invoke({"request": text, "memory": json.dumps(known)}),
                             ExtractConstraints)
            c = Constraints(raw_text=text, **got.model_dump())
            c.hard_excludes = [e for e in c.hard_excludes if _stated(e, text)]
            return c, "llm"
        except Exception:
            pass
    return rules_parse(text), "rules"


# ---------------------------------------------------------------- explain


def summarize_options(constraints: dict, options: list[dict]) -> tuple[str, str]:
    """One short paragraph about the ranked options. Returns (text, writer)."""
    llm = get_llm()
    totals = [o.get("total") for o in options if o.get("total") is not None]
    etas = [o["offer"].get("eta_min") for o in options if o["offer"].get("eta_min") is not None]
    ranked_by = "relevance to the request (reranker)" if options and options[0].get("ranker") == "reranker" \
        else "total price"
    facts = [{"n": i + 1, "restaurant": o["offer"]["restaurant"], "item": o["offer"]["item"],
              "platform": o["offer"]["source"], "total_usd": o.get("total"), "eta_min": o["offer"].get("eta_min"),
              "ranked_by": ranked_by,
              "cheapest_shown": o.get("total") is not None and o.get("total") == min(totals),
              "fastest_shown": bool(etas) and o["offer"].get("eta_min") == min(etas)}
             for i, o in enumerate(options)]
    if llm is not None and facts:
        try:
            msg = llm.invoke(explain_prompt.invoke({"request": constraints.get("raw_text", ""),
                                                    "options": json.dumps(facts, indent=1)}))
            record_fallback_cost(msg)
            text = (msg.content or "").strip() if isinstance(msg.content, str) else ""
            if text:
                return text, "llm"
        except Exception:
            pass
    if not facts:
        return "No option meets all your rules.", "template"
    f = facts[0]
    return (f"Option 1 is {f['item']} from {f['restaurant']} on {f['platform']}: "
            f"${f['total_usd']:.2f} total, " + (f"about {f['eta_min']} min." if f['eta_min'] is not None
                                                 else "delivery time not listed.")), "template"


# ---------------------------------------------------------------- handle_feedback


class FeedbackDecision(BaseModel):
    """What to do after the user turned an offer down."""

    route: Literal["more", "new", "done"] = Field(description="more options, a changed request, or stop")
    reason: str = Field("none given", description="The user's reason in a few words")
    new_hard_rules: list[str] = Field(default_factory=list,
                                      description="Foods the user now says they must never get (allergy, firm never)")
    new_request: str = Field("", description="For route 'new': the changed request in the user's words")


def rules_feedback(text: str) -> dict:
    """Rule-based reader: the fallback when no model is available."""
    t = text.lower().strip()
    if any(w in t for w in ("done", "stop", "cancel", "never mind", "nevermind", "forget it")):
        return {"route": "done", "reason": text.strip(), "new_hard_rules": [], "new_request": "", "reader": "rules"}
    new_rules = [m.strip() for m in re.findall(
        r"(?:allergic to|i can't eat|i cannot eat|never eat|i don't eat|i do not eat)\s+([a-z][a-z\s-]*?)(?=[.,;!]|\band\b|$)", t)]
    new_rules = [r for r in new_rules if _is_food_term(r)]
    if any(w in t for w in ("instead", "actually", "new request", "change", "different cuisine", "something else")) \
            or re.search(r"\$\s*\d+|\d+\s*min", t):
        route = "new"
    else:
        route = "more"
    return {"route": route, "reason": text.strip(), "new_hard_rules": new_rules,
            "new_request": text.strip() if route == "new" else "", "reader": "rules"}


def read_feedback(text: str, shown: list[dict] | None = None) -> dict:
    """Read a rejection or follow-up (doc 7: handle_feedback uses the LLM).
    Returns route, reason, new_hard_rules, new_request and which reader decided."""
    llm = get_llm()
    if llm is not None and text.strip():
        brief = [{"n": i + 1, "item": o["offer"]["item"], "restaurant": o["offer"]["restaurant"],
                  "platform": o["offer"]["source"]} for i, o in enumerate(shown or [])]
        try:
            d = _call_tool(llm, feedback_prompt.invoke({"feedback": text, "shown": json.dumps(brief)}),
                           FeedbackDecision)
            rules = [r.strip() for r in d.new_hard_rules if _stated(r, text) and _is_food_term(r.lower())]
            return {"route": d.route, "reason": d.reason or text.strip(), "new_hard_rules": rules,
                    "new_request": (d.new_request or text).strip() if d.route == "new" else "", "reader": "llm"}
        except Exception:
            pass
    return rules_feedback(text)
