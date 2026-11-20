from data import actors, nodes
from data.match import match_offers, restaurant_similarity
from data.normalize import normalize_results


def _offers():
    return normalize_results({p: actors.load_fixture(p) for p in ("ubereats", "doordash")})


def _pairs(matches, offers):
    by = {o.id: o for o in offers}
    return {(by[m.ubereats_offer_id].item, by[m.doordash_offer_id].item) for m in matches}


def test_vector_match_pairs_same_dish(fake_sie):
    offers = _offers()
    matches, method = match_offers(offers, fake_sie)
    assert method == "vector"
    pairs = _pairs(matches, offers)
    assert ("Szechuan Spicy Beef Noodle Soup", "Sichuan Beef Noodles — Hot") in pairs
    assert ("Dan Dan Noodles", "Dan Dan Noodles") in pairs
    # never across restaurants, never noodles <-> salad
    assert not any("Caesar" in a and "Noodle" in b for a, b in pairs)
    assert all(0 < m.confidence <= 1 for m in matches)


def test_one_to_one_inside_a_restaurant(fake_sie):
    matches, _ = match_offers(_offers(), fake_sie)
    ue = [m.ubereats_offer_id for m in matches]
    dd = [m.doordash_offer_id for m in matches]
    assert len(ue) == len(set(ue)) and len(dd) == len(set(dd))


def test_fuzzy_fallback_without_embedder():
    offers = _offers()
    matches, method = match_offers(offers, None)
    assert method == "fuzzy"
    assert ("Dan Dan Noodles", "Dan Dan Noodles") in _pairs(matches, offers)


def test_restaurant_first_rejects_other_branch():
    offers = _offers()
    a = next(o for o in offers if o.platform == "doordash" and o.restaurant == "Golden Wok")
    b = a.model_copy(update={"address": "999 Other St, San Francisco, CA"})
    assert restaurant_similarity(a, b) == 0.0


def test_match_node_writes_inference_with_evidence(mem):
    state = {"run_id": "r3", "search_results": {p: actors.load_fixture(p) for p in ("ubereats", "doordash")}}
    state.update(nodes.normalize(state))
    out = nodes.match_offers(state)
    assert out["matches"]
    inf = [e for e in mem.list_audit("r3") if e.node == "match_offers"]
    assert inf and all(e.kind == "inference" for e in inf)
    assert all(len(e.evidence_ids) == 2 for e in inf if "match_id" in e.payload)
    assert mem.vectors   # dish vectors stored on the snapshots


def test_match_node_falls_back_when_sie_fails(mem):
    from data import sie

    class Broken:
        def embed(self, *a, **k):
            raise ConnectionError("down")
    sie.set_sie(Broken())
    state = {"run_id": "r4", "search_results": {p: actors.load_fixture(p) for p in ("ubereats", "doordash")}}
    state.update(nodes.normalize(state))
    out = nodes.match_offers(state)
    assert out["matches"] and all(m.method == "fuzzy" for m in out["matches"])
    assert any(e.payload.get("what") == "matching fallback" for e in mem.list_audit("r4"))
