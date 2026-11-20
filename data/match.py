"""match_offers: pair the same dish on both platforms.

1. Match the restaurant first, by name and address.
2. Compare the dish vectors inside that restaurant (SIE), or fuzzy names (cut-list fallback).
3. Check the price ratio and the modifiers (size).
Similar meaning does not prove two dishes are the same, so every match carries a confidence.
"""
from __future__ import annotations

import re
from collections import defaultdict

from rapidfuzz import fuzz

from contracts import Match, Offer
from data.sie import Embedder, cosine

_SYN = {"szechuan": "sichuan", "szechwan": "sichuan", "w/": "with ", "&": " and ", "—": " ", "-": " "}
_STOP = {"restaurant", "kitchen", "the", "cafe", "inc", "llc", "sf"}
_SIZE = {"small", "medium", "large", "regular", "family", "half", "full", "vegetarian", "vegan"}
# Main ingredients: two dish names that each name one, with none in common, are different dishes
# ("Shrimp Chow Mein" vs "Beef Chow Mein" scores 0.785 on vectors alone).
_MAIN = {"beef", "pork", "chicken", "shrimp", "prawn", "fish", "lamb", "duck", "tofu", "crab", "squid", "scallop",
         "clam", "frog", "intestine", "eggplant", "lobster", "oyster", "cod", "salmon", "tripe", "mutton", "gizzard"}

# Calibrated on the 2026-11-20 real fixtures (5 stores x 2 platforms) with tencent/R3-embedding-0.6b
# on dish names: >= 0.86 were the same dish; 0.80-0.83 held "Sichuan Style Spicy Chicken" vs
# "Chongqing Style Spicy Chicken"; 0.83-0.86 held true pairs such as the onion pancake and frog.
RESTAURANT_MIN = 0.85
VECTOR_MIN = 0.83
FUZZY_MIN = 0.80          # token_sort on names, for the no-embeddings fallback
MAX_PRICE_RATIO = 1.5


def norm(s: str) -> str:
    s = re.sub(r"\([^)]*\)", " ", s.lower())    # "(1559 Mission St)", "(sf)"
    for a, b in _SYN.items():
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(w for w in s.split() if w not in _STOP)


def _street_no(addr: str | None) -> str | None:
    m = re.match(r"\s*(\d+)", addr or "")
    return m.group(1) if m else None


def restaurant_similarity(a: Offer, b: Offer) -> float:
    # token_sort, not token_set: a subset scores 100 under token_set, so "Z & Y Restaurant"
    # (-> "z and y") would equal "Z & Y Peking Duck".
    name = fuzz.token_sort_ratio(norm(a.restaurant), norm(b.restaurant)) / 100
    na, nb = _street_no(a.restaurant_address), _street_no(b.restaurant_address)
    if na and nb and na != nb:
        return 0.0          # same name, different branch
    return name


def _sizes(o: Offer) -> set[str]:
    text = f" {norm(o.item)} "
    return {w for w in _SIZE if f" {w} " in text}


def _main(o: Offer) -> set[str]:
    return {w.rstrip("s") for w in norm(o.item).split() if w.rstrip("s") in _MAIN or w in _MAIN}


def main_ingredient_conflict(a: Offer, b: Offer) -> bool:
    ma, mb = _main(a), _main(b)
    return bool(ma and mb and not (ma & mb))


def _confidence(sim: float, a: Offer, b: Offer) -> float:
    ratio = max(a.price, b.price) / max(min(a.price, b.price), 0.01)
    if ratio > MAX_PRICE_RATIO:
        return 0.0
    conf = sim * (1 - 0.3 * (ratio - 1))   # 10% apart costs 3 points
    if _sizes(a) ^ _sizes(b):
        conf *= 0.8
    return max(0.0, min(1.0, conf))


def dish_text(o: Offer) -> str:
    """The text embedded for matching: the dish name only. Descriptions differ between
    platforms and pulled identical dishes apart ("Ma-Po Tofu w.Ground Pork": 0.737 with them)."""
    return o.item


def match_offers(offers: list[Offer], embedder: Embedder | None = None,
                 vectors: dict[str, list[float]] | None = None) -> tuple[list[Match], str]:
    """Return (matches, method). method is "vector", or "fuzzy" when no embedder is given.
    Match.offer_ids is [ubereats offer_id, doordash offer_id]."""
    ue = [o for o in offers if o.source == "ubereats"]
    dd = [o for o in offers if o.source == "doordash"]
    method = "vector" if embedder is not None else "fuzzy"
    if not ue or not dd:
        return [], method

    by_store: dict[str, list[Offer]] = defaultdict(list)
    for o in ue + dd:
        by_store[f"{o.source}|{o.restaurant}|{o.restaurant_address}"].append(o)
    ue_stores = [k for k in by_store if k.startswith("ubereats|")]
    dd_stores = [k for k in by_store if k.startswith("doordash|")]

    vecs = vectors if vectors is not None else {}
    if embedder is not None and not vecs:
        both = ue + dd
        vecs.update(zip([o.offer_id for o in both], embedder.embed([dish_text(o) for o in both])))

    matches: list[Match] = []
    for uk in ue_stores:
        best_dk, best = None, 0.0
        for dk in dd_stores:
            s = restaurant_similarity(by_store[uk][0], by_store[dk][0])
            if s > best:
                best_dk, best = dk, s
        if best_dk is None or best < RESTAURANT_MIN:
            continue
        pairs = []
        for a in by_store[uk]:
            for b in by_store[best_dk]:
                if main_ingredient_conflict(a, b):
                    continue
                if method == "vector":
                    sim, floor = cosine(vecs[a.offer_id], vecs[b.offer_id]), VECTOR_MIN
                else:
                    sim, floor = fuzz.token_sort_ratio(norm(a.item), norm(b.item)) / 100, FUZZY_MIN
                if sim >= floor:
                    pairs.append((sim, a, b))
        used: set[str] = set()
        for sim, a, b in sorted(pairs, key=lambda p: p[0], reverse=True):   # greedy one-to-one
            if a.offer_id in used or b.offer_id in used:
                continue
            conf = _confidence(sim, a, b) * best
            if conf < 0.5:
                continue
            used |= {a.offer_id, b.offer_id}
            matches.append(Match(offer_ids=[a.offer_id, b.offer_id], confidence=round(conf, 3),
                                 evidence_ids=[e for e in (a.evidence_id, b.evidence_id) if e]))
    return matches, method
