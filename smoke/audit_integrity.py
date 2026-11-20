"""Audit-trail integrity checks. Usage: audit_integrity.py [--run-id ID] [--selftest]"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import contracts  # noqa: E402
from _common import Skip, main  # noqa: E402


def check_run(events: list[contracts.AuditEvent]) -> list[str]:
    violations: list[str] = []
    ids = {e.event_id for e in events}
    ordered = sorted(events, key=lambda e: e.ts)
    for e in ordered:
        if e.kind == "inference" and not e.evidence_ids:
            violations.append(f"inference {e.event_id} ({e.node}) has no evidence_ids")
        for eid in e.evidence_ids:
            if eid not in ids:
                violations.append(f"{e.kind} {e.event_id} cites missing evidence id {eid}")
    for i, e in enumerate(ordered):
        if e.kind == "action" and e.payload.get("tool") == "submit":
            if not any(p.kind == "approval" and p.payload.get("approved") is True for p in ordered[:i]):
                violations.append(f"submit action {e.event_id} not preceded by an approved approval")
    return violations


def selftest() -> str:
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def ev(i, kind, dt, payload=None, cites=None):
        return contracts.AuditEvent(event_id=i, run_id="r", node="n", kind=kind, payload=payload or {},
                                    evidence_ids=cites or [], ts=t0 + timedelta(seconds=dt))

    good = [ev("e1", "evidence", 0), ev("i1", "inference", 1, cites=["e1"]),
            ev("a1", "approval", 2, {"approved": True}), ev("s1", "action", 3, {"tool": "submit"})]
    assert check_run(good) == [], check_run(good)

    uncited = [ev("e1", "evidence", 0), ev("i1", "inference", 1)]
    v = check_run(uncited)
    assert len(v) == 1 and "no evidence_ids" in v[0], v

    dangling = [ev("i1", "inference", 1, cites=["nope"])]
    assert any("missing evidence" in x for x in check_run(dangling))

    early = [ev("s1", "action", 1, {"tool": "submit"}), ev("a1", "approval", 2, {"approved": True})]
    v = check_run(early)
    assert len(v) == 1 and "submit" in v[0], v

    denied = [ev("a1", "approval", 0, {"approved": False}), ev("s1", "action", 1, {"tool": "submit"})]
    assert len(check_run(denied)) == 1
    return "5 selftest cases ok"


def run_check() -> str:
    args = sys.argv[1:]
    run_id = args[args.index("--run-id") + 1] if "--run-id" in args else None
    store = None
    if run_id is None:
        try:
            from agent import graph  # type: ignore
            demo = graph.run_fixture_demo
        except Exception:  # noqa: BLE001
            raise Skip("agent.graph.run_fixture_demo not available yet")
        run_id = demo()
    store = contracts.get_store() if hasattr(contracts, "get_store") else None
    if store is None:
        raise Skip("no contracts.get_store() to load audit events")
    events = store.get_audit(run_id)
    if not events:
        raise RuntimeError(f"no audit events for run {run_id}")
    v = check_run(events)
    if v:
        raise RuntimeError("; ".join(v))
    return f"run {run_id}: {len(events)} events, no violations"


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        main([("audit_selftest", selftest)])
    else:
        main([("audit_integrity", run_check)])
