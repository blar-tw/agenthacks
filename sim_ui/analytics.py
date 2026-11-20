"""Statsig for MealMatch (P3): the ranker flag and the agent's analytics events.

install() is called once by agent/api.py at startup. With STATSIG_SERVER_KEY set it
- picks each user's ranker from the feature gate STATSIG_RANKER_GATE (default
  `mealmatch_reranker`): on -> "reranker", off -> "price";
- sends every contracts.log_event(...) to Statsig.
A gate Statsig does not know (not created yet, misspelled) or any Statsig error keeps the
default ranker (RANKER, else "reranker") rather than quietly switching everyone to price.
Analytics never breaks the agent, and only scrubbed metadata is sent (contracts.log_event
removes secret keys and values first).
"""
from __future__ import annotations

import atexit
import os
from typing import Any

import contracts
from agent import graph

_BAD_REASONS = {"Unrecognized", "Unsupported", "error"}

STATE: dict[str, Any] = {"installed": False, "gate": None, "default_ranker": None, "error": None}


def gate_name() -> str:
    return os.getenv("STATSIG_RANKER_GATE", "mealmatch_reranker")


def ranker_for(server: Any, user_id: str, default: str) -> str:
    """'reranker' or 'price' from the gate; the default when the gate is unknown or Statsig fails."""
    try:
        from statsig import StatsigUser
        gate = server.get_feature_gate(StatsigUser(user_id=user_id or "demo"), gate_name())
        reason = getattr(getattr(gate, "evaluation_details", None), "reason", None)
        if str(getattr(reason, "value", reason)) in _BAD_REASONS:
            return default
        return "reranker" if gate.get_value() else "price"
    except Exception:
        return default


def send_event(server: Any, name: str, metadata: dict) -> None:
    """One Statsig event; metadata values become short strings. Never raises."""
    try:
        from statsig import StatsigEvent, StatsigUser
        user = StatsigUser(user_id=str(metadata.get("user_id") or "demo"))
        meta = {str(k): str(v)[:300] for k, v in (metadata or {}).items()}
        server.log_event(StatsigEvent(user=user, event_name=name, metadata=meta))
    except Exception:
        pass


def install(server: Any = None) -> bool:
    """Wire the ranker flag and the event sink. Returns False (and changes nothing) without a key."""
    default = os.getenv("RANKER", "reranker")
    if server is None:
        key = os.getenv("STATSIG_SERVER_KEY", "").strip()
        if not key:
            return False
        try:
            from statsig import StatsigOptions, StatsigServer
            server = StatsigServer()
            details = server.initialize(key, StatsigOptions(overall_init_timeout=5.0))
            if not details.init_success:
                STATE["error"] = "initialize failed"
                return False
        except Exception as exc:   # Statsig down or SDK problem: the agent runs on its defaults
            STATE["error"] = type(exc).__name__
            return False
        atexit.register(server.shutdown)
    graph.set_ranker_provider(lambda user_id: ranker_for(server, user_id, default))
    contracts.set_event_sink(lambda name, metadata: send_event(server, name, metadata))
    STATE.update(installed=True, gate=gate_name(), default_ranker=default, error=None)
    return True
