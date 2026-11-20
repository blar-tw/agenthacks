"""P1-owned LangGraph nodes (doc section 7).

Nodes that call interrupt() re-run from their start on resume, so they write
audit events only AFTER interrupt() returns. State changes with side effects
(build_cart, submit_or_handoff) live in their own nodes after each interrupt.
"""

from __future__ import annotations

import os
import re
from datetime import datetime

from langgraph.types import interrupt

from agent.default_nodes import tool_mode
from agent.llm import extract_constraints, read_feedback, summarize_options
from contracts import Constraints, Offer, audit, get_tools, log_event, write_preference

PRICE_JUMP_LIMIT = float(os.getenv("PRICE_JUMP_LIMIT", "0.10"))  # doc 11: block > 10% above estimate
MAX_ROUNDS = 3
SEARCH_FIELDS = ("cuisine", "dish_query")  # a change here needs a new scrape


def _merge_constraints(prev: dict, new: Constraints) -> Constraints:
    """A follow-up like 'actually under $30' keeps the earlier fields it doesn't restate."""
    if not prev:
        return new
    merged = dict(prev)
    defaults = Constraints().model_dump()
    for k, v in new.model_dump().items():
        if k in ("hard_excludes", "soft_prefs"):
            merged[k] = list(dict.fromkeys([*prev.get(k, []), *v]))
        elif v != defaults[k]:
            merged[k] = v
    return Constraints(**merged)


def parse_request(state: dict) -> dict:
    text = state.get("request_text", "")
    req_eid = audit("evidence", {"request_text": text}, source_url="user://chat")
    parsed, parser = extract_constraints(text, state.get("memory"))
    prev = state.get("constraints") or {}
    c = _merge_constraints(prev, parsed)
    memory = state.get("memory") or {}
    stated_new = [r for r in parsed.hard_excludes if r not in memory.get("hard_rules", [])]
    for rule in stated_new:  # only the user's own words create a hard rule (doc 8.4)
        write_preference(state.get("user_id", "demo"),
                         {"text": rule, "kind": "hard", "source": "stated", "confidence": 1.0})
    c.hard_excludes = list(dict.fromkeys([*c.hard_excludes, *memory.get("hard_rules", [])]))
    c.soft_prefs = list(dict.fromkeys([*c.soft_prefs, *memory.get("soft_prefs", [])]))
    cites = [req_eid] + ([memory["evidence_id"]] if memory.get("evidence_id") else [])
    audit("inference", {"constraints": c.model_dump(), "parser": parser, "new_hard_rules": stated_new}, cites)
    return {"constraints": c.model_dump(), "prev_constraints": prev, "request_eid": req_eid}


def route_after_parse(state: dict) -> list[str] | str:
    prev, cur = state.get("prev_constraints") or {}, state.get("constraints") or {}
    same_search = prev and all(prev.get(k) == cur.get(k) for k in SEARCH_FIELDS)
    if state.get("offers") and same_search:
        return "filter_rank"  # constraints changed only in budget/time/rules: no new scrape
    return ["scrape_ubereats", "scrape_doordash"]  # both Actors at the same time


def _reasons(opt: dict, constraints: Constraints, request_eid: str | None) -> list[dict]:
    o = Offer(**opt["offer"])
    when = o.scraped_at.strftime("%H:%M") if isinstance(o.scraped_at, datetime) else str(o.scraped_at)[11:16]
    ev = [o.evidence_id] if o.evidence_id else []
    reasons = [{"kind": "evidence", "text": f"${opt['total']:.2f} total on {o.source} "
                                            f"(${o.price:.2f} x {constraints.people} + ${o.fees:.2f} fees), "
                                            f"scraped {when}", "evidence_ids": ev}]
    if o.eta_min is not None:
        reasons.append({"kind": "evidence", "text": f"About {o.eta_min} min delivery", "evidence_ids": ev})
    text = f"{o.item} {o.description}".lower()
    for pref in constraints.soft_prefs:
        if pref.lower().rstrip("s") in text:
            reasons.append({"kind": "inference", "text": f"Matches your preference for {pref}",
                            "evidence_ids": ev + ([request_eid] if request_eid else [])})
    if constraints.hard_excludes:
        reasons.append({"kind": "inference", "text": f"Contains none of: {', '.join(constraints.hard_excludes)}",
                        "evidence_ids": ev})
    if opt.get("match_id"):
        reasons.append({"kind": "inference", "text": "Same dish found on both platforms", "evidence_ids": ev})
    return reasons


def explain(state: dict) -> dict:
    c = Constraints(**state.get("constraints", {}))
    options = state.get("ranked", [])[:3]
    for opt in options:
        opt["reasons"] = _reasons(opt, c, state.get("request_eid"))
    summary, writer = summarize_options(c.model_dump(), options)
    lines = []
    for i, opt in enumerate(options, 1):
        o = opt["offer"]
        lines.append(f"{i}. {o['item']} - {o['restaurant']} ({o['source']}) ${opt['total']:.2f}, "
                     f"{o.get('eta_min')} min")
    missing = state.get("missing_platforms") or []
    note = f"\n({', '.join(missing)} did not respond; showing the other platform.)" if missing else ""
    if options:
        reply = f"{summary}\n" + "\n".join(lines) + note + "\nReply 1-3 to choose, or 'none of these'."
    else:
        near = state.get("near_misses") or []
        trade = "; ".join(f"{d['item']} ({d['why']})" for d in near[:3])
        reply = ("No option meets all your rules. Your hard rules stay in place."
                 + (f" Closest: {trade}. Raise the budget or the time limit, or try another dish." if trade else "")
                 + note)
    cites = sorted({e for opt in options for r in opt["reasons"] for e in r["evidence_ids"]}
                   | {d["evidence_id"] for d in (state.get("near_misses") or []) if d.get("evidence_id")})
    if not cites and state.get("request_eid"):
        cites = [state["request_eid"]]
    audit("inference", {"summary": summary, "writer": writer, "options": [o["offer"]["offer_id"] for o in options],
                        "reasons": [o["reasons"] for o in options]}, cites)
    log_event("options_shown", {"run_id": state.get("run_id"), "count": len(options),
                                "ranker": state.get("ranker", "price")})
    if state.get("notice"):
        reply = f"{state['notice']}\n{reply}"
    return {"ranked": state.get("ranked", []), "shown": options, "reply": reply, "notice": ""}


_PICK = re.compile(r"^\s*(?:option\s*|#)?([1-9])\b", re.I)


def select_option(state: dict) -> dict:
    shown = state.get("shown") or []
    answer = str(interrupt({"type": "select_option", "message": state.get("reply", ""),
                            "options": [{"n": i + 1, "offer_id": o["offer"]["offer_id"], "item": o["offer"]["item"],
                                         "restaurant": o["offer"]["restaurant"], "platform": o["offer"]["source"],
                                         "total": o["total"], "reasons": o.get("reasons", [])}
                                        for i, o in enumerate(shown)]}))
    m = _PICK.match(answer)
    if m and int(m.group(1)) <= len(shown):
        sel = shown[int(m.group(1)) - 1]
        eid = audit("approval", {"decision": "select", "offer_id": sel["offer"]["offer_id"], "text": answer})
        log_event("option_selected", {"run_id": state.get("run_id"), "rank": int(m.group(1))})
        return {"selected": sel, "feedback": "", "user_event_id": eid}
    eid = audit("approval", {"decision": "none_of_these", "text": answer})
    return {"selected": None, "feedback": answer, "user_event_id": eid}


def route_after_select(state: dict) -> str:
    return "build_cart" if state.get("selected") else "handle_feedback"


def build_cart(state: dict) -> dict:
    sel = state["selected"]
    offer = Offer(**sel["offer"])
    people = Constraints(**state.get("constraints", {})).people
    tools = get_tools(tool_mode())
    cart = tools.build_cart(offer, people)
    audit("action", {"tool": "build_cart", "cart_id": cart.cart_id, "platform": cart.platform,
                     "items": [l.model_dump() for l in cart.lines]}, [offer.evidence_id] if offer.evidence_id else [])
    quote = tools.quote(cart)
    q_eid = audit("evidence", {"tool": "quote", "cart_id": quote.cart_id, "subtotal": quote.subtotal,
                               "fees": quote.fees, "total": quote.total, "address_label": quote.address_label},
                  source_url=quote.deep_link or None)
    q = quote.model_dump(mode="json")
    q["evidence_id"] = q_eid
    return {"cart": cart.model_dump(mode="json"), "quote": q}


def verify_quote(state: dict) -> dict:
    sel, q = state["selected"], state["quote"]
    offer = Offer(**sel["offer"])
    estimate = sel["total"]
    problems = []
    if q["total"] > estimate * (1 + PRICE_JUMP_LIMIT):
        problems.append(f"total ${q['total']:.2f} is {100 * (q['total'] / estimate - 1):.0f}% above "
                        f"the ${estimate:.2f} estimate")
    if not q.get("address_label"):
        problems.append("no delivery address on the cart")
    if [l["item"] for l in q.get("lines", [])] != [offer.item]:
        problems.append("cart items differ from the selected dish")
    cites = [x for x in (offer.evidence_id, q.get("evidence_id")) if x]
    audit("inference", {"quote_ok": not problems, "problems": problems, "estimate": estimate,
                        "quoted_total": q["total"]}, cites)
    if problems:
        log_event("quote_blocked", {"run_id": state.get("run_id"), "problems": len(problems)})
        return {"quote_ok": False, "rejected": [*state.get("rejected", []), offer.offer_id],
                "notice": f"I stopped before checkout: {'; '.join(problems)}. Here are other options."}
    return {"quote_ok": True}


def route_after_verify(state: dict) -> str:
    return "approve_purchase" if state.get("quote_ok") else "filter_rank"


# Explicit approval only: the whole reply must be a plain yes, optionally with a polite tail.
# "yes please" / "looks good, go ahead" approve; "ok but cancel" / "yeah no" do not.
_YES_WORD = r"(?:y|yes|yep|yeah|approve|approved|confirm|confirmed|ok|okay|sure|go ahead|do it|looks good|sounds good)"
_TAIL = r"(?:please|thanks|thank you|go ahead|do it|order it|let's do it)"
_YES = re.compile(rf"\s*{_YES_WORD}(?:[\s,!.]+{_TAIL})*\s*[.!]*\s*", re.I)


def approve_purchase(state: dict) -> dict:
    q, sel = state["quote"], state["selected"]
    msg = (f"Approve {sel['offer']['item']} from {sel['offer']['restaurant']} on {q['platform']} "
           f"for ${q['total']:.2f} (incl. ${q['fees']:.2f} fees)? You pay on {q['platform']}. Reply yes or no.")
    answer = str(interrupt({"type": "approve_purchase", "message": msg, "cart": q}))
    approved = bool(_YES.fullmatch(answer))
    eid = audit("approval", {"approved": approved, "text": answer, "cart_id": q["cart_id"], "total": q["total"]},
                [q["evidence_id"]] if q.get("evidence_id") else [])
    log_event("purchase_approved" if approved else "purchase_rejected", {"run_id": state.get("run_id")})
    return {"approval": approved, "approval_event_id": eid, "user_event_id": eid,
            "feedback": "" if approved else answer}


def route_after_approve(state: dict) -> str:
    return "submit_or_handoff" if state.get("approval") is True else "handle_feedback"


def submit_or_handoff(state: dict) -> dict:
    if state.get("approval") is not True or not state.get("approval_event_id"):
        audit("inference", {"blocked": "submit without approval"}, [state.get("user_event_id") or "none"])
        return {"reply": "I need your approval before checkout."}
    from contracts import Cart
    cart = Cart(**{k: v for k, v in state["quote"].items() if k != "evidence_id"})
    url = get_tools(tool_mode()).submit(cart)
    audit("action", {"tool": "submit", "handoff_url": url, "cart_id": cart.cart_id, "total": cart.total},
          [state["approval_event_id"]], source_url=url)
    log_event("handoff", {"run_id": state.get("run_id"), "platform": cart.platform, "total": cart.total})
    return {"handoff_url": url,
            "reply": f"Approved. Open this link to pay on {cart.platform}: {url}\n"
                     f"MealMatch never pays for you."}


def handle_feedback(state: dict) -> dict:
    fb = read_feedback(state.get("feedback", ""), state.get("shown"))
    if state.get("selected"):
        newly = [state["selected"]["offer"]["offer_id"]]
    else:
        newly = [o["offer"]["offer_id"] for o in (state.get("shown") or [])] if fb["route"] == "more" else []
    rejected = list(dict.fromkeys([*state.get("rejected", []), *newly]))
    c = Constraints(**state.get("constraints", {}))
    for rule in fb["new_hard_rules"]:
        write_preference(state.get("user_id", "demo"),
                         {"text": rule, "kind": "hard", "source": "stated", "confidence": 1.0})
        c.hard_excludes = list(dict.fromkeys([*c.hard_excludes, rule]))
    rnd = state.get("round", 0) + 1
    audit("inference", {"route": fb["route"], "reason": fb["reason"], "reader": fb["reader"], "rejected": newly,
                        "new_hard_rules": fb["new_hard_rules"], "round": rnd},
          [state.get("user_event_id")] if state.get("user_event_id") else [])
    out = {"rejected": rejected, "route": fb["route"], "round": rnd, "selected": None,
           "approval": None, "constraints": c.model_dump()}
    if fb["route"] == "new":
        out["request_text"] = fb["new_request"] or state.get("feedback", "")
    return out


def next_step(state: dict) -> dict:
    route = state.get("route", "more")
    if state.get("round", 0) >= MAX_ROUNDS and route != "done":
        route = "done"
        return {"route": route, "reply": "We tried 3 rounds. Start a new request any time."}
    if route == "done":
        return {"route": route, "reply": "Okay, stopping here. Nothing was ordered."}
    return {"route": route}


def route_next(state: dict) -> str:
    return {"more": "filter_rank", "new": "parse_request"}.get(state.get("route", "done"), "persist_learn")
