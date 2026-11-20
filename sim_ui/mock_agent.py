"""Mock MealMatch agent for UI work until P1's LangGraph /chat is ready.

Speaks the same HTTP contract the UI expects from the real agent (see
sim_ui/README.md). Uses contracts.get_tools() and contracts.audit(), so the
audit trail it produces has the same shape as the real one.

Run:  uv run uvicorn sim_ui.mock_agent:app --reload --port 8000
Open: http://localhost:8000/
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

import contracts as c

STATIC = Path(__file__).parent / "static"
QUOTE_TOLERANCE = 0.10  # quote may drift at most 10% from the ranked price
PLATFORMS: list[c.Platform] = ["ubereats", "doordash"]


class ChatAction(BaseModel):
    type: Literal["select", "reject", "approve", "decline"]
    offer_id: Optional[str] = None
    reason: str = ""


class ChatRequest(BaseModel):
    thread_id: Optional[str] = None
    user_id: str = "demo"
    message: str = ""
    action: Optional[ChatAction] = None


THREADS: dict[str, dict[str, Any]] = {}

app = FastAPI(title="MealMatch mock agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ------------------------------------------------------------------ nodes


def parse_request(text: str, prev: dict | None = None) -> c.Constraints:
    """Tiny regex parser standing in for the LLM node."""
    base = c.Constraints(**prev) if prev else c.Constraints()
    t = text.lower()
    upd: dict[str, Any] = {"raw_text": text}
    if m := re.search(r"\$\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:dollars|bucks)", t):
        upd["budget_total"] = float(m.group(1) or m.group(2))
    if m := re.search(r"(?:for|feeds?)\s+(\d+)", t):
        upd["people"] = int(m.group(1))
    if m := re.search(r"(?:under|within|in)\s+(\d+)\s*(?:min|minutes)", t):
        upd["max_eta_min"] = int(m.group(1))
    excludes = re.findall(r"no\s+([a-z]+)", t)
    if excludes:
        upd["hard_excludes"] = sorted(set(base.hard_excludes) | set(excludes))
    dish = re.sub(r"\$\s*\d+(\.\d+)?|for\s+\d+|under\s+\d+\s*\w+|no\s+[a-z]+|[,.!?]", " ", t)
    dish = " ".join(w for w in dish.split() if w not in {"i", "want", "some", "get", "me", "please", "and", "under", "within"})
    if dish and not prev:
        upd["dish_query"] = dish
    return base.model_copy(update=upd)


def search_both(constraints: c.Constraints, mode: c.ToolMode) -> tuple[list[c.Offer], list[str]]:
    tools = c.get_tools(mode)
    offers: list[c.Offer] = []
    missing: list[str] = []
    for p in PLATFORMS:
        try:
            found = tools.search(constraints, p)
        except Exception as exc:  # one platform down must not stop the run
            missing.append(p)
            c.audit("evidence", {"platform": p, "error": type(exc).__name__, "offers": 0})
            continue
        for o in found:
            c.write_price_snapshot(o)
            o.evidence_id = c.audit(
                "evidence",
                {"platform": p, "restaurant": o.restaurant, "item": o.item,
                 "price": o.price, "fees": o.fees, "eta_min": o.eta_min,
                 "scraped_at": o.scraped_at.isoformat()},
                source_url=o.url,
            )
            offers.append(o)
    return offers, missing


def _norm(s: str) -> set[str]:
    return {w for w in re.sub(r"[^a-z ]", " ", s.lower()).split() if len(w) > 2}


def match_dishes(offers: list[c.Offer]) -> dict[str, c.Match]:
    """Same restaurant + overlapping dish words across platforms = a match."""
    by_offer: dict[str, c.Match] = {}
    for a in offers:
        for b in offers:
            if a.source >= b.source or a.restaurant != b.restaurant:
                continue
            wa, wb = _norm(a.item), _norm(b.item)
            conf = len(wa & wb) / max(len(wa | wb), 1)
            if conf >= 0.5:
                ev = [a.evidence_id, b.evidence_id]
                m = c.Match(offer_ids=[a.offer_id, b.offer_id], confidence=round(conf, 2), evidence_ids=ev)
                c.audit("inference", {"what": "same dish on both platforms", "match_id": m.match_id,
                                      "items": [f"{a.item} ({a.source})", f"{b.item} ({b.source})"],
                                      "confidence": m.confidence}, ev)
                by_offer[a.offer_id] = by_offer[b.offer_id] = m
    return by_offer


def rank(offers: list[c.Offer], cons: c.Constraints, matches: dict[str, c.Match],
         rejected: list[str]) -> list[c.RankedOption]:
    ranked: list[c.RankedOption] = []
    for o in offers:
        ev = [o.evidence_id]
        text = f"{o.item} {o.description}".lower()
        if o.offer_id in rejected:
            continue
        hit = next((x for x in cons.hard_excludes if x in text), None)
        if hit:
            c.audit("inference", {"what": "excluded", "offer": o.item, "because": f"contains '{hit}'"}, ev)
            continue
        total = o.price * cons.people + o.fees
        if cons.budget_total and total > cons.budget_total:
            c.audit("inference", {"what": "excluded", "offer": o.item,
                                  "because": f"${total:.2f} is over the ${cons.budget_total:.2f} budget"}, ev)
            continue
        if cons.max_eta_min and o.eta_min and o.eta_min > cons.max_eta_min:
            c.audit("inference", {"what": "excluded", "offer": o.item,
                                  "because": f"{o.eta_min} min is over {cons.max_eta_min} min"}, ev)
            continue
        reasons = [{"text": f"${o.price:.2f} × {cons.people} + ${o.fees:.2f} fees = ${total:.2f}", "evidence_ids": ev}]
        if o.eta_min:
            reasons.append({"text": f"Arrives in about {o.eta_min} min", "evidence_ids": ev})
        m = matches.get(o.offer_id)
        if m:
            other = next(x for x in offers if x.offer_id in m.offer_ids and x.offer_id != o.offer_id)
            other_total = other.price * cons.people + other.fees
            diff = other_total - total
            word = "cheaper" if diff > 0 else "more"
            reasons.append({"text": f"${abs(diff):.2f} {word} than the same dish on {other.source}",
                            "evidence_ids": m.evidence_ids})
        ranked.append(c.RankedOption(offer=o, score=round(-total, 2), match_id=m.match_id if m else None,
                                     reasons=reasons))
    ranked.sort(key=lambda r: r.score, reverse=True)
    if ranked:
        c.audit("inference", {"what": "ranking", "ranker": "price",
                              "order": [f"{r.offer.item} ({r.offer.source})" for r in ranked[:3]]},
                [r.offer.evidence_id for r in ranked[:3]])
    return ranked[:3]


# ------------------------------------------------------------------ state machine


def _dump(st: dict) -> dict:
    events = c.get_audit(st["run_id"])
    return {
        "thread_id": st["thread_id"], "run_id": st["run_id"], "status": st["status"],
        "reply": st.get("reply", ""), "constraints": st.get("constraints"),
        "ranked": st.get("ranked", []), "missing_platforms": st.get("missing_platforms", []),
        "cart": st.get("cart"), "quote": st.get("quote"), "quote_ok": st.get("quote_ok"),
        "handoff_url": st.get("handoff_url", ""),
        "audit": [e.model_dump(mode="json") for e in events],
    }


def _search_and_rank(st: dict, mode: c.ToolMode) -> None:
    cons = c.Constraints(**st["constraints"])
    c.set_audit_context(st["run_id"], "search")
    offers, missing = search_both(cons, mode)
    st["offers"] = [o.model_dump(mode="json") for o in offers]
    st["missing_platforms"] = missing
    c.set_audit_context(st["run_id"], "match")
    matches = match_dishes(offers)
    c.set_audit_context(st["run_id"], "rank")
    ranked = rank(offers, cons, matches, st.get("rejected", []))
    st["ranked"] = [r.model_dump(mode="json") for r in ranked]
    if ranked:
        st["status"] = "awaiting_selection"
        st["reply"] = (f"I compared {len(offers)} dishes on Uber Eats and DoorDash. "
                       f"Here are the best {len(ranked)}. Pick one and I'll build and verify the cart.")
    else:
        st["status"] = "awaiting_message"
        st["reply"] = "Nothing fits those limits. Try a bigger budget or fewer exclusions."
    if missing:
        st["reply"] += f" ({', '.join(missing)} didn't respond, so it's missing from this comparison.)"


@app.post("/chat")
def chat(req: ChatRequest) -> dict:
    mode: c.ToolMode = "fixtures"
    if req.thread_id and req.thread_id in THREADS:
        st = THREADS[req.thread_id]
    else:
        tid = req.thread_id or c.new_id("thr")
        st = THREADS[tid] = {"thread_id": tid, "run_id": c.new_id("run"), "user_id": req.user_id,
                             "rejected": [], "status": "new"}
    a = req.action

    if a is None:  # free-text turn: new request or a refinement
        if not req.message.strip():
            raise HTTPException(400, "message or action required")
        if st["status"] == "done":
            st.update(run_id=c.new_id("run"), rejected=[], cart=None, quote=None, handoff_url="",
                      constraints=None)
        c.set_audit_context(st["run_id"], "parse_request")
        cons = parse_request(req.message, st.get("constraints"))
        st["constraints"] = cons.model_dump()
        c.audit("inference", {"what": "parsed request", **{k: v for k, v in st["constraints"].items() if v}})
        _search_and_rank(st, mode)
        return _dump(st)

    if a.type == "reject":
        c.set_audit_context(st["run_id"], "select_option")
        st["rejected"].append(a.offer_id)
        c.audit("approval", {"approved": False, "offer_id": a.offer_id, "reason": a.reason or "not this one"})
        _search_and_rank(st, mode)
        return _dump(st)

    if a.type == "select":
        if st["status"] != "awaiting_selection":
            raise HTTPException(409, f"not waiting for a selection (status={st['status']})")
        sel = next((r for r in st["ranked"] if r["offer"]["offer_id"] == a.offer_id), None)
        if not sel:
            raise HTTPException(404, "offer not in the current options")
        offer = c.Offer(**sel["offer"])
        cons = c.Constraints(**st["constraints"])
        tools = c.get_tools(mode)
        c.set_audit_context(st["run_id"], "select_option")
        c.audit("approval", {"approved": True, "what": "picked option", "offer": offer.item,
                             "platform": offer.source}, [offer.evidence_id])
        c.set_audit_context(st["run_id"], "build_cart")
        cart = tools.build_cart(offer, qty=cons.people)
        c.audit("action", {"tool": "build_cart", "platform": cart.platform, "restaurant": cart.restaurant,
                           "lines": [l.model_dump() for l in cart.lines]}, [offer.evidence_id])
        c.set_audit_context(st["run_id"], "verify_quote")
        quote = tools.quote(cart)
        qid = c.audit("evidence", {"tool": "quote", "total": quote.total,
                                   "quoted_at": quote.quoted_at.isoformat() if quote.quoted_at else None},
                      source_url=quote.deep_link)
        drift = (quote.total - cart.total) / cart.total if cart.total else 0
        ok = drift <= QUOTE_TOLERANCE and (not cons.budget_total or quote.total <= cons.budget_total)
        c.audit("inference", {"what": "quote check", "expected": cart.total, "quoted": quote.total,
                              "drift_pct": round(drift * 100, 1), "ok": ok}, [offer.evidence_id, qid])
        st.update(selected=sel, cart=cart.model_dump(mode="json"), quote=quote.model_dump(mode="json"), quote_ok=ok)
        st["status"] = "awaiting_approval"
        st["reply"] = (f"Cart verified on {offer.source}: ${quote.total:.2f} total. Approve and I'll hand you the checkout link."
                       if ok else f"Heads up: the live quote is ${quote.total:.2f}, {drift*100:.0f}% above what I ranked. "
                                  "Approve anyway, or decline and I'll show other options.")
        return _dump(st)

    if a.type in ("approve", "decline"):
        if st["status"] != "awaiting_approval":
            raise HTTPException(409, f"not waiting for approval (status={st['status']})")
        c.set_audit_context(st["run_id"], "approve")
        approved = a.type == "approve"
        c.audit("approval", {"approved": approved, "what": "pay for cart", "total": st["quote"]["total"],
                             **({"reason": a.reason} if a.reason else {})})
        if not approved:
            st["rejected"].append(st["selected"]["offer"]["offer_id"])
            st.update(cart=None, quote=None, quote_ok=None)
            _search_and_rank(st, mode)
            return _dump(st)
        c.set_audit_context(st["run_id"], "handoff")
        url = c.get_tools(mode).submit(c.Cart(**st["quote"]))
        c.audit("action", {"tool": "submit", "what": "handoff link created, user pays"}, source_url=url)
        c.write_order(st["user_id"], {"restaurant": st["quote"]["restaurant"], "platform": st["quote"]["platform"],
                                      "total": st["quote"]["total"], "items": [l["item"] for l in st["quote"]["lines"]]})
        st.update(status="done", handoff_url=url,
                  reply="Done. Open the link to pay on the platform. I never touch your card.")
        return _dump(st)

    raise HTTPException(400, "unknown action")


@app.get("/audit/{run_id}")
def audit_trail(run_id: str) -> list[dict]:
    return [e.model_dump(mode="json") for e in c.get_audit(run_id)]


@app.get("/orders/{user_id}")
def orders(user_id: str) -> list[dict]:
    return c.read_orders(user_id)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
