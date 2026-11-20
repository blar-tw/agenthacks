from datetime import datetime, timezone

from contracts import Constraints, Interaction
from data import nodes
from data.distill import count_preferences, should_distill
from data.seed_demo import demo_orders, seed


def test_seed_learns_spicy_noodles_from_orders(mem):
    out = seed(mem)
    learned = dict((t, ids) for t, ids in out["learned"])
    assert "likes spicy noodles" in learned
    assert learned["likes spicy noodles"] == ["ord-12", "ord-15", "ord-19", "ord-21"]
    assert all(p.kind == "soft" for p in mem.preferences("demo", source="learned"))
    assert [p.text for p in mem.preferences("demo", kind="hard")] == ["dislikes mushrooms"]   # stated only
    assert mem.last_distilled_at("demo") is not None


def test_distiller_needs_three_orders_and_three_new_episodes(mem):
    mem.ensure_user("u")
    for o in demo_orders("u")[:2]:
        mem.add_order(o)
    assert not should_distill(mem, "u")
    mem.add_order(demo_orders("u")[2])
    assert should_distill(mem, "u")
    nodes.run_distiller("u", "r")
    assert not should_distill(mem, "u")        # bookmark moved


def test_one_order_is_not_a_preference():
    prefs = count_preferences("u", demo_orders("u")[:2], [])
    assert prefs == []


def test_rejection_reasons_become_soft_dislikes_only():
    rej = [Interaction(id=f"int-{i}", user_id="u", reason="way too oily", rejected_restaurants=["Golden Wok"],
                       at=datetime(2026, 11, i + 1, tzinfo=timezone.utc)) for i in range(3)]
    prefs = count_preferences("u", [], rej)
    assert [(p.text, p.kind, p.source) for p in prefs] == [("dislikes oily food", "soft", "learned")]


def test_load_memory_reads_orders_rules_and_learned(mem):
    seed(mem)
    out = nodes.load_memory({"run_id": "r7", "user_id": "demo", "request": "spicy noodles"})
    m = out["memory"]
    assert len(m.recent_orders) == 5 and m.recent_orders[0].id == "ord-23"
    assert [p.term for p in m.hard_rules] == ["mushroom"]
    assert any(p.text == "likes spicy noodles" for p in m.soft_prefs)
    assert m.recent_rejections[0].reason == "too oily"
    ev = mem.list_audit("r7")
    assert [e.kind for e in ev] == ["evidence", "inference"]
    assert ev[1].evidence_ids == [ev[0].id]


def test_persist_learn_saves_order_and_rejection_episodes(mem):
    seed(mem)
    from data import actors
    state = {"run_id": "r8", "user_id": "demo", "search_results": {p: actors.load_fixture(p) for p in ("ubereats", "doordash")},
             "constraints": Constraints(cuisine="chinese", party_size=2)}
    state.update(nodes.normalize(state))
    state.update(nodes.filter_rank({**state, "ranker": "price"}))
    first, second = state["ranked"][0].offer, state["ranked"][1].offer
    state.update({"rejected": [first.id], "rejection_reason": "too oily", "selected": second.id,
                  "approved": True, "quote": {"total": 31.4}})
    out = nodes.persist_learn(state)
    assert len(out["episode_ids"]) == 2
    order = [o for o in mem.orders if o.run_id == "r8"][0]
    assert order.price_paid == 31.4 and order.items == [second.item]
    inter = [i for i in mem.interactions if i.run_id == "r8"][0]
    assert inter.rejected == [first.id] and first.restaurant in inter.rejected_restaurants
    assert any(e.kind == "action" and e.node == "persist_learn" for e in mem.list_audit("r8"))


def test_no_order_without_approval(mem):
    from data import actors
    state = {"run_id": "r9", "user_id": "demo", "search_results": {"doordash": actors.load_fixture("doordash")}}
    state.update(nodes.normalize(state))
    state.update({"selected": state["offers"][0].id, "approved": False})
    nodes.persist_learn(state)
    assert not [o for o in mem.orders if o.run_id == "r9"]
