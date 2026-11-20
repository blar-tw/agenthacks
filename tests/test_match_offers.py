import contracts as c
from conftest import scraped_state
from data import actors, nodes, sie
from data.match import match_offers, restaurant_similarity
from data.normalize import offers_from_result


def _offers():
    return offers_from_result(actors.load_fixture("ubereats")) + offers_from_result(actors.load_fixture("doordash"))


def _pairs(matches, offers):
    by = {o.offer_id: o for o in offers}
    return {(by[m.offer_ids[0]].item, by[m.offer_ids[1]].item) for m in matches}


def test_vector_match_pairs_same_dish(fake_sie):
    offers = _offers()
    matches, method = match_offers(offers, fake_sie)
    assert method == "vector"
    pairs = _pairs(matches, offers)
    assert ("Szechuan Spicy Beef Noodle Soup", "Sichuan Beef Noodles — Hot") in pairs
    assert ("Dan Dan Noodles", "Dan Dan Noodles") in pairs
    assert not any("Caesar" in a and "Noodle" in b for a, b in pairs)
    assert all(0 < m.confidence <= 1 for m in matches)


def test_one_to_one_and_ubereats_first(fake_sie):
    offers = _offers()
    src = {o.offer_id: o.source for o in offers}
    matches, _ = match_offers(offers, fake_sie)
    assert all(src[m.offer_ids[0]] == "ubereats" and src[m.offer_ids[1]] == "doordash" for m in matches)
    flat = [i for m in matches for i in m.offer_ids]
    assert len(flat) == len(set(flat))


def test_fuzzy_fallback_without_embedder():
    offers = _offers()
    matches, method = match_offers(offers, None)
    assert method == "fuzzy"
    assert ("Dan Dan Noodles", "Dan Dan Noodles") in _pairs(matches, offers)


def test_restaurant_first_rejects_other_branch():
    a = next(o for o in _offers() if o.source == "doordash" and o.restaurant == "Golden Wok")
    b = a.model_copy(update={"restaurant_address": "999 Other St, San Francisco, CA"})
    assert restaurant_similarity(a, b) == 0.0


def test_match_node_writes_inference_citing_evidence(mem):
    state = scraped_state("r4")
    out = nodes.match_offers(state)
    assert out["matches"]
    ev_ids = {o["evidence_id"] for o in state["offers"]}
    inf = [e for e in c.get_audit("r4") if e.node == "match_offers"]
    assert inf and all(e.kind == "inference" for e in inf)
    assert all(len(e.evidence_ids) == 2 and set(e.evidence_ids) <= ev_ids for e in inf)
    assert mem.vectors and mem.match_rows and mem.match_rows[0]["method"] == "vector"


def test_match_node_falls_back_when_sie_fails(mem):
    class Broken:
        def embed(self, *a, **k):
            raise ConnectionError("down")
    state = scraped_state("r5")
    sie.set_sie(Broken())
    out = nodes.match_offers(state)
    assert out["matches"]
    assert any(e.payload.get("what") == "matching fallback" for e in c.get_audit("r5"))
