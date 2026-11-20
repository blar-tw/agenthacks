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
from typing import Any, Optional

from pydantic import BaseModel, Field

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
    try:
        return _env_llm(provider)
    except KeyError:
        return None


def provider_name() -> str:
    return "override" if _LLM_OVERRIDE is not None else os.getenv("LLM_PROVIDER", "sie").lower()


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
                       hard_excludes=[e.strip() for e in excludes if e.strip()],
                       soft_prefs=[p.strip() for p in prefs if p.strip()])


def _stated(term: str, text: str) -> bool:
    """A hard rule must come from the user's own words (doc 8.4 warning)."""
    t = text.lower()
    term = term.lower().strip()
    return bool(term) and (term in t or term.rstrip("s") in t)


def extract_constraints(text: str) -> tuple[Constraints, str]:
    """Returns (constraints, parser) where parser is 'llm' or 'rules'."""
    llm = get_llm()
    if llm is not None:
        prompt = [("system", "Extract the user's food order constraints. Call the tool exactly once."),
                  ("user", text)]
        try:
            try:
                msg = llm.bind_tools([ExtractConstraints], tool_choice="ExtractConstraints").invoke(prompt)
            except Exception:  # OpenRouter rejects a forced tool_choice; "auto" works there
                msg = llm.bind_tools([ExtractConstraints], tool_choice="auto").invoke(prompt)
            args = msg.tool_calls[0]["args"]
            got = ExtractConstraints.model_validate(args)
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
    facts = [{"n": i + 1, "restaurant": o["offer"]["restaurant"], "item": o["offer"]["item"],
              "platform": o["offer"]["source"], "total": o.get("total"), "eta_min": o["offer"].get("eta_min")}
             for i, o in enumerate(options)]
    if llm is not None and facts:
        try:
            msg = llm.invoke([
                ("system", "You help a user choose food delivery. In at most 2 short sentences, say why option 1 "
                           "is recommended, using only the facts given. Do not invent facts."),
                ("user", json.dumps({"request": constraints.get("raw_text", ""), "options": facts}))])
            text = (msg.content or "").strip()
            if text:
                return text, "llm"
        except Exception:
            pass
    if not facts:
        return "No option meets all your rules.", "template"
    f = facts[0]
    return (f"Option 1 is {f['item']} from {f['restaurant']} on {f['platform']}: "
            f"${f['total']:.2f} total, about {f['eta_min']} min."), "template"


# ---------------------------------------------------------------- handle_feedback


def read_feedback(text: str) -> dict:
    """Classify a rejection or follow-up. Rule-based: fast and auditable."""
    t = text.lower().strip()
    new_rules = [m.strip() for m in re.findall(r"(?:allergic to|i can't eat|i cannot eat|never)\s+([a-z][a-z\s-]*?)(?=[.,;!]|$)", t)]
    if any(w in t for w in ("done", "stop", "cancel", "never mind", "nevermind", "forget it")):
        route = "done"
    elif any(w in t for w in ("instead", "actually", "new request", "change", "different cuisine", "something else")) \
            or re.search(r"\$\s*\d+|\d+\s*min", t):
        route = "new"
    else:
        route = "more"
    return {"route": route, "reason": text.strip(), "new_hard_rules": new_rules}
