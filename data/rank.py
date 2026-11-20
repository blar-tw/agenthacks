"""filter_rank: remove options that break a rule, rank the rest.

Two rankers: "price" (the baseline) and "reranker" (Qwen3 Reranker 4B on SIE).
The Statsig flag picks one; this module takes the choice as a parameter.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from contracts import Constraints, Match, Offer, RankedOption
from data.sie import Scorer

# A hard rule names a food; these are the words that count as that food.
EXCLUSION_TERMS: dict[str, list[str]] = {
    "mushroom": ["mushroom", "shiitake", "enoki", "portobello", "portabella", "cremini", "oyster mushroom",
                 "king oyster", "wood ear", "morel", "chanterelle", "porcini", "maitake", "truffle"],
    "peanut": ["peanut", "groundnut", "satay"],
    "shellfish": ["shrimp", "prawn", "crab", "lobster", "scallop", "clam", "mussel", "crawfish"],
    "pork": ["pork", "bacon", "ham", "char siu", "lard", "chorizo"],
    "beef": ["beef", "brisket", "steak", "oxtail"],
    "dairy": ["milk", "cheese", "cream", "butter", "parmesan", "yogurt", "paneer"],
    "egg": ["egg"],
    "cilantro": ["cilantro", "coriander"],
}
_PLURAL = re.compile(r"(?:es|s)$")


def _has(word: str, text: str) -> bool:
    return re.search(rf"\b{re.escape(word)}(?:s|es)?\b", text) is not None


def term_of(text: str) -> str:
    """'No mushrooms' / 'I'm allergic to peanuts' / 'dislikes mushrooms' -> 'mushroom' / 'peanut'."""
    t = text.lower()
    for key, words in EXCLUSION_TERMS.items():
        if any(_has(w, t) for w in [key, *words]):
            return key
    last = re.sub(r"[^a-z ]", "", t).split()
    return _PLURAL.sub("", last[-1]) if last else t


def violates(offer: Offer, terms: list[str]) -> str | None:
    hay = f"{offer.item} {offer.description}".lower()
    for term in terms:
        if any(_has(w, hay) for w in EXCLUSION_TERMS.get(term, [term])):
            return term
    return None


def est_total(o: Offer, people: int) -> float:
    return round(o.price * max(people, 1) + (o.fees or 0.0), 2)


def hard_terms(c: Constraints, memory: dict | None) -> list[str]:
    rules = [r for r in (memory or {}).get("rules", []) if r.get("kind", "hard") == "hard"]
    return sorted({term_of(x) for x in c.hard_excludes} | {r.get("term") or term_of(r.get("text", "")) for r in rules})


@dataclass
class FilterResult:
    kept: list[RankedOption]
    removed: list[dict] = field(default_factory=list)   # {offer_id, reason}


def filter_offers(offers: list[Offer], c: Constraints, memory: dict | None, matches: list[Match],
                  rejected: list[str]) -> FilterResult:
    hard = hard_terms(c, memory)
    partner: dict[str, tuple[str, str]] = {}
    for m in matches:
        a, b = m.offer_ids
        partner[a], partner[b] = (b, m.match_id), (a, m.match_id)

    removed: list[dict] = []
    ok: dict[str, Offer] = {}
    for o in offers:
        if o.offer_id in rejected:
            removed.append({"offer_id": o.offer_id, "reason": "rejected by the user"}); continue
        if (t := violates(o, hard)):
            removed.append({"offer_id": o.offer_id, "reason": f"hard rule: no {t}"}); continue
        total = est_total(o, c.people)
        if c.budget_total is not None and total > c.budget_total:
            removed.append({"offer_id": o.offer_id, "reason": f"over budget: ${total:.2f} > ${c.budget_total:.2f}"})
            continue
        if c.max_eta_min is not None and o.eta_min is not None and o.eta_min > c.max_eta_min:
            removed.append({"offer_id": o.offer_id, "reason": f"too slow: {o.eta_min} min > {c.max_eta_min} min"})
            continue
        ok[o.offer_id] = o

    kept: list[RankedOption] = []
    done: set[str] = set()
    for o in ok.values():
        if o.offer_id in done:
            continue
        match_id = None
        if o.offer_id in partner and partner[o.offer_id][0] in ok:
            other, match_id = ok[partner[o.offer_id][0]], partner[o.offer_id][1]
            o = min([o, other], key=lambda x: est_total(x, c.people))   # show the cheaper platform
            done.add(other.offer_id)
        done.add(o.offer_id)
        kept.append(RankedOption(offer=o, score=0.0, ranker="price", match_id=match_id))
    return FilterResult(kept=kept, removed=removed)


def rank_price(options: list[RankedOption], people: int) -> list[RankedOption]:
    out = sorted(options, key=lambda r: (est_total(r.offer, people), r.offer.eta_min or 999))
    return [r.model_copy(update={"score": -est_total(r.offer, people), "ranker": "price"}) for r in out]


def rerank_query(c: Constraints, memory: dict | None) -> str:
    parts = [c.dish_query or c.raw_text or ", ".join(filter(None, [c.cuisine, *c.soft_prefs]))]
    soft = list(c.soft_prefs) + [p.get("text", "") for p in (memory or {}).get("soft_prefs", [])]
    soft = [s for s in dict.fromkeys(soft) if s]
    if soft:
        parts.append("The user prefers: " + "; ".join(soft))
    return " ".join(p for p in parts if p)


def rank_reranker(options: list[RankedOption], c: Constraints, memory: dict | None, scorer: Scorer,
                  bad_restaurants: set[str] | None = None) -> list[RankedOption]:
    scores = scorer.score(rerank_query(c, memory),
                          {r.offer.offer_id: f"{r.offer.item}: {r.offer.description} ({r.offer.restaurant})"
                           for r in options})
    bad = {x.lower() for x in (bad_restaurants or set())}
    for i in (memory or {}).get("rejections", []):
        bad |= {x.lower() for x in i.get("rejected_restaurants", [])}
    liked = {o.get("restaurant", "").lower() for o in (memory or {}).get("orders", []) if (o.get("rating") or 0) >= 4}
    out = []
    for r in options:
        name = r.offer.restaurant.lower()
        s = scores.get(r.offer.offer_id, 0.0)
        s += 0.05 if name in liked else 0.0
        s -= 0.15 if name in bad else 0.0
        s -= 0.02 if r.offer.eta_min is None else 0.0
        out.append(r.model_copy(update={"score": round(s, 4), "ranker": "reranker"}))
    return sorted(out, key=lambda r: r.score, reverse=True)
