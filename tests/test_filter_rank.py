from contracts import Constraints, Interaction, MemoryContext, Preference
from data import actors, nodes
from data.match import match_offers
from data.normalize import normalize_results
from data.rank import filter_offers, rank_price, term_of, violates

REQ = Constraints(query="spicy Chinese noodles for two", cuisine="chinese", party_size=2, budget=40.0,
                  max_eta_min=40, exclusions=["mushrooms"], wants=["noodles", "spicy"])


def _setup(fake_sie):
    state = {"run_id": "setup", "search_results": {p: actors.load_fixture(p) for p in ("ubereats", "doordash")}}
    offers = nodes.normalize(state)["offers"]     # the node attaches evidence IDs
    matches, _ = match_offers(offers, fake_sie)
    return offers, matches


def test_term_of_and_synonyms():
    assert term_of("No mushrooms") == "mushroom"
    assert term_of("I'm allergic to peanuts") == "peanut"
    offers = normalize_results({"ubereats": actors.load_fixture("ubereats")})
    shiitake = next(o for o in offers if "Shiitake" in o.item)
    enoki = next(o for o in offers if "Enoki" in o.item)
    assert violates(shiitake, ["mushroom"]) == "mushroom"
    assert violates(enoki, ["mushroom"]) == "mushroom"


def test_hard_rules_budget_eta_and_rejected_are_removed(fake_sie):
    offers, matches = _setup(fake_sie)
    rejected = [next(o.id for o in offers if o.item == "Dan Dan Noodles" and o.platform == "ubereats")]
    fr = filter_offers(offers, REQ, None, matches, rejected)
    kept_items = {r.offer.item for r in fr.kept} | {r.alt_offer.item for r in fr.kept if r.alt_offer}
    assert not any("Shiitake" in i or "Enoki" in i for i in kept_items)
    reasons = {x["reason"] for x in fr.removed}
    assert any(r.startswith("hard rule: no mushroom") for r in reasons)
    assert "rejected by the user" in reasons
    assert all(r.est_total <= 40 for r in fr.kept)
    assert all(r.offer.eta_min is None or r.offer.eta_min <= 40 for r in fr.kept)


def test_matched_pair_shows_cheaper_platform_first(fake_sie):
    offers, matches = _setup(fake_sie)
    fr = filter_offers(offers, REQ, None, matches, [])
    soup = next(r for r in fr.kept if "Beef Noodle" in r.offer.item)
    assert soup.alt_offer is not None and soup.match_id
    assert soup.est_total <= soup.alt_offer.price * 2 + (soup.alt_offer.fees or 0)
    assert len(soup.evidence_ids) == 2


def test_price_ranker_is_cheapest_first(fake_sie):
    offers, matches = _setup(fake_sie)
    ranked = rank_price(filter_offers(offers, REQ, None, matches, []).kept)
    totals = [r.est_total for r in ranked]
    assert totals == sorted(totals) and all(r.ranker == "price" for r in ranked)


def test_reranker_prefers_noodles_and_demotes_rejected_restaurant(fake_sie):
    offers, matches = _setup(fake_sie)
    mem = MemoryContext(
        hard_rules=[Preference(id="h", user_id="demo", text="dislikes mushrooms", kind="hard", source="stated", term="mushroom")],
        soft_prefs=[Preference(id="s", user_id="demo", text="likes spicy noodles", kind="soft", source="learned")],
        recent_rejections=[Interaction(id="i", user_id="demo", rejected_restaurants=["Golden Wok"], reason="too oily",
                                       at="2026-11-13T19:30:00+00:00")])
    state = {"run_id": "r5", "constraints": REQ, "memory": mem, "offers": offers, "matches": matches,
             "ranker": "reranker"}
    out = nodes.filter_rank(state)
    assert out["ranker"] == "reranker"
    top = out["ranked"][0].offer
    assert "Noodle" in top.item and top.restaurant != "Golden Wok"
    wok = [i for i, r in enumerate(out["ranked"]) if r.offer.restaurant == "Golden Wok"]
    assert all(i > 0 for i in wok)


def test_filter_rank_node_falls_back_to_price_when_reranker_fails(fake_sie, mem):
    from data import sie
    offers, matches = _setup(fake_sie)

    class Broken:
        def score(self, *a, **k):
            raise TimeoutError
    sie.set_sie(Broken())
    out = nodes.filter_rank({"run_id": "r6", "constraints": REQ, "offers": offers, "matches": matches,
                             "ranker": "reranker"})
    assert out["ranker"] == "price"
    kinds = {e.kind for e in mem.list_audit("r6")}
    assert kinds == {"inference"}
    ranked_ev = [e for e in mem.list_audit("r6") if e.payload.get("what") == "ranked options"][0]
    assert ranked_ev.evidence_ids     # the ranking cites the evidence behind it
