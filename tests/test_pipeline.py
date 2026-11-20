"""Fixtures only: load_memory -> scrape -> normalize -> match_offers -> filter_rank -> persist_learn
-> distill_preferences, with P1's state shapes. Every step leaves audit rows."""
import contracts as c
from data import nodes
from data.seed_demo import seed


def test_p2_pipeline_on_fixtures(mem):
    seed()
    state = {"run_id": "run-p", "user_id": "demo",
             "request_text": "Spicy Chinese food for two, under $40, within 40 minutes, no mushrooms",
             "constraints": c.Constraints(dish_query="spicy Chinese noodles", cuisine="chinese", people=2,
                                          budget_total=40, max_eta_min=40, hard_excludes=["mushrooms"],
                                          soft_prefs=["noodles"]).model_dump()}
    for node in (nodes.load_memory, nodes.scrape, nodes.normalize, nodes.match_offers, nodes.filter_rank):
        state.update(node(state))
    assert state["ranked"] and state["missing_platforms"] == []
    top = c.RankedOption(**state["ranked"][0])
    assert "mushroom" not in (top.offer.item + top.offer.description).lower()
    state.update({"selected": state["ranked"][0], "approval": True})
    for node in (nodes.persist_learn, lambda s: nodes.distill_preferences(s, background=False)):
        state.update(node(state))

    by_node: dict[str, set[str]] = {}
    for e in c.get_audit("run-p"):
        by_node.setdefault(e.node, set()).add(e.kind)
    for node in ("load_memory", "scrape_ubereats", "scrape_doordash", "normalize", "match_offers",
                 "filter_rank", "persist_learn"):
        assert node in by_node, f"{node} left no audit rows"
    assert by_node["normalize"] == {"evidence"} and by_node["filter_rank"] == {"inference"}
    assert by_node["persist_learn"] == {"action"}
    # every cited evidence id exists in the audit log
    ids = {e.event_id for e in mem.audit}
    assert all(set(e.evidence_ids) <= ids for e in c.get_audit("run-p"))
