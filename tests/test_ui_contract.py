"""The web UI's /chat contract (sim_ui/README.md) served by the real agent."""
from fastapi.testclient import TestClient

from agent.api import app
from smoke.audit_integrity import check_run
import contracts
from tests.conftest import DEMO

client = TestClient(app)


def post(**body):
    return client.post("/chat", json={"thread_id": "ui", "user_id": "demo", **body})


def test_ui_happy_path_select_approve_handoff(tools):
    r = post(message=DEMO).json()
    assert r["status"] == "awaiting_selection" and r["thread_id"] == "ui"
    assert r["constraints"]["budget_total"] == 40 and r["audit"]
    first = r["ranked"][0]
    assert {"offer", "total", "reasons"} <= set(first) and first["reasons"][0]["evidence_ids"]

    r = post(action={"type": "select", "offer_id": first["offer"]["offer_id"]}).json()
    assert r["status"] == "awaiting_approval" and r["ranked"] == []
    assert r["quote_ok"] is True and r["quote"]["lines"] and r["quote"]["platform"] == first["offer"]["source"]

    r = post(action={"type": "approve"}).json()
    assert r["status"] == "done" and r["handoff_url"] and r["quote"]["total"] > 0
    assert tools.calls["submit"] == 1
    assert check_run(contracts.get_audit(r["run_id"])) == []


def test_not_this_rejects_only_that_option():
    r = post(message=DEMO).json()
    keep, drop = r["ranked"][0]["offer"]["offer_id"], r["ranked"][1]["offer"]["offer_id"]
    r = post(action={"type": "reject", "offer_id": drop, "reason": "too slow"}).json()
    ids = [o["offer"]["offer_id"] for o in r["ranked"]]
    assert r["status"] == "awaiting_selection" and drop not in ids and keep in ids


def test_decline_does_not_submit(tools):
    r = post(message=DEMO).json()
    post(action={"type": "select", "offer_id": r["ranked"][0]["offer"]["offer_id"]})
    r = post(action={"type": "decline", "reason": "too expensive"}).json()
    assert r["status"] != "done" and tools.calls["submit"] == 0


def test_action_at_the_wrong_step_is_refused():
    assert post(action={"type": "approve"}).status_code == 409
    post(message=DEMO)
    assert post(action={"type": "select", "offer_id": "off_nope"}).status_code == 400
    assert post(action={"type": "approve"}).status_code == 409


def test_page_and_orders_endpoints():
    assert "<html" in client.get("/").text.lower()
    assert client.get("/orders/demo").status_code == 200
