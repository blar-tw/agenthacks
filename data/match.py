"""match_offers: pair the same dish on both platforms.

1. Match the restaurant first, by name and address.
2. Compare the dish vectors inside that restaurant (SIE), or fuzzy names (cut-list fallback).
3. Check the price ratio and the modifiers (size, spice level).
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
_SIZE = {"small", "medium", "large", "regular", "family", "half", "full"}
_SPICE = {"mild", "medium", "hot", "extra hot", "spicy", "numbing"}

RESTAURANT_MIN = 0.85
VECTOR_MIN = 0.80
FUZZY_MIN = 0.70
MAX_PRICE_RATIO = 1.5


def norm(s: str) -> str:
    s = s.lower()
    for a, b in _SYN.items():
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(w for w in s.split() if w not in _STOP)


def _street_no(addr: str | None) -> str | None:
    m = re.match(r"\s*(\d+)", addr or "")
    return m.group(1) if m else None


def restaurant_similarity(a: Offer, b: Offer) -> float:
    name = fuzz.token_set_ratio(norm(a.restaurant), norm(b.restaurant)) / 100
    na, nb = _street_no(a.address), _street_no(b.address)
    if na and nb and na != nb:
        return 0.0          # same name, different branch
    return name


def _modifiers(o: Offer, vocab: set[str]) -> set[str]:
    text = f" {norm(o.item)} "
    return {w for w in vocab if f" {w} " in text}


def _confidence(sim: float, a: Offer, b: Offer) -> float:
    ratio = max(a.price, b.price) / max(min(a.price, b.price), 0.01)
    if ratio > MAX_PRICE_RATIO:
        return 0.0
    conf = sim * (1 - 0.3 * (ratio - 1))   # 10% apart costs 3 points
    if _modifiers(a, _SIZE) ^ _modifiers(b, _SIZE):
        conf *= 0.8
    return max(0.0, min(1.0, conf))


def dish_text(o: Offer) -> str:
    return f"{o.item}. {o.description}".strip(". ")


def match_offers(offers: list[Offer], embedder: Embedder | None = None,
                 vectors: dict[str, list[float]] | None = None) -> tuple[list[Match], str]:
    """Return (matches, method). method is "vector", or "fuzzy" when no embedder is given."""
    ue = [o for o in offers if o.platform == "ubereats"]
    dd = [o for o in offers if o.platform == "doordash"]
    if not ue or not dd:
        return [], "vector" if embedder else "fuzzy"

    by_store: dict[str, list[Offer]] = defaultdict(list)
    for o in ue + dd:
        by_store[f"{o.platform}|{o.restaurant}|{o.address or ''}"].append(o)
    ue_stores = [k for k in by_store if k.startswith("ubereats|")]
    dd_stores = [k for k in by_store if k.startswith("doordash|")]

    method = "vector" if embedder is not None else "fuzzy"
    vecs = vectors if vectors is not None else {}
    if embedder is not None and not vectors:
        todo = ue + dd
        for o, v in zip(todo, embedder.embed([dish_text(o) for o in todo])):
            vecs[o.id] = v

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
                if method == "vector":
                    sim = cosine(vecs[a.id], vecs[b.id])
                    floor = VECTOR_MIN
                else:
                    sim = fuzz.token_set_ratio(norm(a.item), norm(b.item)) / 100
                    floor = FUZZY_MIN
                if sim >= floor:
                    pairs.append((sim, a, b))
        used: set[str] = set()
        for sim, a, b in sorted(pairs, key=lambda p: p[0], reverse=True):   # greedy one-to-one
            if a.id in used or b.id in used:
                continue
            conf = _confidence(sim, a, b) * best
            if conf < 0.5:
                continue
            used |= {a.id, b.id}
            matches.append(Match(id=f"m-{a.id}-{b.id}", ubereats_offer_id=a.id, doordash_offer_id=b.id,
                                 confidence=round(conf, 3), method=method,
                                 evidence_ids=[e for e in (a.evidence_id, b.evidence_id) if e]))
    return matches, method
