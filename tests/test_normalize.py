import asyncio

from contracts import Offer
from data import actors, nodes
from data.normalize import normalize_results, parse_minutes, parse_money


def _results():
    return {p: actors.load_fixture(p) for p in ("ubereats", "doordash")}


def test_parse_helpers():
    assert parse_money("$2.99") == 2.99
    assert parse_money("Free delivery") == 0.0
    assert parse_money(None) is None
    assert parse_minutes("25-35 min") == 35
    assert parse_minutes(32) == 32


def test_fixtures_parse_into_offers():
    offers = normalize_results(_results())
    assert all(isinstance(o, Offer) for o in offers)
    ue = [o for o in offers if o.platform == "ubereats"]
    dd = [o for o in offers if o.platform == "doordash"]
    assert len(ue) == 9          # the failed store row is skipped
    assert len(dd) == 9
    sg = next(o for o in dd if o.item == "Dan Dan Noodles")
    assert sg.fees == 2.99 and sg.eta_min == 32 and sg.address.startswith("1234 Clement")
    assert sg.source_url.startswith("https://www.doordash.com/")
    # The Uber Eats Actor reports no delivery fee or ETA.
    assert all(o.fees is None and o.eta_min is None for o in ue)
    assert len({o.id for o in offers}) == len(offers)


def test_failed_platform_gives_no_offers():
    res = _results()
    res["doordash"] = res["doordash"].model_copy(update={"error": "timed out"})
    assert {o.platform for o in normalize_results(res)} == {"ubereats"}


def test_normalize_node_writes_snapshots_and_evidence(mem):
    state = {"run_id": "r1", "search_results": _results()}
    out = nodes.normalize(state)
    offers = out["offers"]
    assert len(mem.snapshots) == len(offers)
    ev = {e.id: e for e in mem.list_audit("r1")}
    assert all(o.evidence_id in ev and ev[o.evidence_id].kind == "evidence" for o in offers)
    assert all(ev[o.evidence_id].payload["scraped_at"] for o in offers)


def test_scrape_runs_both_platforms_and_records_missing(mem):
    from data.tools import FixtureShopTools, set_tools
    set_tools(FixtureShopTools(missing={"doordash"}))
    out = asyncio.run(nodes.scrape({"run_id": "r2"}))
    assert out["missing_platforms"] == ["doordash"]
    assert out["search_results"]["ubereats"].items
    nodes_seen = {e.node for e in mem.list_audit("r2")}
    assert nodes_seen == {"scrape_ubereats", "scrape_doordash"}
