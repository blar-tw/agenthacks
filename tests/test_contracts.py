import contracts as c


def test_stub_tools_search_returns_offers():
    tools = c.get_tools("fixtures")
    offers = tools.search(c.Constraints(raw_text="spicy noodles"), "doordash")
    assert offers and all(isinstance(o, c.Offer) and o.source == "doordash" for o in offers)


def test_audit_returns_id_and_scrubs_secrets():
    c.set_store(c.InMemoryStore())
    eid = c.audit("evidence", {"price": 1.0, "session_cookie": "x", "api_key": "y"}, run_id="r1", node="n")
    events = c.get_audit("r1")
    assert events[0].event_id == eid
    assert events[0].payload == {"price": 1.0}


def test_log_event_scrubs_and_never_raises():
    c.set_event_sink(lambda n, m: 1 / 0)
    c.log_event("x", {"token": "t", "ok": 1})
    assert c.EVENTS[-1] == ("x", {"ok": 1})
    c.set_event_sink(lambda n, m: None)


def test_register_tools_overrides_stub():
    class Fake(c.StubTools):
        pass

    c.register_tools("sim", Fake)
    assert isinstance(c.get_tools("sim"), Fake)
    c.register_tools("sim", c.StubTools)
