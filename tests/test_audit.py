import pytest

from contracts import audit


def test_audit_writes_event_with_hash(mem):
    eid = audit("evidence", {"price": 12.5}, [], run_id="ra", node="normalize", source_url="https://x")
    (e,) = mem.list_audit("ra")
    assert e.id == eid and e.kind == "evidence" and len(e.hash) == 64 and e.source_url == "https://x"


def test_audit_rejects_unknown_kind():
    with pytest.raises(ValueError):
        audit("guess", {}, [], run_id="ra", node="x")


def test_audit_scrubs_secrets(mem):
    audit("action", {"cookie": "abc", "nested": {"api_key": "k"}, "note": "Bearer abcdef123",
                     "ok": "sk-sie-1234567890abcdef"}, [], run_id="rb", node="x")
    (e,) = mem.list_audit("rb")
    assert e.payload["cookie"] == "[redacted]" and e.payload["nested"]["api_key"] == "[redacted]"
    assert "abcdef123" not in str(e.payload) and "sk-sie" not in str(e.payload)
