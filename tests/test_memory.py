import contracts as c
from conftest import scraped_state
from data import nodes
from data.distill import count_preferences, should_distill
from data.seed_demo import demo_orders, seed


def test_seed_learns_spicy_noodles_with_episode_ids(mem):
    out = seed()
    learned = dict(out["learned"])
    assert learned["likes spicy noodles"] == ["ord-12", "ord-15", "ord-19", "ord-21"]
    prefs = c.read_preferences("demo")
    assert [p["text"] for p in prefs if p["kind"] == "hard"] == ["dislikes mushrooms"]   # stated only
    assert all(p["kind"] == "soft" for p in prefs if p["source"] == "learned")
    assert mem.last_distilled_at("demo") is not None


def test_seed_is_idempotent(mem):
    seed()
    before = (len(c.read_orders("demo", 100)), len(c.read_preferences("demo")))
    again = seed()
    assert again["learned_now"] == 0
    assert (len(c.read_orders("demo", 100)), len(c.read_preferences("demo"))) == before


def test_distiller_needs_three_orders_and_three_new_episodes(mem):
    for o in demo_orders()[:2]:
        c.write_order("u", o)
    assert not should_distill("u")
    c.write_order("u", demo_orders()[2])
    assert should_distill("u")
    nodes.run_distiller("u", "r")
    assert not should_distill("u")        # the bookmark moved


def test_one_order_is_not_a_preference():
    assert count_preferences(demo_orders()[:2], []) == []


def test_rejection_reasons_become_soft_dislikes_only():
    rej = [{"interaction_id": f"int-{i}", "reason": "way too oily", "rejected_restaurants": ["Golden Wok"]}
           for i in range(3)]
    assert [(p["text"], p["kind"], p["source"]) for p in count_preferences([], rej)] == \
        [("dislikes oily food", "soft", "learned")]


def test_load_memory_reads_orders_rules_and_learned(mem):
    seed()
    m = nodes.load_memory({"run_id": "r11", "user_id": "demo", "request_text": "spicy noodles"})["memory"]
    assert len(m["orders"]) == 5 and m["orders"][0]["order_id"] == "ord-23"
    assert [r["term"] for r in m["rules"]] == ["mushroom"]
    assert any(p["text"] == "likes spicy noodles" for p in m["soft_prefs"])
    assert m["rejections"][0]["reason"] == "too oily"
    ev = c.get_audit("r11")
    assert [e.kind for e in ev] == ["evidence", "inference"] and ev[1].evidence_ids == [ev[0].event_id]


def test_save_stated_rule_is_hard_and_stated(mem):
    nodes.save_stated_rule("u2", "I'm allergic to peanuts", run_id="r12")
    (p,) = c.read_preferences("u2")
    assert (p["kind"], p["source"], p["term"]) == ("hard", "stated", "peanut")
    assert c.get_audit("r12")[0].kind == "action"


def test_persist_learn_saves_order_after_approval_and_rejection(mem):
    state = scraped_state("r13", constraints=c.Constraints(cuisine="chinese", people=2).model_dump(), ranker="price")
    state.update(nodes.filter_rank(state))
    first, second = state["ranked"][0], state["ranked"][1]
    state.update({"rejected": [first["offer"]["offer_id"]], "feedback": "too oily", "selected": second,
                  "approval": True, "quote": {"total": 31.4, "fees": 2.99}})
    nodes.persist_learn(state)
    (order,) = [o for o in c.read_orders("demo", 100) if o.get("run_id") == "r13"]
    assert order["price_paid"] == 31.4 and order["items"] == [second["offer"]["item"]]
    (inter,) = [i for i in c.read_interactions("demo", 100) if i.get("run_id") == "r13"]
    assert inter["rejected"] == [first["offer"]["offer_id"]] and inter["reason"] == "too oily"
    assert first["offer"]["restaurant"] in inter["rejected_restaurants"]
    saved = next(e for e in c.get_audit("r13") if e.node == "persist_learn")
    assert saved.kind == "action" and len(saved.payload["episode_ids"]) == 2


def test_no_order_without_approval(mem):
    state = scraped_state("r14", ranker="price")
    state.update(nodes.filter_rank(state))
    state.update({"selected": state["ranked"][0], "approval": False})
    nodes.persist_learn(state)
    assert not [o for o in c.read_orders("demo", 100) if o.get("run_id") == "r14"]
    (inter,) = [i for i in c.read_interactions("demo", 100) if i.get("run_id") == "r14"]
    assert inter["rejected"] == [state["ranked"][0]["offer"]["offer_id"]]   # a "no" is a rejection episode
