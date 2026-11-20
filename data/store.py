"""Memory functions. Every node reads and writes memory through a Store; no node writes SQL.

InMemoryStore stands in until SingleStore is configured (Brief 9.3 note).
SingleStoreStore is the source of truth once MEMORY_BACKEND=singlestore.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable, Optional, Protocol

from contracts import AuditEvent, Interaction, Match, Offer, OrderRecord, Preference
from data import config

TABLES = ("users", "orders", "interactions", "preferences", "price_snapshots", "matches", "audit_events")


class Store(Protocol):
    def ensure_user(self, user_id: str, address_label: str | None = None) -> None: ...
    def last_distilled_at(self, user_id: str) -> Optional[datetime]: ...
    def set_last_distilled(self, user_id: str, at: datetime) -> None: ...

    def add_snapshots(self, run_id: str, offers: Iterable[Offer]) -> None: ...
    def set_snapshot_vectors(self, vectors: dict[str, list[float]]) -> None: ...
    def get_snapshot(self, offer_id: str) -> Optional[Offer]: ...

    def add_matches(self, run_id: str, matches: Iterable[Match]) -> None: ...

    def add_audit(self, event: AuditEvent) -> None: ...
    def list_audit(self, run_id: str) -> list[AuditEvent]: ...

    def add_order(self, order: OrderRecord) -> None: ...
    def recent_orders(self, user_id: str, limit: int = 5) -> list[OrderRecord]: ...
    def orders_since(self, user_id: str, since: Optional[datetime]) -> list[OrderRecord]: ...

    def add_interaction(self, interaction: Interaction) -> None: ...
    def rejections_since(self, user_id: str, since: Optional[datetime]) -> list[Interaction]: ...

    def upsert_preference(self, pref: Preference) -> None: ...
    def preferences(self, user_id: str, kind: str | None = None, source: str | None = None) -> list[Preference]: ...


# ---------------------------------------------------------------- in memory

class InMemoryStore:
    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.snapshots: dict[str, Offer] = {}
        self.vectors: dict[str, list[float]] = {}
        self.matches: list[Match] = []
        self.audit: list[AuditEvent] = []
        self.orders: list[OrderRecord] = []
        self.interactions: list[Interaction] = []
        self.prefs: dict[str, Preference] = {}

    def ensure_user(self, user_id, address_label=None):
        self.users.setdefault(user_id, {"address_label": address_label, "last_distilled_at": None})

    def last_distilled_at(self, user_id):
        return self.users.get(user_id, {}).get("last_distilled_at")

    def set_last_distilled(self, user_id, at):
        self.ensure_user(user_id)
        self.users[user_id]["last_distilled_at"] = at

    def add_snapshots(self, run_id, offers):
        for o in offers:
            self.snapshots[o.id] = o

    def set_snapshot_vectors(self, vectors):
        self.vectors.update(vectors)

    def get_snapshot(self, offer_id):
        return self.snapshots.get(offer_id)

    def add_matches(self, run_id, matches):
        self.matches.extend(matches)

    def add_audit(self, event):
        self.audit.append(event)

    def list_audit(self, run_id):
        return [e for e in self.audit if e.run_id == run_id]

    def add_order(self, order):
        self.orders.append(order)

    def recent_orders(self, user_id, limit=5):
        rows = sorted((o for o in self.orders if o.user_id == user_id), key=lambda o: o.ordered_at, reverse=True)
        return rows[:limit]

    def orders_since(self, user_id, since):
        return [o for o in self.orders if o.user_id == user_id and (since is None or o.ordered_at > since)]

    def add_interaction(self, interaction):
        self.interactions.append(interaction)

    def rejections_since(self, user_id, since):
        return [i for i in self.interactions
                if i.user_id == user_id and (i.rejected or i.reason) and (since is None or i.at > since)]

    def upsert_preference(self, pref):
        self.prefs[pref.id] = pref

    def preferences(self, user_id, kind=None, source=None):
        return [p for p in self.prefs.values() if p.user_id == user_id
                and (kind is None or p.kind == kind) and (source is None or p.source == source)]


# ---------------------------------------------------------------- SingleStore

def _j(v: Any) -> str:
    return json.dumps(v, default=str)


def _vec(v: list[float] | None) -> str | None:
    return None if v is None else json.dumps([round(float(x), 6) for x in v])


def connect():
    import pymysql
    return pymysql.connect(
        host=config.env("SINGLESTORE_HOST"),
        port=int(config.env("SINGLESTORE_PORT", "3306")),
        user=config.env("SINGLESTORE_USER"),
        password=config.env("SINGLESTORE_PASSWORD"),
        database=config.env("SINGLESTORE_DATABASE", "mealmatch") or None,
        autocommit=True,
        connect_timeout=10,
        ssl={"ssl": {}} if config.env("SINGLESTORE_SSL", "1") != "0" else None,
        cursorclass=pymysql.cursors.DictCursor,
    )


def create_tables(conn) -> list[str]:
    sql = (config.ROOT / "data" / "schema.sql").read_text(encoding="utf-8").replace("{dim}", str(config.EMBED_DIM))
    body = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    with conn.cursor() as cur:
        for stmt in (s.strip() for s in body.split(";")):
            if stmt:
                cur.execute(stmt)
    return list(TABLES)


class SingleStoreStore:
    def __init__(self, conn=None) -> None:
        self.conn = conn or connect()

    def _exec(self, sql: str, args: Any = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(sql, args)

    def _many(self, sql: str, rows: list[tuple]) -> None:
        if rows:
            with self.conn.cursor() as cur:
                cur.executemany(sql, rows)

    def _rows(self, sql: str, args: Any = None) -> list[dict[str, Any]]:
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            return list(cur.fetchall())

    # users
    def ensure_user(self, user_id, address_label=None):
        self._exec("INSERT IGNORE INTO users (id, address_label, settings) VALUES (%s, %s, %s)",
                   (user_id, address_label, _j({})))

    def last_distilled_at(self, user_id):
        rows = self._rows("SELECT last_distilled_at FROM users WHERE id=%s", (user_id,))
        return rows[0]["last_distilled_at"] if rows else None

    def set_last_distilled(self, user_id, at):
        self.ensure_user(user_id)
        self._exec("UPDATE users SET last_distilled_at=%s WHERE id=%s", (at, user_id))

    # snapshots
    def add_snapshots(self, run_id, offers):
        self._many(
            "REPLACE INTO price_snapshots (id, run_id, platform, restaurant, restaurant_id, address, item, "
            "description, price, fees, eta_min, currency, cuisines, rating, source_url, actor_run_id, scraped_at, "
            "evidence_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [(o.id, run_id, o.platform, o.restaurant, o.restaurant_id, o.address, o.item, o.description, o.price,
              o.fees, o.eta_min, o.currency, _j(o.cuisines), o.rating, o.source_url, o.actor_run_id,
              o.scraped_at.replace(tzinfo=None), o.evidence_id) for o in offers])

    def set_snapshot_vectors(self, vectors):
        self._many("UPDATE price_snapshots SET dish_vec=%s WHERE id=%s",
                   [(_vec(v), k) for k, v in vectors.items()])

    def get_snapshot(self, offer_id):
        rows = self._rows("SELECT * FROM price_snapshots WHERE id=%s", (offer_id,))
        if not rows:
            return None
        r = rows[0]
        r["cuisines"] = json.loads(r.get("cuisines") or "[]") if isinstance(r.get("cuisines"), str) else (r.get("cuisines") or [])
        r.pop("dish_vec", None); r.pop("run_id", None)
        return Offer(**r)

    # matches
    def add_matches(self, run_id, matches):
        now = datetime.utcnow()
        self._many("REPLACE INTO matches (id, run_id, ubereats_offer_id, doordash_offer_id, confidence, method, "
                   "evidence_ids, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                   [(m.id, run_id, m.ubereats_offer_id, m.doordash_offer_id, m.confidence, m.method,
                     _j(m.evidence_ids), now) for m in matches])

    # audit
    def add_audit(self, e):
        self._exec("INSERT INTO audit_events (id, run_id, node, at, kind, source_url, actor_run_id, payload, "
                   "evidence_ids, hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                   (e.id, e.run_id, e.node, e.at.replace(tzinfo=None), e.kind, e.source_url, e.actor_run_id,
                    _j(e.payload), _j(e.evidence_ids), e.hash))

    def list_audit(self, run_id):
        out = []
        for r in self._rows("SELECT * FROM audit_events WHERE run_id=%s ORDER BY at, id", (run_id,)):
            r["payload"] = _load(r["payload"], {})
            r["evidence_ids"] = _load(r["evidence_ids"], [])
            out.append(AuditEvent(**r))
        return out

    # orders
    def add_order(self, o):
        self._exec("REPLACE INTO orders (id, user_id, run_id, platform, restaurant, items, cuisine, price_paid, fees, "
                   "eta_min, rating, ordered_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                   (o.id, o.user_id, o.run_id, o.platform, o.restaurant, _j(o.items), o.cuisine, o.price_paid,
                    o.fees, o.eta_min, o.rating, o.ordered_at.replace(tzinfo=None)))

    def _orders(self, sql, args):
        out = []
        for r in self._rows(sql, args):
            r["items"] = _load(r["items"], [])
            out.append(OrderRecord(**r))
        return out

    def recent_orders(self, user_id, limit=5):
        return self._orders("SELECT * FROM orders WHERE user_id=%s ORDER BY ordered_at DESC LIMIT %s", (user_id, limit))

    def orders_since(self, user_id, since):
        if since is None:
            return self._orders("SELECT * FROM orders WHERE user_id=%s ORDER BY ordered_at", (user_id,))
        return self._orders("SELECT * FROM orders WHERE user_id=%s AND ordered_at > %s ORDER BY ordered_at",
                            (user_id, since))

    # interactions
    def add_interaction(self, i):
        self._exec("REPLACE INTO interactions (id, user_id, run_id, shown, selected, rejected, rejected_restaurants, "
                   "reason, at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                   (i.id, i.user_id, i.run_id, _j(i.shown), i.selected, _j(i.rejected), _j(i.rejected_restaurants),
                    i.reason, i.at.replace(tzinfo=None)))

    def rejections_since(self, user_id, since):
        sql = "SELECT * FROM interactions WHERE user_id=%s"
        args: tuple = (user_id,)
        if since is not None:
            sql += " AND at > %s"
            args = (user_id, since)
        out = []
        for r in self._rows(sql + " ORDER BY at", args):
            for k in ("shown", "rejected", "rejected_restaurants"):
                r[k] = _load(r[k], [])
            i = Interaction(**r)
            if i.rejected or i.reason:
                out.append(i)
        return out

    # preferences
    def upsert_preference(self, p):
        self._exec("REPLACE INTO preferences (id, user_id, text, kind, source, confidence, term, episode_ids, "
                   "created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                   (p.id, p.user_id, p.text, p.kind, p.source, p.confidence, p.term, _j(p.episode_ids),
                    (p.created_at or datetime.utcnow()).replace(tzinfo=None)))

    def preferences(self, user_id, kind=None, source=None):
        sql, args = "SELECT id, user_id, text, kind, source, confidence, term, episode_ids, created_at " \
                    "FROM preferences WHERE user_id=%s", [user_id]
        if kind:
            sql += " AND kind=%s"; args.append(kind)
        if source:
            sql += " AND source=%s"; args.append(source)
        out = []
        for r in self._rows(sql, tuple(args)):
            r["episode_ids"] = _load(r["episode_ids"], [])
            out.append(Preference(**r))
        return out


def _load(v: Any, default: Any) -> Any:
    if v is None:
        return default
    return json.loads(v) if isinstance(v, (str, bytes)) else v


# ---------------------------------------------------------------- access

_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = SingleStoreStore() if config.memory_backend() == "singlestore" else InMemoryStore()
    return _store


def set_store(store: Store | None) -> None:
    global _store
    _store = store
