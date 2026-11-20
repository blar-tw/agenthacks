"""Default versions of nodes owned by P2 (data/memory) and P3 (audit_report).

The graph uses these until data/nodes.py or sim_ui/nodes.py define a function
with the same name; then the real one replaces the default automatically.
Each default is simple but honest: it writes the same audit events the real
node must write, so the audit trail works end to end from the first merge.
"""

from __future__ import annotations

import concurrent.futures as cf
import difflib
import os

from contracts import (AgentState, Constraints, Match, Offer, RankedOption, audit, get_tools,
                       read_orders, read_preferences, write_interaction, write_order, write_price_snapshot)

SCRAPE_TIMEOUT_S = float(os.getenv("SCRAPE_TIMEOUT_S", "90"))


def tool_mode() -> str:
    return os.getenv("TOOL_MODE", "fixtures")


def load_memory(state: AgentState) -> dict:
    uid = state.get("user_id", "demo")
    orders = read_orders(uid, 5)
    prefs = read_preferences(uid)
    hard = [p["text"] for p in prefs if p.get("kind") == "hard"]
    soft = [p["text"] for p in prefs if p.get("kind") == "soft"]
    eid = audit("evidence", {"orders": len(orders), "hard_rules": hard, "soft_prefs": soft},
                source_url="store://memory")
    return {"memory": {"orders": orders, "hard_rules": hard, "soft_prefs": soft, "evidence_id": eid}}


def _scrape(state: AgentState, platform: str) -> dict:
    c = Constraints(**state.get("constraints", {}))
    tools = get_tools(tool_mode())
    pool = cf.ThreadPoolExecutor(max_workers=1)
    try:
        offers = pool.submit(tools.search, c, platform).result(timeout=SCRAPE_TIMEOUT_S)
        audit("action", {"tool": "search", "platform": platform, "results": len(offers)})
        return {"raw_by_platform": {platform: [o.model_dump(mode="json") for o in offers]}}
    except Exception as e:  # timeout or platform down: one platform is enough
        audit("action", {"tool": "search", "platform": platform, "error": type(e).__name__})
        return {"raw_by_platform": {platform: None}}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def scrape_ubereats(state: AgentState) -> dict:
    return _scrape(state, "ubereats")


def scrape_doordash(state: AgentState) -> dict:
    return _scrape(state, "doordash")


def normalize(state: AgentState) -> dict:
    offers, missing = [], []
    for platform, rows in sorted((state.get("raw_by_platform") or {}).items()):
        if rows is None:
            missing.append(platform)
            continue
        for row in rows:
            o = Offer(**row)
            snap = write_price_snapshot(o)
            o.evidence_id = audit(
                "evidence",
                {"snapshot_id": snap, "restaurant": o.restaurant, "item": o.item, "price": o.price,
                 "fees": o.fees, "eta_min": o.eta_min, "platform": o.source,
                 "scraped_at": o.scraped_at.isoformat()},
                source_url=o.url or None)
            offers.append(o.model_dump(mode="json"))
    if missing:
        audit("evidence", {"missing_platforms": missing})
    return {"offers": offers, "missing_platforms": missing}


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum() or ch == " ").strip()


def match_offers(state: AgentState) -> dict:
    """Fallback matcher (doc 9.6): same restaurant, fuzzy item name."""
    offers = [Offer(**o) for o in state.get("offers", [])]
    ue = [o for o in offers if o.source == "ubereats"]
    dd = [o for o in offers if o.source == "doordash"]
    matches = []
    for a in ue:
        for b in dd:
            if _norm(a.restaurant) != _norm(b.restaurant):
                continue
            conf = difflib.SequenceMatcher(None, _norm(a.item), _norm(b.item)).ratio()
            if conf >= 0.6:
                ev = [x for x in (a.evidence_id, b.evidence_id) if x]
                m = Match(offer_ids=[a.offer_id, b.offer_id], confidence=round(conf, 2), evidence_ids=ev)
                audit("inference", {"match_id": m.match_id, "same_dish": [a.item, b.item],
                                    "confidence": m.confidence, "method": "fuzzy_name"}, ev)
                matches.append(m.model_dump(mode="json"))
    return {"matches": matches}


def estimate_total(o: Offer, people: int) -> float:
    return round(o.price * max(people, 1) + o.fees, 2)


def _split(state: AgentState) -> tuple[list[tuple[Offer, float]], list[dict]]:
    """Offers that pass every rule (with their total), and the dropped ones with why."""
    c = Constraints(**state.get("constraints", {}))
    rejected = set(state.get("rejected", []))
    excludes = [e.lower() for e in c.hard_excludes]
    passed, dropped = [], []
    for row in state.get("offers", []):
        o = Offer(**row)
        text = f"{o.item} {o.description}".lower()
        total = estimate_total(o, c.people)
        why = None
        if o.offer_id in rejected:
            why = "rejected by user"
        elif any(e.rstrip("s") in text for e in excludes):
            why = "breaks a hard rule"
        elif c.budget_total is not None and total > c.budget_total:
            why = "over budget"
        elif c.max_eta_min is not None and o.eta_min is not None and o.eta_min > c.max_eta_min:
            why = "too slow"
        if why:
            dropped.append({"offer_id": o.offer_id, "item": o.item, "why": why, "evidence_id": o.evidence_id})
        else:
            passed.append((o, total))
    return passed, dropped


def near_misses(state: AgentState) -> list[dict]:
    """Dropped offers that only missed on budget or time: explain shows them when nothing fits."""
    return [d for d in _split(state)[1] if d["why"] in ("over budget", "too slow")]


def filter_rank(state: AgentState) -> dict:
    c = Constraints(**state.get("constraints", {}))
    match_by_offer = {oid: m["match_id"] for m in state.get("matches", []) for oid in m["offer_ids"]}
    passed, dropped = _split(state)
    kept = [RankedOption(offer=o, score=-total, ranker="price", match_id=match_by_offer.get(o.offer_id))
            for o, total in passed]
    kept.sort(key=lambda r: r.score, reverse=True)
    ranker = state.get("ranker", "price")
    ev = [r.offer.evidence_id for r in kept if r.offer.evidence_id] + \
         [d["evidence_id"] for d in dropped if d["evidence_id"]]
    audit("inference", {"ranker": ranker, "kept": [r.offer.offer_id for r in kept],
                        "dropped": dropped}, ev)
    ranked = []
    for r in kept:
        d = r.model_dump(mode="json")
        d["total"] = estimate_total(r.offer, c.people)
        ranked.append(d)
    near = [d for d in dropped if d["why"] in ("over budget", "too slow")]
    return {"ranked": ranked, "near_misses": near}


def persist_learn(state: AgentState) -> dict:
    uid = state.get("user_id", "demo")
    if state.get("handoff_url"):
        sel = state.get("selected") or {}
        oid = write_order(uid, {"restaurant": sel.get("offer", {}).get("restaurant"),
                                "item": sel.get("offer", {}).get("item"),
                                "platform": sel.get("offer", {}).get("source"),
                                "total": (state.get("quote") or {}).get("total"), "run_id": state.get("run_id")})
        audit("action", {"tool": "write_order", "order_id": oid})
    else:
        iid = write_interaction(uid, {"run_id": state.get("run_id"), "rejected": state.get("rejected", []),
                                      "reason": state.get("feedback", "")})
        audit("action", {"tool": "write_interaction", "interaction_id": iid})
    return {}


def distill_preferences(state: AgentState) -> dict:
    """Counting distiller placeholder: P2 replaces with Cognee (doc 8.4)."""
    return {}


def audit_report(state: AgentState) -> dict:
    from contracts import get_audit
    events = get_audit(state.get("run_id", ""))
    counts: dict[str, int] = {}
    for e in events:
        counts[e.kind] = counts.get(e.kind, 0) + 1
    summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
    reply = state.get("reply", "")
    return {"reply": f"{reply}\n\nAudit trail for run {state.get('run_id')}: {summary}.".strip()}
