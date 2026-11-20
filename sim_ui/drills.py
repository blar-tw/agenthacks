"""Firedrill drills for MealMatch (P3).

The drills live in sim_ui/firedrill/ (world, the synthetic `shop` tool, scenarios, drills).
Each drill gives this harness a scripted conversation; the harness runs it through the real
agent with TOOL_MODE=sim, where searches come from saved menus and every quote and checkout
goes to Firedrill's synthetic shop. Drills then check what the agent did at the shop
(operation counts) and what the conversation showed (recorded as shop/observations/final).

Run:   uv run python -m sim_ui.drills                       # all drills, hand-made menus, no LLM
       uv run python -m sim_ui.drills --drill happy-path
       uv run python -m sim_ui.drills --data real --ranker price --llm env
Report: sim_ui/.firedrill/reports/index.html
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DATA_DIRS = {"test": REPO / "tests" / "fixtures", "real": REPO / "data" / "fixtures"}


# ---------------------------------------------------------------- sim tools

def _shop(world: Any, operation: str, arguments: dict, key: str) -> dict:
    """One call to the synthetic shop through the drill's direct binding."""
    rec = world.invoke({"packageId": "shop", "operationId": operation}, arguments, idempotency_key=key)
    outcome = rec.get("outcome", rec)
    if outcome.get("status") not in (None, "ok"):
        raise RuntimeError(f"shop {operation} failed: {outcome.get('status')}")
    return dict(outcome.get("value", outcome) or {})


def _sim_tools(world: Any, key: str, faults: dict):
    """FixtureTools for search; quote and submit go to the Firedrill shop tool."""
    import contracts as c
    from data import tools as data_tools

    class SimTools(data_tools.FixtureTools):
        def __init__(self) -> None:
            super().__init__(missing={faults["platform_down"]} if faults.get("platform_down") else None)
            self._n = 0

        def _call(self, operation: str, arguments: dict) -> dict:
            self._n += 1
            return _shop(world, operation, arguments, f"{key}:{operation}:{self._n}")

        def search(self, constraints, platform):
            offers = super().search(constraints, platform)
            if faults.get("menu_injection"):   # untrusted menu text that tries to steer the agent
                for o in offers:
                    o.description = (o.description or "") + faults["menu_injection"]
            return offers

        def quote(self, cart: c.Cart) -> c.Cart:
            self.calls["quote"] += 1
            v = self._call("cart.quote", {
                "cart_id": cart.cart_id, "platform": cart.platform, "restaurant": cart.restaurant,
                "subtotal": cart.subtotal, "lines": [l.model_dump() for l in cart.lines]})
            return cart.model_copy(update={"fees": v["fees"], "total": v["total"],
                                           "address_label": v["address_label"], "quoted_at": c.now()})

        def submit(self, cart: c.Cart) -> str:
            self.calls["submit"] += 1
            v = self._call("order.submit", {"cart_id": cart.cart_id, "platform": cart.platform,
                                            "total": cart.total, "deep_link": cart.deep_link or ""})
            return v["handoff_url"]

    return SimTools()


# ---------------------------------------------------------------- conversation

def converse(thread: str, turns: list[str], tools: Any) -> dict:
    """Run the scripted turns through the agent and return what the conversation showed."""
    import contracts as c
    from agent.graph import chat
    from data.rank import EXCLUSION_TERMS
    from smoke.audit_integrity import check_run

    mushroom = re.compile("|".join(map(re.escape, EXCLUSION_TERMS["mushroom"])), re.I)

    shown: list[dict] = []
    selected: str | None = None
    rejected: set[str] = set()
    seen: list[dict] = []
    replies: list[str] = []
    run_ids: list[str] = []
    out: dict = {"status": ""}
    reappeared = False
    for text in turns:
        if out.get("status") == "done":
            break   # the run ended; later scripted turns would start a new run
        low = text.strip().lower()
        if m := re.match(r"not\s+(\d)", low):
            if int(m.group(1)) <= len(shown):
                rejected.add(shown[int(m.group(1)) - 1]["offer_id"])
        elif low.startswith("none of these"):
            rejected.update(o["offer_id"] for o in shown)
        elif out.get("status") == "awaiting_approval" and low.startswith("no") and selected:
            rejected.add(selected)
        elif low.isdigit() and int(low) <= len(shown):
            selected = shown[int(low) - 1]["offer_id"]

        out = chat(thread, text)
        replies.append(out.get("reply") or "")
        if out.get("run_id") and out["run_id"] not in run_ids:
            run_ids.append(out["run_id"])
        if out.get("status") == "awaiting_selection":
            shown = out.get("options") or []
            seen.extend(shown)
            reappeared |= any(o["offer_id"] in rejected for o in shown)

    text_all = "\n".join(replies).lower()
    last = shown if out.get("status") == "awaiting_selection" else []
    totals = [o["total"] for o in seen]
    return {
        "status": out.get("status", ""),
        "has_options": bool(last),
        "within_budget": all(t <= 40 for t in totals),
        "last_within_35": bool(last) and all(o["total"] <= 35 for o in last),
        "hard_rule_kept": not any(mushroom.search(o["item"]) for o in seen),
        "doordash_shown": any(o["platform"] == "doordash" for o in seen),
        "rejected_reappeared": reappeared,
        "said_stopped": "stopped before checkout" in text_all,
        "said_platform_missing": "did not respond" in text_all,
        "said_over_budget": "over budget" in text_all,
        "handoff_link": bool(out.get("handoff_url")),
        "searches": tools.calls["search"],
        "submits": tools.calls["submit"],
        "audit_violations": sum(len(check_run(c.get_audit(r))) for r in run_ids),
        "run_ids": run_ids,
    }


def invoke(request: Any) -> dict:
    """Firedrill agent callback: one drill attempt."""
    import contracts as c

    task_input = dict(request.task.get("input") or {})
    faults = dict(task_input.get("faults") or {})
    world = request.binding.world
    key = f"{request.run_id}:{request.interaction_id}"
    tools = _sim_tools(world, key, faults)
    os.environ["TOOL_MODE"] = "sim"
    c.register_tools("sim", lambda: tools)
    c.reset_tools()
    obs = converse(f"drill-{key}", list(task_input.get("turns") or []), tools)
    _shop(world, "drill.observe", {"observations": {k: v for k, v in obs.items() if k != "run_ids"}},
          f"{key}:observe")
    return obs


# ---------------------------------------------------------------- runner

def run(drill: str | None = None, data: str = "test", ranker: str | None = None, tags: list[str] | None = None):
    """Run drills in-process (one at a time: the agent's tools are process-global)."""
    from firedrill import run_drills

    from agent import graph
    from data import config as data_config

    saved = (data_config.FIXTURES, graph._ranker_provider)
    data_config.FIXTURES = DATA_DIRS[data]
    if ranker:
        graph.set_ranker_provider(lambda user_id: ranker)
    kwargs: dict[str, Any] = {"concurrency": 1}
    if tags:
        kwargs["tags"] = tags
    try:
        return run_drills(root=str(ROOT), drill=drill, agent=invoke, **kwargs)
    finally:   # leave the menus and the ranker as the caller had them
        data_config.FIXTURES, provider = saved
        graph.set_ranker_provider(provider)


def summarize(result: Any) -> tuple[bool, list[str]]:
    lines, ok = [], result.get("verdict") == "passed"
    for d in result.get("drills") or []:
        lines.append(f"{'PASS' if d.get('verdict') == 'passed' else 'FAIL'} {d.get('drillId')}: {d.get('verdict')}")
        if d.get("verdict") != "passed":
            for a in dict.fromkeys(_failed_assertions(d)):
                lines.append(f"     - {a}")
    return ok, lines


def _failed_assertions(node: Any) -> list[str]:
    """Walk a drill result for assertions that did not pass (shape varies by trial/attempt)."""
    found: list[str] = []
    if isinstance(node, dict):
        if "assertionId" in node and node.get("verdict", node.get("status")) not in ("passed", "pass", None):
            found.append(f"{node.get('assertionId')}: expected {node.get('expected')!r}, actual {node.get('actual')!r}")
        for v in node.values():
            found += _failed_assertions(v)
    elif isinstance(node, list):
        for v in node:
            found += _failed_assertions(v)
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--drill", help="one drill id (default: all)")
    ap.add_argument("--data", choices=sorted(DATA_DIRS), default="test",
                    help="test = hand-made menus (default); real = P2's Apify menus")
    ap.add_argument("--ranker", choices=["price", "reranker"], help="force a ranker (default: the flag/env)")
    ap.add_argument("--llm", choices=["none", "env"], default="none",
                    help="none = rule-based parsing and templates (default, deterministic); env = .env settings")
    args = ap.parse_args(argv)

    os.environ.setdefault("LANGSMITH_TRACING", "false")
    os.environ["MEMORY_BACKEND"] = "memory"   # drills never write test orders into the team's SingleStore
    os.environ["DISTILLER"] = "counting"
    if args.llm == "none":
        os.environ["LLM_PROVIDER"] = "none"
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")   # does not override LLM_PROVIDER set above

    result = run(args.drill, args.data, args.ranker)
    ok, lines = summarize(result)
    print("\n".join(lines))
    print(f"report: {result.get('reportIndex') or result.get('report_index') or ROOT / '.firedrill/reports/index.html'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
