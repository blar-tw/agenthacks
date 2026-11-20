"""MealMatch LangGraph (doc section 7): 18 nodes, a checkpointer, 2 interrupts.

Nodes owned by P2/P3 are resolved at build time: a function with the node's
name in data/nodes.py or sim_ui/nodes.py replaces the default in
agent/default_nodes.py, so merging their branches needs no edit here.
"""

from __future__ import annotations

import importlib
import os
import uuid
from typing import Annotated, Any, Callable, Optional

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from agent import default_nodes, nodes
from contracts import AgentState, log_event, set_audit_context


def _merge_platforms(old: Optional[dict], new: Optional[dict]) -> dict:
    if new is None:  # a new run resets the scrape results
        return {}
    return {**(old or {}), **new}


class GraphState(AgentState, total=False):
    raw_by_platform: Annotated[dict, _merge_platforms]
    near_misses: list[dict]
    shown: list[dict]
    request_eid: Optional[str]
    user_event_id: Optional[str]
    approval_event_id: Optional[str]
    notice: str


P1_NODES = ["parse_request", "explain", "select_option", "build_cart", "verify_quote", "approve_purchase",
            "submit_or_handoff", "handle_feedback", "next_step"]
DELEGATED_NODES = ["load_memory", "scrape_ubereats", "scrape_doordash", "normalize", "match_offers",
                   "filter_rank", "persist_learn", "distill_preferences", "audit_report"]
ALL_NODES = DELEGATED_NODES[:1] + P1_NODES[:1] + DELEGATED_NODES[1:6] + P1_NODES[1:] + DELEGATED_NODES[6:]
NODE_SOURCES: dict[str, str] = {}

_ranker_provider: Callable[[str], str] = lambda user_id: os.getenv("RANKER", "price")  # noqa: E731


def set_ranker_provider(fn: Callable[[str], str]) -> None:
    """P3 wires the Statsig flag here: fn(user_id) -> 'price' | 'reranker'."""
    global _ranker_provider
    _ranker_provider = fn


def _resolve(name: str) -> Callable:
    for modname in ("data.nodes", "sim_ui.nodes"):
        try:
            mod = importlib.import_module(modname)
        except ModuleNotFoundError as e:
            if e.name != modname:
                raise
            continue
        fn = getattr(mod, name, None)
        if callable(fn):
            NODE_SOURCES[name] = modname
            return fn
    NODE_SOURCES[name] = "agent.default_nodes"
    return getattr(default_nodes, name)


def _with_audit(name: str, fn: Callable) -> Callable:
    def run(state: dict) -> dict:
        token = set_audit_context(state.get("run_id", "no-run"), name)
        try:
            return fn(state)
        finally:
            token.var.reset(token)
    run.__name__ = name
    return run


def build_graph(checkpointer: Any = None):
    g = StateGraph(GraphState)
    for name in P1_NODES:
        g.add_node(name, _with_audit(name, getattr(nodes, name)))
    for name in DELEGATED_NODES:
        g.add_node(name, _with_audit(name, _resolve(name)))

    g.add_edge(START, "load_memory")
    g.add_edge("load_memory", "parse_request")
    g.add_conditional_edges("parse_request", nodes.route_after_parse,
                            ["scrape_ubereats", "scrape_doordash", "filter_rank"])
    g.add_edge(["scrape_ubereats", "scrape_doordash"], "normalize")  # waits for both (each has a timeout)
    g.add_edge("normalize", "match_offers")
    g.add_edge("match_offers", "filter_rank")
    g.add_edge("filter_rank", "explain")
    g.add_edge("explain", "select_option")
    g.add_conditional_edges("select_option", nodes.route_after_select, ["build_cart", "handle_feedback"])
    g.add_edge("build_cart", "verify_quote")
    g.add_conditional_edges("verify_quote", nodes.route_after_verify, ["approve_purchase", "filter_rank"])
    g.add_conditional_edges("approve_purchase", nodes.route_after_approve, ["submit_or_handoff", "handle_feedback"])
    g.add_edge("submit_or_handoff", "persist_learn")
    g.add_edge("handle_feedback", "next_step")
    g.add_conditional_edges("next_step", nodes.route_next, ["filter_rank", "parse_request", "persist_learn"])
    g.add_edge("persist_learn", "distill_preferences")
    g.add_edge("distill_preferences", "audit_report")
    g.add_edge("audit_report", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())


_GRAPH = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


def reset_graph() -> None:
    global _GRAPH
    _GRAPH = None


def chat(thread_id: str, message: str, user_id: str = "demo") -> dict:
    """One user turn. Resumes a paused run on this thread, or starts a new run."""
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}
    snap = graph.get_state(config)
    if snap.next and snap.interrupts:
        result = graph.invoke(Command(resume=message), config)
        run_id = snap.values.get("run_id")
    else:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        ranker = _ranker_provider(user_id)
        log_event("run_started", {"run_id": run_id, "ranker": ranker})
        result = graph.invoke({
            "run_id": run_id, "thread_id": thread_id, "user_id": user_id, "request_text": message,
            "constraints": {}, "prev_constraints": {}, "offers": [], "raw_by_platform": None, "matches": [],
            "ranked": [], "shown": [], "rejected": [], "selected": None, "cart": None, "quote": None,
            "approval": None, "approval_event_id": None, "feedback": "", "route": "", "round": 0,
            "reply": "", "handoff_url": "", "ranker": ranker, "near_misses": [], "missing_platforms": [], "notice": "",
        }, config)
    pending = result.get("__interrupt__") or []
    if pending:
        value = pending[0].value
        status = "awaiting_selection" if value.get("type") == "select_option" else "awaiting_approval"
        return {"reply": value.get("message", ""), "run_id": run_id, "status": status,
                "options": value.get("options"), "cart": value.get("cart")}
    return {"reply": result.get("reply", ""), "run_id": run_id, "status": "done",
            "handoff_url": result.get("handoff_url") or None}


DEMO_REQUEST = ("Find me spicy Chinese food for two under $40. I prefer noodles. I do not like mushrooms. "
                "I want the cheapest option that arrives within 40 minutes.")


def run_fixture_demo() -> str:
    """Happy path in-process (used by smoke/audit_integrity.py). Returns the run_id."""
    thread = f"demo_{uuid.uuid4().hex[:8]}"
    first = chat(thread, DEMO_REQUEST)
    chat(thread, "1")
    chat(thread, "yes")
    return first["run_id"]
