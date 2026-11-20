import contracts as c
from conftest import scraped_state
from data import nodes, sie
from data.rank import est_total, term_of, violates

REQ = c.Constraints(raw_text="Spicy Chinese food for two, under $40, 40 min, no mushrooms",
                    dish_query="spicy Chinese noodles", cuisine="chinese", people=2, budget_total=40.0,
                    max_eta_min=40, hard_excludes=["mushrooms"], soft_prefs=["noodles"])


def _state(run_id, **extra):
    state = scraped_state(run_id, constraints=REQ.model_dump(), **extra)
    state.update(nodes.match_offers(state))
    return state


def _ranked(out):
    return [c.RankedOption(**r) for r in out["ranked"]]


def test_term_of_and_synonyms():
    assert term_of("No mushrooms") == "mushroom"
    assert term_of("I'm allergic to peanuts") == "peanut"
    shiitake = c.Offer(restaurant="x", item="Bok Choy with Shiitake", price=1, source="ubereats")
    enoki = c.Offer(restaurant="x", item="Mala Noodles", description="enoki", price=1, source="ubereats")
    assert violates(shiitake, ["mushroom"]) == violates(enoki, ["mushroom"]) == "mushroom"


def test_hard_rules_budget_eta_and_rejected_are_removed(mem):
    state = _state("r6")
    dan_dan_ue = next(o["offer_id"] for o in state["offers"] if o["item"] == "Dan Dan Noodles" and o["source"] == "ubereats")
    state["rejected"] = [dan_dan_ue]
    ranked = _ranked(nodes.filter_rank(state))
    items = " ".join(r.offer.item + r.offer.description for r in ranked).lower()
    assert "shiitake" not in items and "enoki" not in items
    assert all(est_total(r.offer, 2) <= 40 for r in ranked)
    assert all(r.offer.eta_min is None or r.offer.eta_min <= 40 for r in ranked)
    assert dan_dan_ue not in {r.offer.offer_id for r in ranked}
    removed = next(e for e in c.get_audit("r6") if e.payload.get("what") == "removed options that break a rule")
    reasons = {x["reason"] for x in removed.payload["removed"]}
    assert "hard rule: no mushroom" in reasons and "rejected by the user" in reasons
    assert removed.evidence_ids


def test_matched_pair_keeps_the_cheaper_platform(mem):
    state = _state("r7", ranker="price")
    ranked = _ranked(nodes.filter_rank(state))
    soup = next(r for r in ranked if "Beef Noodle" in r.offer.item)
    assert soup.match_id
    match = next(m for m in state["matches"] if m["match_id"] == soup.match_id)
    pair = [c.Offer(**o) for o in state["offers"] if o["offer_id"] in match["offer_ids"]]
    assert est_total(soup.offer, 2) == min(est_total(o, 2) for o in pair)
    assert sum(1 for r in ranked if r.match_id == soup.match_id) == 1


def test_price_tie_shows_the_better_documented_platform():
    from data.rank import filter_offers
    ue = c.Offer(offer_id="ue1", restaurant="Z", item="Dan Dan Noodles", price=12.95, source="ubereats")
    dd = c.Offer(offer_id="dd1", restaurant="Z", restaurant_address="655 Jackson St", item="Dan Dan Noodles",
                 price=12.95, source="doordash")
    m = c.Match(offer_ids=["ue1", "dd1"], confidence=0.9)
    for order in ([ue, dd], [dd, ue]):   # the scrape order must not decide
        (kept,) = filter_offers(order, c.Constraints(dish_query="noodles"), None, [m], []).kept
        assert kept.offer.source == "doordash" and kept.match_id == m.match_id


def test_price_ranker_is_cheapest_first(mem):
    out = nodes.filter_rank(_state("r8", ranker="price"))
    ranked = _ranked(out)
    totals = [est_total(r.offer, 2) for r in ranked]
    assert out["ranker"] == "price" and totals == sorted(totals)


def test_reranker_prefers_noodles_and_demotes_rejected_restaurant(mem):
    memory = {"orders": [], "rules": [{"text": "dislikes mushrooms", "kind": "hard", "term": "mushroom"}],
              "soft_prefs": [{"text": "likes spicy noodles", "kind": "soft", "source": "learned"}],
              "rejections": [{"rejected_restaurants": ["Golden Wok"], "reason": "too oily"}]}
    out = nodes.filter_rank(_state("r9", memory=memory, ranker="reranker"))
    ranked = _ranked(out)
    assert out["ranker"] == "reranker"
    assert "Noodle" in ranked[0].offer.item and ranked[0].offer.restaurant != "Golden Wok"
    ranked_ev = next(e for e in c.get_audit("r9") if e.payload.get("what") == "ranked options")
    assert ranked_ev.evidence_ids     # the ranking cites the evidence behind it


def test_reranker_failure_falls_back_to_price(mem):
    state = _state("r10", ranker="reranker")

    class Broken:
        def score(self, *a, **k):
            raise TimeoutError
    sie.set_sie(Broken())
    out = nodes.filter_rank(state)
    assert out["ranker"] == "price"
    assert any(e.payload.get("what") == "reranker failed; price-only ranker used" for e in c.get_audit("r10"))


def test_mix_platforms_puts_the_other_app_at_number_3():
    from data.nodes import mix_platforms
    def r(i, src):
        return c.RankedOption(offer=c.Offer(offer_id=i, restaurant="x", item=i, price=1, source=src), score=1, ranker="reranker")
    ranked = [r("a", "ubereats"), r("b", "ubereats"), r("c", "ubereats"), r("d", "ubereats"), r("e", "doordash")]
    out, pick = mix_platforms(ranked, 3)
    assert [x.offer.offer_id for x in out] == ["a", "b", "e", "c", "d"] and pick.offer.offer_id == "e"
    same, none = mix_platforms(ranked[:4], 3)
    assert none is None and same == ranked[:4]
