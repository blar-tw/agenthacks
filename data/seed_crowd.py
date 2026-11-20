"""Seed 40 synthetic users around the demo user, so memory tables look like a real multi-user database.

    MEMORY_BACKEND=singlestore uv run python -m data.seed_crowd          # add (safe to re-run: fixed IDs)
    MEMORY_BACKEND=singlestore uv run python -m data.seed_crowd --remove # delete them again

Every row is plainly fake: user IDs are "sample-user-01" .. "sample-user-40" and order IDs start with
"ord-sample-". The demo user's own rows are never touched. The UI's memory view scans these rows and
skips them on user_id, the way load_memory's WHERE clause does.
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone

import contracts as c

USERS = [f"sample-user-{n:02d}" for n in range(1, 41)]
RESTAURANTS = [
    ("Z & Y Restaurant", ["Dan Dan Noodles", "Chongqing Chicken", "Tan Tan Noodle", "Fish Fillet in Hot Chili Oil"]),
    ("Sichuan Home", ["Ma-po Tofu", "Pot Sticker (6)", "Lamb in Cumin Sauce", "Dry Fried Green Beans"]),
    ("Spices", ["Hot & Sour Soup", "Spicy Wonton", "Cumin Beef", "Three Cup Chicken"]),
    ("China Live", ["Xiao Long Bao", "Kung Pao Chicken", "Peking Duck Bao", "Scallion Pancake"]),
    ("Z & Y Peking Duck", ["Half Peking Duck", "Spicy Dumpling", "Duck Fried Rice", "Garlic Eggplant"]),
    ("Golden Wok", ["Orange Chicken", "Beef Chow Fun", "Egg Rolls"]),
    ("Noodle House", ["Beef Noodle Soup", "Spicy Wonton Noodle Soup", "Zha Jiang Mian"]),
]
RULES = ["peanuts", "pork", "shellfish", "mushrooms", "cilantro", "beef", "dairy"]
LIKES = ["likes dumplings", "likes spicy food", "likes soup", "prefers fast delivery", "likes tofu dishes",
         "orders for two on weekends", "likes duck", "avoids fried food"]
REASONS = ["too oily", "too slow", "too expensive", "portion too small", "arrived cold"]


def rows(seed: int = 7) -> tuple[list[tuple[str, dict]], list[tuple[str, dict]], list[tuple[str, dict]]]:
    rnd = random.Random(seed)
    base = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)
    orders, prefs, inters = [], [], []
    for u_i, user in enumerate(USERS):
        for k in range(rnd.randint(6, 14)):
            rest, menu = rnd.choice(RESTAURANTS)
            items = rnd.sample(menu, rnd.randint(1, 2))
            paid = round(sum(rnd.uniform(11, 24) for _ in items), 2)
            orders.append((user, {"order_id": f"ord-sample-{u_i + 1:02d}-{k + 1:02d}", "platform": rnd.choice(["ubereats", "doordash"]),
                                  "restaurant": rest, "items": items, "cuisine": "chinese", "price_paid": paid,
                                  "fees": round(rnd.uniform(0.99, 4.99), 2), "eta_min": rnd.randint(22, 55),
                                  "rating": rnd.randint(3, 5), "ordered_at": (base + timedelta(hours=rnd.randint(0, 80 * 24))).isoformat()}))
        for r in rnd.sample(RULES, rnd.randint(0, 2)):
            prefs.append((user, {"text": f"no {r}", "kind": "hard", "source": "stated", "confidence": 1.0, "term": r.rstrip("s"),
                                 "created_at": (base + timedelta(days=rnd.randint(0, 60))).isoformat()}))
        for t in rnd.sample(LIKES, rnd.randint(1, 2)):
            prefs.append((user, {"text": t, "kind": "soft", "source": "learned", "confidence": round(rnd.uniform(0.55, 0.95), 2),
                                 "created_at": (base + timedelta(days=rnd.randint(30, 80))).isoformat()}))
        for k in range(rnd.randint(0, 2)):
            rest = rnd.choice(RESTAURANTS)[0]
            inters.append((user, {"interaction_id": f"int-sample-{u_i + 1:02d}-{k + 1}", "shown": [], "rejected": [],
                                  "rejected_restaurants": [rest], "reason": rnd.choice(REASONS),
                                  "at": (base + timedelta(days=rnd.randint(10, 80))).isoformat()}))
    return orders, prefs, inters


def seed() -> dict:
    s = c.get_store()
    orders, prefs, inters = rows()
    for user in USERS:
        if hasattr(s, "ensure_user"):
            s.ensure_user(user, address_label="sample")
    for user, o in orders:
        c.write_order(user, o)
    for user, p in prefs:
        c.write_preference(user, p)
    for user, i in inters:
        c.write_interaction(user, i)
    return {"users": len(USERS), "orders": len(orders), "preferences": len(prefs), "interactions": len(inters)}


def remove() -> None:
    s = c.get_store()
    if not hasattr(s, "_exec"):
        return
    marks = ",".join(["%s"] * len(USERS))
    for table in ("orders", "preferences", "interactions", "users"):
        s._exec(f"DELETE FROM {table} WHERE user_id IN ({marks})", tuple(USERS))


if __name__ == "__main__":
    from dotenv import load_dotenv

    from data import store
    load_dotenv()
    store.install()
    if "--remove" in sys.argv:
        remove()
        print("removed the sample users")
    else:
        print(seed())
