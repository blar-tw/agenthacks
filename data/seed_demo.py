"""Seed the demo user: 5 past orders, one stated hard rule, one rejection, then distill.

    uv run python -m data.seed_demo     # uses MEMORY_BACKEND from .env (singlestore for the demo)

Afterwards the audit trail can show "likes spicy noodles, learned from orders #12, #15, #19, #21".
Safe to run again: rows are keyed by fixed IDs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import contracts as c

DEMO_USER = "demo"


def demo_orders() -> list[dict]:
    base = datetime(2026, 10, 2, 19, 0, tzinfo=timezone.utc)
    rows = [
        ("ord-12", "doordash", "Sichuan Garden", ["Dan Dan Noodles"], 26.97, 2.99, 31, 5),
        ("ord-15", "ubereats", "Noodle House", ["Spicy Wonton Noodle Soup"], 29.0, 1.99, 36, 5),
        ("ord-19", "doordash", "Sichuan Garden", ["Sichuan Beef Noodles - Hot"], 33.97, 2.99, 29, 4),
        ("ord-21", "ubereats", "Spicy Village", ["Chongqing Spicy Noodles"], 31.5, 2.49, 41, 4),
        ("ord-23", "doordash", "Mama Lin's Dumplings", ["Pork Soup Dumplings"], 24.5, 1.49, 27, 4),
    ]
    return [{"order_id": i, "platform": p, "restaurant": r, "items": items, "cuisine": "chinese",
             "price_paid": paid, "fees": fees, "eta_min": eta, "rating": rating,
             "ordered_at": (base + timedelta(days=7 * n)).isoformat()}
            for n, (i, p, r, items, paid, fees, eta, rating) in enumerate(rows)]


def seed(user_id: str = DEMO_USER) -> dict:
    from data import nodes

    s = c.get_store()
    if hasattr(s, "ensure_user"):
        s.ensure_user(user_id, address_label="home")
    existing = {o.get("order_id") for o in c.read_orders(user_id, 1000)}
    for o in demo_orders():
        if o["order_id"] not in existing:
            c.write_order(user_id, o)
    c.write_preference(user_id, {"pref_id": f"pref_{user_id}_no_mushrooms", "text": "dislikes mushrooms",
                                 "kind": "hard", "source": "stated", "confidence": 1.0, "term": "mushroom",
                                 "created_at": "2026-10-01T00:00:00+00:00"})
    if not any(i.get("interaction_id") == "int-golden-wok-1" for i in c.read_interactions(user_id, 1000)):
        c.write_interaction(user_id, {"interaction_id": "int-golden-wok-1", "shown": [], "rejected": [],
                                      "rejected_restaurants": ["Golden Wok"], "reason": "too oily",
                                      "at": "2026-11-13T19:30:00+00:00"})
    prefs = nodes.run_distiller(user_id, run_id="seed-demo")   # no-op on a re-run: no new episodes
    learned = [p for p in c.read_preferences(user_id) if p.get("source") == "learned"]
    return {"orders": len(demo_orders()), "learned_now": len(prefs),
            "learned": [(p["text"], p.get("episode_ids", [])) for p in learned]}


def seed_cognee(user_id: str = DEMO_USER) -> list[str]:
    """Send every demo episode to Cognee (with its ID in the text), then recall what was learned.
    The normal distiller only sends episodes newer than the bookmark; this backfills the demo user."""
    import asyncio

    from data import distill

    orders, rejections = distill.history(user_id)
    texts = [distill.episode_text(e) for e in [*orders, *rejections]]
    q = "What food does the user like or dislike? Cite the episode IDs in brackets."

    async def go() -> list[str]:
        await distill.cognee_remember(texts)
        out: list[str] = []
        for _ in range(12):   # Cloud builds the graph in the background
            out = await distill.cognee_recall(q, wait_s=120)
            if any(distill.EPISODE_REF.search(t) for t in out):
                break
            await asyncio.sleep(10)
        return out

    return asyncio.run(go())


if __name__ == "__main__":
    import sys

    out = seed()
    print(f"seeded {out['orders']} demo orders; distiller learned {out['learned_now']} preference(s) this run")
    for text, ids in out["learned"]:
        print(f"learned: {text} <- {', '.join(ids)}")
    if "--cognee" in sys.argv:
        for text in seed_cognee():
            print(f"cognee recall: {text}")
