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

# Relevance gate: a request word matches a dish if the dish text has the word or one of these.
# Search results hold everything near the user (a salad bar for "spicy Chinese"), and the price
# ranker alone would pick the cheapest thing on the page.
RELEVANCE_TERMS: dict[str, list[str]] = {
    "spicy": ["spicy", "chili", "chilli", "chile", "mala", "hot", "sichuan", "szechuan", "chongqing", "numbing",
              "peppercorn", "jalapeno", "sriracha", "gochujang", "vindaloo", "kung pao", "dan dan"],
    "noodle": ["noodle", "lo mein", "chow mein", "ramen", "udon", "soba", "pho", "pad thai", "dan dan", "mein",
               "vermicelli", "rice stick", "pasta", "spaghetti"],
    "chinese": ["chinese", "sichuan", "szechuan", "chongqing", "hunan", "cantonese", "mapo", "kung pao",
                "general tso", "lo mein", "chow mein", "dan dan", "wonton", "dumpling", "bok choy", "fried rice",
                "dim sum", "char siu", "mala", "wok"],
    "rice": ["rice", "biryani", "risotto", "bibimbap", "congee"],
    "soup": ["soup", "broth", "pho", "ramen", "stew"],
}
_STOP = {"food", "dish", "dishes", "meal", "something", "some", "for", "me", "the", "a", "an", "and", "with",
         "option", "options", "cheap", "cheapest", "good", "best", "order", "get", "want", "like", "style", "of"}


def _singular(w: str) -> str:
    if w in RELEVANCE_TERMS or not w.endswith("s") or w.endswith("ss"):
        return w
    if w[:-1] in RELEVANCE_TERMS or not w.endswith("oes"):
        return w[:-1]
    return w[:-2]   # tomatoes -> tomato


_CUISINES = {"chinese", "thai", "indian", "mexican", "italian", "japanese", "korean", "vietnamese", "american",
             "french", "greek", "mediterranean", "sichuan", "szechuan", "cantonese", "hunan"}


# Real menus list drinks, plain rice and condiments next to meals; the cheapest "spicy" item on a
# real menu was "House Hot Mustard 2oz". These are never the answer unless the request names them.
_ADDON = re.compile(r"\b\d+\s?oz\b|^side\b|\bside of\b|\badd[- ]?on\b|^extra\b", re.I)
_DISH_LINK = re.compile(r"\b(?:in|with|wit|and|w)\b|w/|w\.", re.I)   # "Eggplant in Garlic Sauce" is a dish
_DRINK_OR_PLAIN = re.compile(r"^(?:diet |house )?(?:coke|coca[- ]?cola|sprite|fanta|7[- ]?up|pepsi|soda|bottled water|water"
                             r"|.*\btea|.*\bjuice|.*\bmilkshake|.*\blemonade|soy milk|(?:steamed )?(?:white |brown |jasmine )*rice)$",
                             re.I)


def is_side(offer: Offer) -> bool:
    """A drink, plain rice, sauce or add-on rather than a dish (names are matched without the Chinese text)."""
    name = re.sub(r"\([^)]*\)|[^\x00-\x7f]", " ", offer.item)
    name = re.sub(r"\s+", " ", name).strip()
    just_sauce = bool(re.search(r"\b(?:sauce|dressing)$", name, re.I)) and not _DISH_LINK.search(name)
    return bool(_ADDON.search(name) or _DRINK_OR_PLAIN.match(name) or just_sauce)


def _words(text: str) -> list[str]:
    return sorted({_singular(w) for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOP and len(w) > 2})


def request_words(c: Constraints) -> tuple[list[str], bool]:
    """(words, dish_only). Dish words match the dish itself; with none, the cuisine may match the restaurant."""
    dish = _words(c.dish_query)
    specific = [w for w in dish if w not in _CUISINES]
    dish = specific or dish   # "spicy Chinese food": "spicy" says more than "chinese"
    return (dish, True) if dish else (_words(c.cuisine or ""), False)


def relevant(offer: Offer, words: list[str], dish_only: bool = True) -> bool:
    hay = f"{offer.item} {offer.description}" + ("" if dish_only else f" {offer.restaurant}")
    return any(_has(t, hay.lower()) for w in words for t in RELEVANCE_TERMS.get(w, [w]))


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


def evidence_gaps(o: Offer) -> int:
    """How many facts the platform did not give us: fee, ETA, store address."""
    from data.normalize import UNKNOWN_FEES
    return (o.offer_id in UNKNOWN_FEES) + (o.eta_min is None) + (not o.restaurant_address)


def est_total(o: Offer, people: int) -> float:
    return round(o.price * max(people, 1) + (o.fees or 0.0), 2)


def _text(p) -> str:
    return p if isinstance(p, str) else (p or {}).get("text", "")


def hard_terms(c: Constraints, memory: dict | None) -> list[str]:
    """Hard rules from the request plus memory: P1's hard_rules (text) and P2's rules (with term)."""
    mem = memory or {}
    rules = [r for r in mem.get("rules", []) if r.get("kind", "hard") == "hard"]
    terms = {term_of(x) for x in c.hard_excludes} | {term_of(_text(x)) for x in mem.get("hard_rules", [])}
    terms |= {r.get("term") or term_of(r.get("text", "")) for r in rules}
    return sorted(t for t in terms if t)


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
    # Turning down a dish turns down the same dish on the other platform too.
    rejected = list(dict.fromkeys([*rejected, *(partner[r][0] for r in rejected if r in partner)]))

    removed: list[dict] = []
    ok: dict[str, Offer] = {}
    allowed: list[Offer] = []
    for o in offers:
        if o.offer_id in rejected:
            removed.append({"offer_id": o.offer_id, "reason": "rejected by the user"}); continue
        if (t := violates(o, hard)):
            removed.append({"offer_id": o.offer_id, "reason": f"hard rule: no {t}"}); continue
        allowed.append(o)

    words, dish_only = request_words(c)
    on_topic = [o for o in allowed if relevant(o, words, dish_only)] if words else allowed
    if on_topic:   # if no dish matches the request words, the gate is off and the ranker decides
        keep = {o.offer_id for o in on_topic}
        removed += [{"offer_id": o.offer_id, "reason": f"not what was asked for ({', '.join(words)})"}
                    for o in allowed if o.offer_id not in keep]
        allowed = on_topic

    asked = set(_words(c.dish_query))
    meals = [o for o in allowed if not is_side(o) or asked & set(_words(o.item))]
    if meals:   # never empty the list just because everything left is a side
        keep = {o.offer_id for o in meals}
        removed += [{"offer_id": o.offer_id, "reason": "side, drink or add-on"} for o in allowed if o.offer_id not in keep]
        allowed = meals

    for o in allowed:
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
            # Show the cheaper platform. On a tie (96 of 109 pairs on the real menus), show the offer
            # with more verified facts, not whichever came first: min() used to keep Uber Eats every time.
            o = min([o, other], key=lambda x: (est_total(x, c.people), evidence_gaps(x)))
            done.add(other.offer_id)
        done.add(o.offer_id)
        kept.append(RankedOption(offer=o, score=0.0, ranker="price", match_id=match_id))
    return FilterResult(kept=kept, removed=removed)


def rank_price(options: list[RankedOption], people: int) -> list[RankedOption]:
    out = sorted(options, key=lambda r: (est_total(r.offer, people), r.offer.eta_min or 999))
    return [r.model_copy(update={"score": -est_total(r.offer, people), "ranker": "price"}) for r in out]


def rerank_query(c: Constraints, memory: dict | None) -> str:
    parts = [c.dish_query or c.raw_text or ", ".join(filter(None, [c.cuisine, *c.soft_prefs]))]
    soft = list(c.soft_prefs) + [_text(p) for p in (memory or {}).get("soft_prefs", [])]
    soft = [s for s in dict.fromkeys(soft) if s]
    if soft:
        parts.append("The user prefers: " + "; ".join(soft))
    return " ".join(p for p in parts if p)


def rank_reranker(options: list[RankedOption], c: Constraints, memory: dict | None, scorer: Scorer,
                  bad_restaurants: set[str] | None = None, max_wait_s: float = 90.0) -> list[RankedOption]:
    items = {r.offer.offer_id: f"{r.offer.item}: {r.offer.description} ({r.offer.restaurant})" for r in options}
    scores = scorer.score(rerank_query(c, memory), items, max_wait_s=max_wait_s)
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
