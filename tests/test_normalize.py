import contracts as c
from conftest import scraped_state
from data import actors
from data.normalize import UNKNOWN_FEES, offers_from_result, parse_minutes, parse_money
from data.tools import FixtureTools


def test_parse_helpers():
    assert parse_money("$2.99") == 2.99
    assert parse_money("Free delivery") == 0.0
    assert parse_money(None) is None
    assert parse_minutes("25-35 min") == 35
    assert parse_minutes(32) == 32


def test_fixtures_parse_into_contract_offers():
    ue = offers_from_result(actors.load_fixture("ubereats"))
    dd = offers_from_result(actors.load_fixture("doordash"))
    assert all(isinstance(o, c.Offer) for o in ue + dd)
    assert len(ue) == 9 and len(dd) == 9            # the failed Uber Eats store row is skipped
    assert {o.source for o in ue} == {"ubereats"} and {o.source for o in dd} == {"doordash"}
    sg = next(o for o in dd if o.item == "Dan Dan Noodles")
    assert sg.fees == 2.99 and sg.eta_min == 32 and sg.restaurant_address.startswith("1234 Clement")
    assert sg.url.startswith("https://www.doordash.com/")
    # The Uber Eats Actor reports no delivery fee or ETA.
    assert all(o.offer_id in UNKNOWN_FEES and o.eta_min is None for o in ue)
    # Stable IDs: the same snapshot parses to the same offer_id.
    assert [o.offer_id for o in offers_from_result(actors.load_fixture("doordash"))] == [o.offer_id for o in dd]


def test_html_entities_are_decoded():
    res = actors.SearchResult(platform="ubereats", scraped_at="2026-11-20T12:00:00+00:00", items=[
        {"storeName": "Z &amp; Y Peking Duck", "itemName": "Fish &amp; Chili", "price": 20}])
    (o,) = offers_from_result(res)
    assert o.restaurant == "Z & Y Peking Duck" and o.item == "Fish & Chili"


def test_failed_platform_gives_no_offers():
    res = actors.load_fixture("doordash").model_copy(update={"error": "timed out"})
    assert offers_from_result(res) == []


def test_scrape_runs_both_platforms(mem):
    state = scraped_state("r1")
    assert state["missing_platforms"] == []
    assert {o["source"] for o in state["offers"]} == {"ubereats", "doordash"}
    assert {e.node for e in c.get_audit("r1") if e.node.startswith("scrape_")} == {"scrape_ubereats", "scrape_doordash"}


def test_scrape_records_a_missing_platform(mem):
    c.register_tools("fixtures", lambda: FixtureTools(missing={"doordash"}))
    state = scraped_state("r2")
    assert state["missing_platforms"] == ["doordash"]
    assert {o["source"] for o in state["offers"]} == {"ubereats"}
    dd = next(e for e in c.get_audit("r2") if e.node == "scrape_doordash")
    assert dd.kind == "evidence" and "TimeoutError" in dd.payload["error"]


def test_normalize_writes_evidence_and_snapshots(mem):
    state = scraped_state("r3")
    ev = {e.event_id: e for e in c.get_audit("r3")}
    for o in state["offers"]:
        e = ev[o["evidence_id"]]
        assert e.kind == "evidence" and e.node == "normalize" and e.payload["scraped_at"]
    assert len(mem.snapshots) == len(state["offers"])
    ue = next(o for o in state["offers"] if o["source"] == "ubereats")
    assert ev[ue["evidence_id"]].payload["fees_known"] is False
