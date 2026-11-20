"""P2's LangGraph nodes. Each takes AgentState and returns a partial state update.

load_memory, scrape (both platforms at once; scrape_ubereats / scrape_doordash for
separate graph nodes), normalize, match_offers, filter_rank, persist_learn,
distill_preferences. Every node writes audit events through contracts.audit.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from contracts import (PLATFORMS, AgentState, Constraints, Interaction, MemoryContext, OrderRecord, Platform,
                       Preference, SearchResult, audit, log_event)
from data import config, distill
from data.match import dish_text, match_offers as _match
from data.normalize import normalize_results
from data.rank import filter_offers, rank, term_of
from data.sie import get_sie
from data.store import get_store
from data.tools import get_tools


def _run(state: AgentState) -> str:
    return state.get("run_id") or "run-unknown"


def _user(state: AgentState) -> str:
    return state.get("user_id") or "demo"


def _constraints(state: AgentState) -> Constraints:
    c = state.get("constraints")
    if isinstance(c, dict):
        return Constraints(**c)
    return c or Constraints(query=state.get("request", ""))


def _memory(state: AgentState) -> MemoryContext | None:
    m = state.get("memory")
    return MemoryContext(**m) if isinstance(m, dict) else m


def _sync(coro):
    """Run a coroutine from sync code, even when an event loop is already running (LangGraph async)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict[str, Any] = {}

    def target():
        try:
            box["v"] = asyncio.run(coro)
        except BaseException as exc:
            box["e"] = exc
    t = threading.Thread(target=target)
    t.start(); t.join()
    if "e" in box:
        raise box["e"]
    return box.get("v")


def _sie_or_none():
    try:
        return get_sie()
    except RuntimeError:
        return None


# ---------------------------------------------------------------- load_memory

def load_memory(state: AgentState) -> dict[str, Any]:
    store, user, run = get_store(), _user(state), _run(state)
    store.ensure_user(user)
    orders = store.recent_orders(user, 5)
    hard = store.preferences(user, kind="hard")
    soft = store.preferences(user, kind="soft")
    rejections = store.rejections_since(user, None)[-5:]

    ev = audit("evidence", {"what": "memory read from SingleStore", "order_ids": [o.id for o in orders],
                            "hard_rules": [p.text for p in hard],
                            "rejection_ids": [i.id for i in rejections]},
               run_id=run, node="load_memory")

    if config.distiller_name() == "cognee":
        query = state.get("request") or _constraints(state).query or "food preferences"
        try:
            recalled = _sync(distill.cognee_recall(f"What food does the user like? {query}"))
            known = {p.text for p in soft}
            for text in recalled[:5]:
                if text not in known:
                    soft.append(Preference(id=f"cognee-{uuid.uuid4().hex[:8]}", user_id=user, text=text[:300],
                                           kind="soft", source="learned", confidence=0.5,
                                           episode_ids=distill.EPISODE_REF.findall(text)))
        except Exception as exc:  # Cognee down: SingleStore prefs still work
            audit("inference", {"what": "Cognee recall failed; using SingleStore preferences only",
                                "error": type(exc).__name__}, [ev], run_id=run, node="load_memory")

    learned = [p for p in soft if p.source == "learned"]
    if learned:
        audit("inference", {"what": "learned soft preferences recalled",
                            "preferences": [{"text": p.text, "confidence": p.confidence,
                                             "episode_ids": p.episode_ids} for p in learned]},
              [ev], run_id=run, node="load_memory")
    return {"memory": MemoryContext(recent_orders=orders, hard_rules=hard, soft_prefs=soft,
                                    recent_rejections=rejections)}


def save_stated_rule(user_id: str, text: str, kind: str = "hard", run_id: str | None = None) -> Preference:
    """For parse_request / handle_feedback (P1): only the user's own words create a hard rule."""
    p = Preference(id=f"pref-{user_id}-stated-{uuid.uuid4().hex[:8]}", user_id=user_id, text=text,
                   kind=kind, source="stated", confidence=1.0, term=term_of(text) if kind == "hard" else None,
                   created_at=datetime.now(timezone.utc))
    get_store().upsert_preference(p)
    if run_id:
        audit("action", {"what": f"saved stated {kind} rule", "text": text}, run_id=run_id, node="memory")
    return p


# ---------------------------------------------------------------- scrape

async def _search(platform: Platform, state: AgentState) -> SearchResult:
    c = _constraints(state)
    loc = c.location or config.env("SCRAPE_CITY", "San Francisco, CA")
    started = datetime.now(timezone.utc)
    try:
        res = await asyncio.wait_for(get_tools().search(platform, loc, c), timeout=config.ACTOR_TIMEOUT_S)
    except asyncio.TimeoutError:
        res = SearchResult(platform=platform, scraped_at=started, error=f"timed out after {config.ACTOR_TIMEOUT_S} s")
    except Exception as exc:
        res = SearchResult(platform=platform, scraped_at=started, error=f"{type(exc).__name__}: {str(exc)[:200]}")
    payload = {"platform": platform, "mode": config.tool_mode(), "items": len(res.items),
               "scraped_at": res.scraped_at.isoformat()}
    if res.error:
        payload["error"] = res.error
    audit("evidence", payload, run_id=_run(state), node=f"scrape_{platform}", actor_run_id=res.actor_run_id)
    log_event("scrape", {"platform": platform, "ok": not res.error, "items": len(res.items)})
    return res


async def scrape(state: AgentState) -> dict[str, Any]:
    """Both Actors at the same time, 90 s each. One platform result is enough."""
    results = await asyncio.gather(*(_search(p, state) for p in PLATFORMS))
    by = {r.platform: r for r in results}
    return {"search_results": by, "missing_platforms": [p for p, r in by.items() if r.error]}


async def scrape_ubereats(state: AgentState) -> dict[str, Any]:
    r = await _search("ubereats", state)
    return {"search_results": {"ubereats": r}, "missing_platforms": ["ubereats"] if r.error else []}


async def scrape_doordash(state: AgentState) -> dict[str, Any]:
    r = await _search("doordash", state)
    return {"search_results": {"doordash": r}, "missing_platforms": ["doordash"] if r.error else []}


# ---------------------------------------------------------------- normalize

def normalize(state: AgentState) -> dict[str, Any]:
    run = _run(state)
    results = {k: (SearchResult(**v) if isinstance(v, dict) else v)
               for k, v in (state.get("search_results") or {}).items()}
    offers = normalize_results(results)
    for o in offers:   # one evidence event per price snapshot
        o.evidence_id = audit("evidence", {"platform": o.platform, "restaurant": o.restaurant, "item": o.item,
                                           "price": o.price, "fees": o.fees, "eta_min": o.eta_min,
                                           "scraped_at": o.scraped_at.isoformat(), "snapshot_id": o.id},
                              run_id=run, node="normalize", source_url=o.source_url, actor_run_id=o.actor_run_id)
    get_store().add_snapshots(run, offers)
    return {"offers": offers}


# ---------------------------------------------------------------- match_offers

def match_offers(state: AgentState) -> dict[str, Any]:
    run, offers = _run(state), list(state.get("offers") or [])
    sie, note = _sie_or_none(), None
    matches, method = [], "fuzzy"
    if sie is not None:
        try:
            vecs = dict(zip([o.id for o in offers], sie.embed([dish_text(o) for o in offers])))
            matches, method = _match(offers, sie, vecs)
            get_store().set_snapshot_vectors(vecs)
        except Exception as exc:
            note = f"SIE embeddings failed ({type(exc).__name__}); used fuzzy names"
    else:
        note = "SIE not configured; used fuzzy names"
    if method == "fuzzy":
        matches, method = _match(offers, None)
    by_id = {o.id: o for o in offers}
    for m in matches:
        a, b = by_id[m.ubereats_offer_id], by_id[m.doordash_offer_id]
        audit("inference", {"what": "same dish on both platforms", "restaurant": a.restaurant,
                            "ubereats_item": a.item, "doordash_item": b.item, "confidence": m.confidence,
                            "method": m.method, "match_id": m.id},
              m.evidence_ids, run_id=run, node="match_offers")
    if note:
        audit("inference", {"what": "matching fallback", "note": note, "matches": len(matches)},
              run_id=run, node="match_offers")
    get_store().add_matches(run, matches)
    return {"matches": matches}


# ---------------------------------------------------------------- filter_rank

def filter_rank(state: AgentState, top_k: int = 10) -> dict[str, Any]:
    run, c, mem = _run(state), _constraints(state), _memory(state)
    ranker = state.get("ranker") or config.ranker_name()
    fr = filter_offers(list(state.get("offers") or []), c, mem, list(state.get("matches") or []),
                       list(state.get("rejected") or []))
    audit("inference", {"what": "removed options that break a rule", "removed": fr.removed,
                        "kept": len(fr.kept)}, run_id=run, node="filter_rank")
    scorer = _sie_or_none() if ranker == "reranker" else None
    used = ranker
    try:
        ranked = rank(fr.kept, ranker, c, mem, scorer, state.get("rejected_restaurants"))
        if ranker == "reranker" and scorer is None:
            used = "price"
    except Exception as exc:
        ranked, used = rank(fr.kept, "price", c, mem, None), "price"
        audit("inference", {"what": "reranker failed; price-only ranker used", "error": type(exc).__name__},
              run_id=run, node="filter_rank")
    ranked = ranked[:top_k]
    audit("inference", {"what": "ranked options", "ranker": used, "requested_ranker": ranker,
                        "top": [{"offer_id": r.offer.id, "item": r.offer.item, "restaurant": r.offer.restaurant,
                                 "platform": r.offer.platform, "est_total": r.est_total, "score": r.score,
                                 "alt_platform": r.alt_offer.platform if r.alt_offer else None,
                                 "alt_total": r.alt_offer and round(r.alt_offer.price * max(c.party_size, 1)
                                                                    + (r.alt_offer.fees or 0), 2)}
                                for r in ranked[:5]]},
          [e for r in ranked[:5] for e in r.evidence_ids], run_id=run, node="filter_rank")
    log_event("ranked", {"ranker": used, "options": len(ranked)})
    return {"ranked": ranked, "ranker": used}


# ---------------------------------------------------------------- persist_learn

def persist_learn(state: AgentState) -> dict[str, Any]:
    store, user, run = get_store(), _user(state), _run(state)
    now = datetime.now(timezone.utc)
    c = _constraints(state)
    ranked = list(state.get("ranked") or [])
    episodes: list[str] = []

    sel_id = state.get("selected")
    o = next((r.offer for r in ranked if r.offer.id == sel_id), None)
    if o is None and sel_id:
        o = store.get_snapshot(sel_id)
    if state.get("approved") and o is not None:
        q = state.get("quote") or {}
        est = o.price * max(c.party_size, 1) + (o.fees or 0)
        order = OrderRecord(id=f"ord-{uuid.uuid4().hex[:8]}", user_id=user, run_id=run, platform=o.platform,
                            restaurant=o.restaurant, items=[o.item], cuisine=c.cuisine,
                            price_paid=float(q.get("total") or est), fees=q.get("fees", o.fees),
                            eta_min=q.get("eta_min", o.eta_min), ordered_at=now)
        store.add_order(order)
        episodes.append(order.id)

    rejected = list(state.get("rejected") or [])
    reason = state.get("rejection_reason")
    if rejected or reason or ranked:
        rej_rest = list(state.get("rejected_restaurants") or [])
        for rid in rejected:
            snap = store.get_snapshot(rid)
            if snap and snap.restaurant not in rej_rest:
                rej_rest.append(snap.restaurant)
        inter = Interaction(id=f"int-{uuid.uuid4().hex[:8]}", user_id=user, run_id=run,
                            shown=[r.offer.id for r in ranked[:5]], selected=sel_id, rejected=rejected,
                            rejected_restaurants=rej_rest, reason=reason, at=now)
        store.add_interaction(inter)
        if rejected or reason:
            episodes.append(inter.id)
    audit("action", {"what": "saved episodes", "episode_ids": episodes}, run_id=run, node="persist_learn")
    return {"episode_ids": episodes}


# ---------------------------------------------------------------- distill_preferences

def run_distiller(user_id: str, run_id: str) -> list[Preference]:
    """Synchronous distiller run. Cognee when DISTILLER=cognee, counting otherwise or on failure."""
    store = get_store()
    if not distill.should_distill(store, user_id):
        return []
    new_orders, new_rejections = distill.episodes_since(store, user_id)
    all_orders = store.orders_since(user_id, None)
    all_rejections = store.rejections_since(user_id, None)
    prefs = distill.count_preferences(user_id, all_orders, all_rejections)   # also the episode-ID map
    mode = "counting"
    if config.distiller_name() == "cognee":
        try:
            _sync(distill.cognee_remember([distill.episode_text(e) for e in [*new_orders, *new_rejections]]))
            mode = "cognee"
        except Exception as exc:
            audit("inference", {"what": "Cognee distiller failed; counting distiller used",
                                "error": type(exc).__name__}, run_id=run_id, node="distill_preferences")
    for p in prefs:
        assert p.kind == "soft" and p.source == "learned"   # the distiller never writes a hard rule
        store.upsert_preference(p)
    store.set_last_distilled(user_id, datetime.now(timezone.utc))
    audit("inference", {"what": "learned soft preferences", "distiller": mode,
                        "new_episode_ids": [e.id for e in [*new_orders, *new_rejections]],
                        "preferences": [{"text": p.text, "confidence": p.confidence, "episode_ids": p.episode_ids}
                                        for p in prefs]},
          run_id=run_id, node="distill_preferences")
    log_event("distiller_run", {"distiller": mode, "preferences": len(prefs)})
    return prefs


def distill_preferences(state: AgentState, background: bool = True) -> dict[str, Any]:
    """Runs in the background: the user does not wait for it."""
    user, run = _user(state), _run(state)
    if background:
        threading.Thread(target=run_distiller, args=(user, run), daemon=True).start()
        return {}
    run_distiller(user, run)
    return {}
