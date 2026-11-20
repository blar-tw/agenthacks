from fastapi.testclient import TestClient

import contracts as c
from sim_ui.mock_agent import app


def test_happy_path_cites_evidence_and_approves_before_submit():
    c.set_store(c.InMemoryStore())
    c.reset_tools()
    cl = TestClient(app)
    r = cl.post("/chat", json={"message": "spicy noodles for 2 under $40 no pork"}).json()
    assert r["status"] == "awaiting_selection" and r["ranked"]
    assert all("pork" not in (o["offer"]["item"] + o["offer"]["description"]).lower() for o in r["ranked"])
    tid, oid = r["thread_id"], r["ranked"][0]["offer"]["offer_id"]

    r = cl.post("/chat", json={"thread_id": tid, "action": {"type": "select", "offer_id": oid}}).json()
    assert r["status"] == "awaiting_approval" and r["quote_ok"]

    r = cl.post("/chat", json={"thread_id": tid, "action": {"type": "approve"}}).json()
    assert r["status"] == "done" and r["handoff_url"]

    ids = {e["event_id"] for e in r["audit"]}
    evidence = {e["event_id"] for e in r["audit"] if e["kind"] == "evidence"}
    for e in r["audit"]:
        assert set(e["evidence_ids"]) <= ids
        if e["kind"] == "inference" and e["node"] != "parse_request":
            assert e["evidence_ids"] and set(e["evidence_ids"]) <= evidence
    kinds = [(e["kind"], e["payload"].get("tool")) for e in r["audit"]]
    assert kinds.index(("action", "submit")) > max(i for i, k in enumerate(kinds) if k[0] == "approval")


def test_approve_without_cart_is_refused():
    cl = TestClient(app)
    r = cl.post("/chat", json={"message": "noodles"}).json()
    assert cl.post("/chat", json={"thread_id": r["thread_id"], "action": {"type": "approve"}}).status_code == 409
