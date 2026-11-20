"""filter_rank: remove options that break a rule, rank the rest.

Two rankers: "price" (the baseline) and "reranker" (Qwen3 Reranker 4B on SIE).
The Statsig flag picks one; this module takes the choice as a parameter.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from contracts import Constraints, Match, MemoryContext, Offer, RankedOption, RankerName
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


def term_of(text: str) -> str:
    """'No mushrooms' / 'I'm allergic to peanuts' / 'dislikes mushrooms' -> 'mushroom' / 'peanut'."""
    t = text.lower()
    for key, words in EXCLUSION_TERMS.items():
        if any(re.search(rf"\b{re.escape(w)}(?:s|es)?\b", t) for w in [key, *words]):
            return key
    last = re.sub(r"[^a-z ]", "", t).split()
    return _PLURAL.sub("", last[-1]) if last else t


def violates(offer: Offer, terms: list[str]) -> str | None:
    hay = f"{offer.item} {offer.description}".lower()
    for term in terms:
        for w in EXCLUSION_TERMS.get(term, [term]):
            if re.search(rf"\b{re.escape(w)}(?:s|es)?\b", hay):
                return term
    return None


def est_total(o: Offer, party_size: int) -> float:
    return round(o.price * max(party_size, 1) + (o.fees or 0.0), 2)


@dataclass
class FilterResult:
    kept: list[RankedOption]
    removed: list[dict] = field(default_factory=list)   # {offer_id, reason}


def filter_offers(offers: list[Offer], c: Constraints, memory: MemoryContext | None, matches: list[Match],
                  rejected: list[str]) -> FilterResult:
    hard = [term_of(x) for x in c.exclusions] + [p.term or term_of(p.text) for p in (memory.hard_rules if memory else [])]
    hard = sorted(set(hard))
    by_id = {o.id: o for o in offers}
    partner: dict[str, tuple[str, str]] = {}
    for m in matches:
        partner[m.ubereats_offer_id] = (m.doordash_offer_id, m.id)
        partner[m.doordash_offer_id] = (m.ubereats_offer_id, m.id)

    removed: list[dict] = []
    ok: dict[str, Offer] = {}
    for o in offers:
        if o.id in rejected:
            removed.append({"offer_id": o.id, "reason": "rejected by the user"}); continue
        if (t := violates(o, hard)):
            removed.append({"offer_id": o.id, "reason": f"hard rule: no {t}"}); continue
        total = est_total(o, c.party_size)
        if c.budget is not None and total > c.budget:
            removed.append({"offer_id": o.id, "reason": f"over budget: ${total:.2f} > ${c.budget:.2f}"}); continue
        if c.max_eta_min is not None and o.eta_min is not None and o.eta_min > c.max_eta_min:
            removed.append({"offer_id": o.id, "reason": f"too slow: {o.eta_min} min > {c.max_eta_min} min"}); continue
        ok[o.id] = o

    kept: list[RankedOption] = []
    done: set[str] = set()
    for o in ok.values():
        if o.id in done:
            continue
        alt, match_id = None, None
        if o.id in partner and partner[o.id][0] in ok:
            other = ok[partner[o.id][0]]
            match_id = partner[o.id][1]
            # show the cheaper platform first; known fees beat unknown ones on a tie
            a, b = sorted([o, other], key=lambda x: (est_total(x, c.party_size), x.fees is None))
            o, alt = a, b
            done.add(alt.id)
        done.add(o.id)
        ev = [e for e in (o.evidence_id, alt.evidence_id if alt else None) if e]
        kept.append(RankedOption(offer=o, score=0.0, ranker="price", est_total=est_total(o, c.party_size),
                                 fees_known=o.fees is not None, eta_known=o.eta_min is not None,
                                 match_id=match_id, alt_offer=alt, evidence_ids=ev))
    return FilterResult(kept=kept, removed=removed)


def rank_price(options: list[RankedOption]) -> list[RankedOption]:
    out = sorted(options, key=lambda r: (r.est_total, r.offer.eta_min if r.offer.eta_min is not None else 999))
    return [r.model_copy(update={"score": -r.est_total, "ranker": "price"}) for r in out]


def rerank_query(c: Constraints, memory: MemoryContext | None) -> str:
    parts = [c.query or ", ".join(filter(None, [c.cuisine, *c.wants]))]
    soft = list(c.wants) + [p.text for p in (memory.soft_prefs if memory else [])]
    if soft:
        parts.append("The user prefers: " + "; ".join(dict.fromkeys(soft)))
    return " ".join(p for p in parts if p)


def rank_reranker(options: list[RankedOption], c: Constraints, memory: MemoryContext | None, scorer: Scorer,
                  rejected_restaurants: list[str] | None = None) -> list[RankedOption]:
    query = rerank_query(c, memory)
    texts = {r.offer.id: f"{r.offer.item}: {r.offer.description} ({r.offer.restaurant})" for r in options}
    scores = scorer.score(query, texts)
    bad = {x.lower() for x in (rejected_restaurants or [])}
    for i in (memory.recent_rejections if memory else []):
        bad |= {x.lower() for x in i.rejected_restaurants}
    liked = {o.restaurant.lower() for o in (memory.recent_orders if memory else []) if (o.rating or 0) >= 4}
    out = []
    for r in options:
        s = scores.get(r.offer.id, 0.0)
        name = r.offer.restaurant.lower()
        s += 0.05 if name in liked else 0.0
        s -= 0.15 if name in bad else 0.0
        s -= 0.02 if not r.eta_known else 0.0
        out.append(r.model_copy(update={"score": round(s, 4), "ranker": "reranker"}))
    return sorted(out, key=lambda r: r.score, reverse=True)


def rank(options: list[RankedOption], ranker: RankerName, c: Constraints, memory: MemoryContext | None,
         scorer: Scorer | None, rejected_restaurants: list[str] | None = None) -> list[RankedOption]:
    if ranker == "reranker" and scorer is not None:
        return rank_reranker(options, c, memory, scorer, rejected_restaurants)
    return rank_price(options)
