"""P2's LangGraph nodes. Each takes AgentState and returns a partial update (dicts, as the contract says).

    load_memory -> parse_request (P1) -> scrape -> normalize -> match_offers -> filter_rank -> explain (P1) ...
    ... -> persist_learn -> distill_preferences (background)

`scrape` runs both platforms at the same time (it covers scrape_ubereats and scrape_doordash:
two graph nodes would both write `offers`). Every node writes audit events via contracts.audit.
Importing this module registers P2's tool modes and memory store.
"""
from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any

import contracts as c
from contracts import AgentState, Constraints, Match, Offer, RankedOption
from data import config, distill, store, tools
from data.actors import PLATFORMS
from data.match import dish_text, match_offers as _match
from data.normalize import UNKNOWN_FEES, dedupe
from data.rank import est_total, filter_offers, rank_price, rank_reranker, term_of
from data.sie import get_sie

tools.register()
store.install()

NODE_SIE_WAIT_S = 10.0   # in the graph, wait at most this long on SIE 503/429, then take the fallback


def _run(state: AgentState) -> str:
    return state.get("run_id") or "no-run"


def _user(state: AgentState) -> str:
    return state.get("user_id") or "demo"


def _constraints(state: AgentState) -> Constraints:
    raw = state.get("constraints")
    return Constraints(**raw) if raw else Constraints(raw_text=state.get("request_text", ""))


def _offers(state: AgentState) -> list[Offer]:
    return [o if isinstance(o, Offer) else Offer(**o) for o in state.get("offers") or []]


def _jsonable(v: Any) -> Any:
    return json.loads(json.dumps(v, default=str))


def _sync(coro):
    """Run a coroutine from sync code, even inside a running event loop."""
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
    user, run = _user(state), _run(state)
    orders = c.read_orders(user, 5)
    prefs = c.read_preferences(user)
    rules = [p for p in prefs if p.get("kind") == "hard"]
    soft = [p for p in prefs if p.get("kind") == "soft"]
    rejections = [i for i in c.read_interactions(user, 20) if distill.is_rejection(i)][:5]

    ev = c.audit("evidence", {"what": "memory read from the store", "order_ids": [o.get("order_id") for o in orders],
                              "hard_rules": [r.get("text") for r in rules],
                              "rejection_ids": [i.get("interaction_id") for i in rejections]},
                 run_id=run, node="load_memory")

    if config.distiller_name() == "cognee":
        query = state.get("request_text") or "food preferences"
        try:
            known = {p.get("text") for p in soft}
            for text in _sync(distill.cognee_recall(f"What food does the user like? {query}"))[:5]:
                if text not in known:
                    soft.append({"text": text[:300], "kind": "soft", "source": "learned", "confidence": 0.5,
                                 "episode_ids": distill.EPISODE_REF.findall(text), "via": "cognee"})
        except Exception as exc:  # Cognee down: the stored preferences still work
            c.audit("inference", {"what": "Cognee recall failed; using stored preferences only",
                                  "error": type(exc).__name__}, [ev], run_id=run, node="load_memory")

    learned = [p for p in soft if p.get("source") == "learned"]
    if learned:
        c.audit("inference", {"what": "learned soft preferences recalled",
                              "preferences": [{k: p.get(k) for k in ("text", "confidence", "episode_ids")}
                                              for p in learned]},
                [ev], run_id=run, node="load_memory")
    return {"memory": _jsonable({"orders": orders, "rules": rules, "soft_prefs": soft, "rejections": rejections})}


def save_stated_rule(user_id: str, text: str, kind: str = "hard", run_id: str | None = None) -> str:
    """For parse_request / handle_feedback (P1). Only the user's own words create a hard rule."""
    pid = c.write_preference(user_id, {"text": text, "kind": kind, "source": "stated", "confidence": 1.0,
                                       "term": term_of(text) if kind == "hard" else None,
                                       "created_at": c.now().isoformat()})
    c.audit("action", {"what": f"saved stated {kind} rule", "text": text, "pref_id": pid},
            run_id=run_id, node="memory")
    return pid


# ---------------------------------------------------------------- scrape

def scrape(state: AgentState) -> dict[str, Any]:
    """Both platforms at the same time, 90 s each. One platform result is enough."""
    run, cons = _run(state), _constraints(state)
    mode = config.tool_mode()
    shop = c.get_tools(mode)
    offers: list[Offer] = []
    missing: list[str] = []
    pool = ThreadPoolExecutor(max_workers=len(PLATFORMS))
    futures = {p: pool.submit(shop.search, cons, p) for p in PLATFORMS}
    for p, fut in futures.items():
        payload: dict[str, Any] = {"platform": p, "mode": mode}
        try:
            got = fut.result(timeout=config.ACTOR_TIMEOUT_S)
            offers.extend(got)
            payload["offers"] = len(got)
            if not got:
                missing.append(p)
                payload["error"] = "no offers returned"
        except Exception as exc:
            missing.append(p)
            # concurrent.futures.TimeoutError is the builtin TimeoutError: tell "we waited 90 s"
            # apart from "the platform itself raised TimeoutError" by whether the call finished.
            if isinstance(exc, FutureTimeout) and not fut.done():
                payload["error"] = f"timed out after {config.ACTOR_TIMEOUT_S} s"
            else:
                payload["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        raw = getattr(shop, "last", {}).get(p)
        if raw is not None:
            payload["scraped_at"] = raw.scraped_at.isoformat()
        c.audit("evidence", payload, run_id=run, node=f"scrape_{p}",
                actor_run_id=raw.actor_run_id if raw is not None else None)
        c.log_event("scrape", {"platform": p, "ok": p not in missing})
    pool.shutdown(wait=False, cancel_futures=True)
    return {"offers": [o.model_dump(mode="json") for o in offers], "missing_platforms": missing}


# ---------------------------------------------------------------- normalize

def normalize(state: AgentState) -> dict[str, Any]:
    """One format for both platforms; every price becomes an evidence event and a price snapshot."""
    run = _run(state)
    offers = dedupe(_offers(state))
    new = [o for o in offers if not o.evidence_id]
    ids = store.audit_many("evidence", [
        ({"platform": o.source, "restaurant": o.restaurant, "item": o.item, "price": o.price, "fees": o.fees,
          "fees_known": o.offer_id not in UNKNOWN_FEES, "eta_min": o.eta_min,
          "scraped_at": o.scraped_at.isoformat(), "offer_id": o.offer_id}, [], o.url or None) for o in new],
        run_id=run, node="normalize")
    for o, eid in zip(new, ids):
        o.evidence_id = eid
    store.write_snapshots(run, new)
    return {"offers": [o.model_dump(mode="json") for o in offers]}


# ---------------------------------------------------------------- match_offers

def match_offers(state: AgentState) -> dict[str, Any]:
    run, offers = _run(state), _offers(state)
    sie, note = _sie_or_none(), None
    matches, method = [], "fuzzy"
    if sie is not None:
        try:
            vecs = dict(zip([o.offer_id for o in offers],
                            sie.embed([dish_text(o) for o in offers], max_wait_s=NODE_SIE_WAIT_S)))
            matches, method = _match(offers, sie, vecs)
            store.set_snapshot_vectors(vecs, run)
        except Exception as exc:
            note = f"SIE embeddings failed ({type(exc).__name__}); used fuzzy names"
    else:
        note = "SIE not configured; used fuzzy names"
    if note:
        matches, method = _match(offers, None)
    by_id = {o.offer_id: o for o in offers}
    pairs = [(by_id[m.offer_ids[0]], by_id[m.offer_ids[1]], m) for m in matches]
    store.audit_many("inference", [
        ({"what": "same dish on both platforms", "restaurant": a.restaurant, "ubereats_item": a.item,
          "doordash_item": b.item, "confidence": m.confidence, "method": method, "match_id": m.match_id},
         m.evidence_ids, None) for a, b, m in pairs], run_id=run, node="match_offers")
    if note:
        c.audit("inference", {"what": "matching fallback", "note": note, "matches": len(matches)},
                [o.evidence_id for o in offers if o.evidence_id], run_id=run, node="match_offers")
    store.write_matches(run, [{**m.model_dump(), "method": method} for m in matches])
    return {"matches": [m.model_dump() for m in matches]}


# ---------------------------------------------------------------- filter_rank

def filter_rank(state: AgentState, top_k: int = 10) -> dict[str, Any]:
    run, cons, mem = _run(state), _constraints(state), state.get("memory") or {}
    offers = _offers(state)
    matches = [Match(**m) for m in state.get("matches") or []]
    rejected = list(state.get("rejected") or [])
    ranker = state.get("ranker") or config.ranker_name()
    fr = filter_offers(offers, cons, mem, matches, rejected)
    ev_of = {o.offer_id: o.evidence_id for o in offers}
    all_ev = [e for e in ev_of.values() if e]   # every inference cites evidence, even when a list is empty
    c.audit("inference", {"what": "removed options that break a rule", "removed": fr.removed, "kept": len(fr.kept)},
            [ev_of[x["offer_id"]] for x in fr.removed if ev_of.get(x["offer_id"])] or all_ev,
            run_id=run, node="filter_rank")

    bad = {o.restaurant for o in offers if o.offer_id in rejected}   # rejected this session
    used = "price"
    ranked = rank_price(fr.kept, cons.people)
    if ranker == "reranker":
        scorer = _sie_or_none()
        if scorer is None:
            c.audit("inference", {"what": "reranker not configured; price-only ranker used"}, all_ev,
                    run_id=run, node="filter_rank")
        else:
            try:
                ranked, used = rank_reranker(fr.kept, cons, mem, scorer, bad, max_wait_s=NODE_SIE_WAIT_S), "reranker"
            except Exception as exc:
                c.audit("inference", {"what": "reranker failed; price-only ranker used",
                                      "error": type(exc).__name__}, all_ev, run_id=run, node="filter_rank")
    ranked = ranked[:top_k]
    c.audit("inference", {"what": "ranked options", "ranker": used, "requested_ranker": ranker,
                          "top": [{"offer_id": r.offer.offer_id, "item": r.offer.item, "restaurant": r.offer.restaurant,
                                   "platform": r.offer.source, "est_total": est_total(r.offer, cons.people),
                                   "score": r.score, "match_id": r.match_id} for r in ranked[:5]]},
            [r.offer.evidence_id for r in ranked[:5] if r.offer.evidence_id] or all_ev, run_id=run, node="filter_rank")
    c.log_event("ranked", {"ranker": used, "options": len(ranked)})
    by_id = {o.offer_id: o for o in offers}
    near = [{"offer_id": x["offer_id"], "item": by_id[x["offer_id"]].item, "why": x["reason"],
             "evidence_id": ev_of.get(x["offer_id"])}
            for x in fr.removed if x["reason"].startswith(("over budget", "too slow"))] if not ranked else []
    return {"ranked": [r.model_dump(mode="json") for r in ranked], "ranker": used, "near_misses": near}


# ---------------------------------------------------------------- persist_learn

def persist_learn(state: AgentState) -> dict[str, Any]:
    """Saves the order (only after approval) or the rejection as an episode."""
    user, run, cons = _user(state), _run(state), _constraints(state)
    offers = {o.offer_id: o for o in _offers(state)}
    ranked = [RankedOption(**r) for r in state.get("ranked") or []]
    episodes: list[str] = []

    sel = state.get("selected")
    sel_offer = Offer(**sel["offer"]) if sel and sel.get("offer") else None
    if state.get("approval") is True and sel_offer is not None:
        q = state.get("quote") or {}
        oid = c.write_order(user, {
            "order_id": c.new_id("ord"), "run_id": run, "platform": sel_offer.source,
            "restaurant": sel_offer.restaurant, "items": [sel_offer.item], "cuisine": cons.cuisine,
            "price_paid": float(q.get("total") or est_total(sel_offer, cons.people)),
            "fees": q.get("fees", sel_offer.fees), "eta_min": sel_offer.eta_min, "ordered_at": c.now().isoformat()})
        episodes.append(oid)

    rejected = list(state.get("rejected") or [])
    reason = state.get("feedback") or None
    if state.get("approval") is False and sel_offer is not None and sel_offer.offer_id not in rejected:
        rejected.append(sel_offer.offer_id)
    if rejected or reason or ranked:
        rej_rest = sorted({offers[r].restaurant for r in rejected if r in offers})
        iid = c.write_interaction(user, {
            "run_id": run, "shown": [r.offer.offer_id for r in ranked[:5]],
            "selected": sel_offer.offer_id if sel_offer else None, "rejected": rejected,
            "rejected_restaurants": rej_rest, "reason": reason, "at": c.now().isoformat()})
        if rejected or reason:
            episodes.append(iid)
    c.audit("action", {"what": "saved episodes", "episode_ids": episodes}, run_id=run, node="persist_learn")
    return {}


# ---------------------------------------------------------------- distill_preferences

def run_distiller(user_id: str, run_id: str) -> list[dict]:
    """Synchronous distiller run. Cognee when DISTILLER=cognee, counting otherwise or on failure."""
    if not distill.should_distill(user_id):
        return []
    new_orders, new_rej = distill.episodes_since(user_id, store.last_distilled_at(user_id))
    all_orders, all_rej = distill.history(user_id)
    new_ids = [e.get("order_id") or e.get("interaction_id") for e in [*new_orders, *new_rej]]
    ev = c.audit("evidence", {"what": "episodes read for the distiller", "new_episode_ids": new_ids,
                              "orders": len(all_orders), "rejections": len(all_rej)},
                 run_id=run_id, node="distill_preferences")
    prefs = distill.count_preferences(all_orders, all_rej)   # also the preference -> episode-ID map
    mode = "counting"
    if config.distiller_name() == "cognee":
        try:
            _sync(distill.cognee_remember([distill.episode_text(e) for e in [*new_orders, *new_rej]]))
            mode = "cognee"
        except Exception as exc:
            c.audit("inference", {"what": "Cognee distiller failed; counting distiller used",
                                  "error": type(exc).__name__}, [ev], run_id=run_id, node="distill_preferences")
    for p in prefs:
        assert p["kind"] == "soft" and p["source"] == "learned"   # the distiller never writes a hard rule
        c.write_preference(user_id, p)
    store.set_last_distilled(user_id, c.now())
    c.audit("inference", {"what": "learned soft preferences", "distiller": mode, "new_episode_ids": new_ids,
                          "preferences": [{k: p[k] for k in ("text", "confidence", "episode_ids")} for p in prefs]},
            [ev], run_id=run_id, node="distill_preferences")
    c.log_event("distiller_run", {"distiller": mode, "preferences": len(prefs)})
    return prefs


def distill_preferences(state: AgentState, background: bool = True) -> dict[str, Any]:
    """Runs in the background: the user does not wait for it."""
    user, run = _user(state), _run(state)
    if background:
        threading.Thread(target=run_distiller, args=(user, run), daemon=True).start()
    else:
        run_distiller(user, run)
    return {}
