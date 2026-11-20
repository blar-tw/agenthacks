from fastapi.testclient import TestClient

from agent.api import app
from tests.conftest import DEMO

client = TestClient(app)


def test_chat_pauses_then_resumes_on_same_thread():
    r1 = client.post("/chat", json={"message": DEMO, "thread_id": "th-1"}).json()
    assert "reply" in r1 and r1["status"] == "awaiting_selection"
    assert len(r1["options"]) >= 1

    r2 = client.post("/chat", json={"message": "1", "thread_id": "th-1"}).json()
    assert r2["status"] == "awaiting_approval"
    assert r2["run_id"] == r1["run_id"]


def test_other_thread_does_not_resume():
    client.post("/chat", json={"message": DEMO, "thread_id": "th-a"})
    r = client.post("/chat", json={"message": DEMO, "thread_id": "th-b"}).json()
    assert r["status"] == "awaiting_selection"


def test_audit_endpoint_returns_events_in_order():
    r1 = client.post("/chat", json={"message": DEMO, "thread_id": "th-2"}).json()
    client.post("/chat", json={"message": "1", "thread_id": "th-2"})
    client.post("/chat", json={"message": "yes", "thread_id": "th-2"})
    events = client.get(f"/audit/{r1['run_id']}").json()["events"]
    ts = [e["ts"] for e in events]
    assert ts == sorted(ts)
    assert {e["kind"] for e in events} == {"evidence", "inference", "action", "approval"}


def test_audit_unknown_run_is_404():
    assert client.get("/audit/nope").status_code == 404
