"""P1 graph + P2 data layer together: fallback paths still keep the audit trail whole."""
import contracts
from agent.graph import chat, get_graph, run_fixture_demo
from data import sie
from smoke.audit_integrity import check_run
from tests.conftest import DEMO


class BrokenSIE:
    def embed(self, texts, is_query=False):
        raise RuntimeError("503")

    def score(self, query, items):
        raise RuntimeError("503")


def test_graph_uses_p2_single_scrape_node():
    names = set(get_graph().get_graph().nodes)
    assert "scrape" in names and "scrape_ubereats" not in names


def test_sie_down_still_cites_evidence_everywhere(tools):
    sie.set_sie(BrokenSIE())
    run_id = run_fixture_demo()
    assert tools.calls["submit"] == 1
    assert check_run(contracts.get_audit(run_id)) == []


def test_no_options_still_cites_evidence():
    r = chat("t", "Spicy noodles for two under $10 within 40 minutes, no mushrooms")
    assert r["options"] == [] and "over budget" in r["reply"]
    assert check_run(contracts.get_audit(r["run_id"])) == []


def test_demo_shows_options_from_p2_fixtures():
    r = chat("t", DEMO)
    assert r["options"] and all(o["total"] <= 40 for o in r["options"])
    assert all("mushroom" not in o["item"].lower() for o in r["options"])


def test_demo_has_no_off_topic_dish():
    r = chat("t", DEMO)
    assert r["options"] and not any("salad" in o["item"].lower() for o in r["options"])
    removed = [e for e in contracts.get_audit(r["run_id"])
               if e.kind == "inference" and e.payload.get("what") == "removed options that break a rule"]
    assert any(x["reason"].startswith("not what was asked for") for x in removed[0].payload["removed"])


def test_relevance_gate_turns_off_when_nothing_matches():
    from data import actors, rank
    from data.normalize import offers_from_result
    offers = [o for p in ("ubereats", "doordash") for o in offers_from_result(actors.load_fixture(p))]
    pizza = contracts.Constraints(dish_query="pizza", people=1)
    assert len(rank.filter_offers(offers, pizza, {}, [], []).kept) > 0
    salad = contracts.Constraints(dish_query="salad", people=1)
    assert {r.offer.item for r in rank.filter_offers(offers, salad, {}, [], []).kept} == {"Caesar Salad"}


def test_off_topic_dish_is_not_a_near_miss():
    r = chat("t", "Spicy noodles for two under $10 within 40 minutes, no mushrooms")
    assert "over budget" in r["reply"] and "Salad" not in r["reply"]


def test_gate_matches_the_dish_not_the_restaurant_or_cuisine():
    from data import actors, rank
    from data.normalize import offers_from_result
    offers = [o for p in ("ubereats", "doordash") for o in offers_from_result(actors.load_fixture(p))]
    for q in ("spicy noodles", "spicy Chinese food"):
        cons = contracts.Constraints(cuisine="Chinese", dish_query=q, people=2)
        kept = {r.offer.item for r in rank.filter_offers(offers, cons, {}, [], []).kept}
        assert not any("Bok Choy" in k for k in kept), q   # at "Sichuan Garden", but not spicy


def test_rejecting_a_dish_also_drops_its_twin_on_the_other_platform():
    from data import rank
    chat("t", DEMO)
    state = get_graph().get_state({"configurable": {"thread_id": "t"}}).values
    twins = [m["offer_ids"] for m in state["matches"]]
    assert twins, "fixtures should contain matched dishes"
    a, b = twins[0]
    offers = [contracts.Offer(**o) for o in state["offers"]]
    cons = contracts.Constraints(**state["constraints"])
    kept = {k.offer.offer_id for k in rank.filter_offers(offers, cons, {}, [contracts.Match(**m) for m in state["matches"]], [a]).kept}
    assert a not in kept and b not in kept
