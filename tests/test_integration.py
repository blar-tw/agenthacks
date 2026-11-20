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


def test_sides_drinks_and_condiments_are_not_dishes():
    from data import rank

    def off(item):
        return contracts.Offer(restaurant="R", item=item, price=5.0, source="ubereats")
    for side in ["House Hot Mustard 2oz", "Coke", "Diet Coke (健怡)", "Steamed Rice 白米飯", "Lemon Ice Tea",
                 "Signature Chili Bean Sauce", "Side Peking Duck"]:
        assert rank.is_side(off(side)), side
    for dish in ["Eggplant in Garlic Sauce", "Fried Rice w.Preserved Mustard Greens, Minced Pork",
                 "Pork Intestine Wit Chili Sauce", "Tan Tan Noodle 擔擔麵", "Tea Smoked Duck"]:
        assert not rank.is_side(off(dish)), dish


def test_price_ranker_skips_a_cheap_condiment():
    from data import rank
    offers = [contracts.Offer(restaurant="China Live", item="House Hot Mustard 2oz", price=6.95, source="ubereats"),
              contracts.Offer(restaurant="Z&Y", item="Spicy Tan Tan Noodle", price=18.0, source="ubereats")]
    cons = contracts.Constraints(dish_query="spicy noodles", people=2)
    fr = rank.filter_offers(offers, cons, {}, [], [])
    assert [r.offer.item for r in fr.kept] == ["Spicy Tan Tan Noodle"]
    assert any(x["reason"] == "side, drink or add-on" for x in fr.removed)


def test_default_ranker_is_the_reranker(monkeypatch):
    from agent import graph
    monkeypatch.delenv("RANKER", raising=False)
    assert graph._ranker_provider("demo") == "reranker"


def test_explain_facts_say_which_option_is_cheapest():
    from agent import llm
    calls = []

    class Rec:
        def invoke(self, msgs):
            calls.append(msgs)
            return type("M", (), {"content": "ok", "response_metadata": {}, "usage_metadata": None})()
    llm.set_llm(Rec())
    opts = [{"offer": {"restaurant": "Z&Y", "item": "Vermicelli", "source": "ubereats", "eta_min": None},
             "total": 33.9, "ranker": "reranker"},
            {"offer": {"restaurant": "Z&Y", "item": "Cold Noodle", "source": "ubereats", "eta_min": None},
             "total": 25.9, "ranker": "reranker"}]
    llm.summarize_options({"raw_text": "spicy noodles"}, opts)
    human = calls[0][1].content if hasattr(calls[0], "__getitem__") else calls[0].to_messages()[1].content
    assert '"cheapest_shown": false' in human and '"cheapest_shown": true' in human and "relevance" in human
