"""The distiller: repeated episodes -> learned soft preferences (Brief 8.4).

Counting distiller: counts repeated dish tags, cuisines and rejection reasons. It is the
14:30 fallback and also builds the preference -> episode-ID map in both modes.
Cognee distiller: sends the episodes, with their IDs in the text, to Cognee remember,
then runs improve. load_memory recalls from Cognee.

The distiller never writes a hard rule.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime, timezone

from contracts import Interaction, OrderRecord, Preference
from data import config
from data.store import Store

MIN_EPISODES = 3            # trigger: new episodes since users.last_distilled_at
MIN_EVIDENCE = 3            # episodes behind each preference
MIN_ORDERS = 3              # new user: no run below 3 orders

DISH_TAGS: dict[str, list[str]] = {
    "spicy": ["spicy", "mala", "chili", "chilli", "hot", "sichuan", "szechuan", "chongqing", "dan dan", "hunan", "numbing"],
    "noodles": ["noodle", "lo mein", "chow mein", "ramen", "udon", "pho", "dan dan"],
    "dumplings": ["dumpling", "wonton", "bao", "gyoza", "xiao long"],
    "soup": ["soup", "broth", "pho", "ramen"],
    "rice": ["fried rice", "rice bowl", "congee", "biryani"],
    "tofu": ["tofu"],
}
REASON_TAGS: dict[str, list[str]] = {
    "oily food": ["oily", "greasy"],
    "slow delivery": ["late", "slow", "took forever"],
    "expensive options": ["expensive", "pricey", "too much"],
    "bland food": ["bland"],
    "salty food": ["salty"],
}


def _tags(text: str, table: dict[str, list[str]]) -> set[str]:
    t = text.lower()
    return {tag for tag, words in table.items() if any(re.search(rf"\b{re.escape(w)}", t) for w in words)}


def _pid(user_id: str, text: str) -> str:
    return f"pref-{user_id}-" + re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def episodes_since(store: Store, user_id: str) -> tuple[list[OrderRecord], list[Interaction]]:
    since = store.last_distilled_at(user_id)
    return store.orders_since(user_id, since), store.rejections_since(user_id, since)


def should_distill(store: Store, user_id: str) -> bool:
    orders, rejections = episodes_since(store, user_id)
    total_orders = len(store.orders_since(user_id, None))
    return total_orders >= MIN_ORDERS and len(orders) + len(rejections) >= MIN_EPISODES


def count_preferences(user_id: str, orders: list[OrderRecord], rejections: list[Interaction]) -> list[Preference]:
    """Soft preferences with >= MIN_EVIDENCE episodes each. Never hard."""
    by_tag: dict[str, list[str]] = defaultdict(list)
    by_cuisine: dict[str, list[str]] = defaultdict(list)
    for o in orders:
        tags = _tags(" ".join(o.items), DISH_TAGS)
        for t in tags:
            by_tag[t].append(o.id)
        if "spicy" in tags:
            for t in tags - {"spicy"}:
                by_tag[f"spicy {t}"].append(o.id)
        if o.cuisine:
            by_cuisine[o.cuisine.lower()].append(o.id)

    n = max(len(orders), 1)
    prefs: list[Preference] = []
    covered: set[str] = set()
    for tag in sorted(by_tag, key=lambda t: (-len(by_tag[t]), -len(t))):     # pairs first on ties
        ids = by_tag[tag]
        if len(ids) < MIN_EVIDENCE or tag in covered:
            continue
        prefs.append(_learned(user_id, f"likes {tag}", ids, len(ids) / n))
        covered |= set(tag.split())
    for cuisine, ids in by_cuisine.items():
        if len(ids) >= MIN_EVIDENCE:
            prefs.append(_learned(user_id, f"often orders {cuisine} food", ids, len(ids) / n))

    by_reason: dict[str, list[str]] = defaultdict(list)
    for i in rejections:
        for t in _tags(i.reason or "", REASON_TAGS):
            by_reason[t].append(i.id)
    for tag, ids in by_reason.items():
        if len(ids) >= MIN_EVIDENCE:
            prefs.append(_learned(user_id, f"dislikes {tag}", ids, min(0.9, len(ids) / max(len(rejections), 1))))
    return prefs


def _learned(user_id: str, text: str, episode_ids: list[str], conf: float) -> Preference:
    return Preference(id=_pid(user_id, text), user_id=user_id, text=text, kind="soft", source="learned",
                      confidence=round(min(conf, 0.95), 2), episode_ids=sorted(set(episode_ids)),
                      created_at=datetime.now(timezone.utc))


def episode_text(e: OrderRecord | Interaction) -> str:
    """Episode IDs go in the text: Cognee does not document custom metadata."""
    if isinstance(e, OrderRecord):
        return (f"[episode {e.id}] On {e.ordered_at:%Y-%m-%d} the user ordered {', '.join(e.items)} from "
                f"{e.restaurant} on {e.platform}. Cuisine: {e.cuisine or 'unknown'}. Rating: {e.rating or 'none'}.")
    return (f"[episode {e.id}] On {e.at:%Y-%m-%d} the user rejected {', '.join(e.rejected_restaurants) or 'an option'}. "
            f"Reason: {e.reason or 'none given'}.")


# ---------------------------------------------------------------- Cognee

def configure_cognee(use_sie: bool = True) -> str:
    """Set Cognee's LLM and embedding env before cognee is imported. Returns the mode used."""
    if use_sie and config.env("SIE_API_KEY"):
        base = config.SIE_BASE_URL + "/v1"
        os.environ.update({
            "LLM_PROVIDER": "custom", "LLM_ENDPOINT": base, "LLM_API_KEY": config.env("SIE_API_KEY"),
            "LLM_MODEL": f"openai/{config.SIE_COGNEE_MODEL}", "LLM_TEMPERATURE": "0",
            "EMBEDDING_PROVIDER": "openai_compatible", "EMBEDDING_ENDPOINT": base,
            "EMBEDDING_API_KEY": config.env("SIE_API_KEY"), "EMBEDDING_MODEL": f"openai/{config.SIE_EMBED_MODEL}",
            "EMBEDDING_DIMENSIONS": str(config.EMBED_DIM),
        })
        return "sie"
    for k in ("LLM_PROVIDER", "LLM_ENDPOINT", "LLM_MODEL", "EMBEDDING_PROVIDER", "EMBEDDING_ENDPOINT",
              "EMBEDDING_MODEL", "EMBEDDING_DIMENSIONS", "EMBEDDING_API_KEY"):
        os.environ.pop(k, None)
    os.environ["LLM_API_KEY"] = config.env("OPENAI_API_KEY")
    return "openai"


async def _cognee():
    import cognee  # optional dependency: uv sync --extra memory
    if config.env("COGNEE_API_KEY"):
        await cognee.serve(url=config.env("COGNEE_SERVICE_URL") or None, api_key=config.env("COGNEE_API_KEY"))
    return cognee


async def cognee_remember(texts: list[str]) -> None:
    cognee = await _cognee()
    for t in texts:
        await cognee.remember(t)
    await cognee.improve()


async def cognee_recall(query: str) -> list[str]:
    cognee = await _cognee()
    res = await cognee.recall(query)
    items = res if isinstance(res, list) else [res]
    return [str(getattr(r, "text", None) or r) for r in items if r]


EPISODE_REF = re.compile(r"\b((?:ord|int)-[A-Za-z0-9-]+)")
