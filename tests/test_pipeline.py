"""Fixtures only: load_memory -> scrape -> normalize -> match_offers -> filter_rank -> persist_learn
-> distill_preferences. Every step leaves audit rows."""
import asyncio

from contracts import Constraints
from data import nodes
from data.seed_demo import seed


def test_p2_pipeline_on_fixtures(mem):
    seed(mem)
    state = {"run_id": "run-p", "user_id": "demo", "request": "Spicy Chinese food for two, under $40, 40 min, no mushrooms",
             "constraints": Constraints(query="spicy Chinese noodles for two", cuisine="chinese", party_size=2,
                                        budget=40, max_eta_min=40, exclusions=["mushrooms"], wants=["noodles"])}
    state.update(nodes.load_memory(state))
    state.update(asyncio.run(nodes.scrape(state)))
    state.update(nodes.normalize(state))
    state.update(nodes.match_offers(state))
    state.update(nodes.filter_rank(state))
    assert state["ranked"] and state["missing_platforms"] == []
    top = state["ranked"][0]
    assert "mushroom" not in (top.offer.item + top.offer.description).lower()
    state.update({"selected": top.offer.id, "approved": True})
    state.update(nodes.persist_learn(state))
    nodes.distill_preferences(state, background=False)

    by_node = {}
    for e in mem.list_audit("run-p"):
        by_node.setdefault(e.node, set()).add(e.kind)
    for node in ("load_memory", "scrape_ubereats", "scrape_doordash", "normalize", "match_offers",
                 "filter_rank", "persist_learn"):
        assert node in by_node, f"{node} left no audit rows"
    assert by_node["normalize"] == {"evidence"}
    assert by_node["filter_rank"] == {"inference"}
    assert by_node["persist_learn"] == {"action"}
    # inference events cite evidence that exists
    ids = {e.id for e in mem.audit}
    for e in mem.list_audit("run-p"):
        assert set(e.evidence_ids) <= ids | {o.evidence_id for o in state["offers"]}
