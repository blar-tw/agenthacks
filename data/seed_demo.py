"""Seed the demo user: 5 past orders, one stated hard rule, one rejection, then distill.

    uv run python -m data.seed_demo                  # MEMORY_BACKEND from .env
    MEMORY_BACKEND=singlestore uv run python -m data.seed_demo

Afterwards the audit trail can show "likes spicy noodles, learned from orders #12, #15, #19, #21".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from contracts import Interaction, OrderRecord, Preference
from data.store import Store, get_store

DEMO_USER = "demo"


def demo_orders(user_id: str = DEMO_USER) -> list[OrderRecord]:
    base = datetime(2026, 10, 2, 19, 0, tzinfo=timezone.utc)
    rows = [
        ("ord-12", "doordash", "Sichuan Garden", ["Dan Dan Noodles"], 26.97, 2.99, 31, 5),
        ("ord-15", "ubereats", "Noodle House", ["Spicy Wonton Noodle Soup"], 29.0, 1.99, 36, 5),
        ("ord-19", "doordash", "Sichuan Garden", ["Sichuan Beef Noodles - Hot"], 33.97, 2.99, 29, 4),
        ("ord-21", "ubereats", "Spicy Village", ["Chongqing Spicy Noodles"], 31.5, 2.49, 41, 4),
        ("ord-23", "doordash", "Mama Lin's Dumplings", ["Pork Soup Dumplings"], 24.5, 1.49, 27, 4),
    ]
    return [OrderRecord(id=i, user_id=user_id, platform=p, restaurant=r, items=items, cuisine="chinese",
                        price_paid=paid, fees=fees, eta_min=eta, rating=rating,
                        ordered_at=base + timedelta(days=7 * n))
            for n, (i, p, r, items, paid, fees, eta, rating) in enumerate(rows)]


def seed(store: Store | None = None, user_id: str = DEMO_USER) -> dict:
    from data.nodes import run_distiller

    store = store or get_store()
    store.ensure_user(user_id, address_label="Home")
    for o in demo_orders(user_id):
        store.add_order(o)
    store.upsert_preference(Preference(id=f"pref-{user_id}-no-mushrooms", user_id=user_id,
                                       text="dislikes mushrooms", kind="hard", source="stated",
                                       confidence=1.0, term="mushroom",
                                       created_at=datetime(2026, 10, 1, tzinfo=timezone.utc)))
    store.add_interaction(Interaction(id="int-golden-wok-1", user_id=user_id, shown=[], rejected=[],
                                      rejected_restaurants=["Golden Wok"], reason="too oily",
                                      at=datetime(2026, 11, 13, 19, 30, tzinfo=timezone.utc)))
    prefs = run_distiller(user_id, run_id="seed-demo")
    return {"orders": 5, "learned": [(p.text, p.episode_ids) for p in prefs]}


if __name__ == "__main__":
    out = seed()
    print(f"seeded {out['orders']} demo orders")
    for text, ids in out["learned"]:
        print(f"learned: {text} <- {', '.join(ids)}")
