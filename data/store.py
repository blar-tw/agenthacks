"""P2's memory store. Implements contracts.MemoryStore (dicts in, dicts out).

- P2MemoryStore: P1's InMemoryStore plus P2's extras. Used until SingleStore is configured.
- SingleStoreStore: the source of truth once MEMORY_BACKEND=singlestore.

Extras beyond the contract (distiller bookmark, matches, dish vectors, batch snapshots) are
reached through the helper functions at the bottom. They also work on P1's plain store.
Nodes never write SQL.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

import contracts as c
from data import config

TABLES = ("users", "orders", "interactions", "preferences", "price_snapshots", "matches", "audit_events")


def to_dt(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v if v is None or v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _naive(v: Any) -> datetime | None:
    d = to_dt(v)
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d else None


def pref_key(user_id: str, pref: dict) -> str:
    """Learned preferences upsert by text, so a distiller re-run updates instead of duplicating."""
    if pref.get("pref_id"):
        return pref["pref_id"]
    slug = re.sub(r"[^a-z0-9]+", "-", str(pref.get("text", "")).lower()).strip("-")[:80]
    return f"pref_{user_id}_{pref.get('source', 'stated')}_{slug}"


def audit_hash(e: c.AuditEvent) -> str:
    return hashlib.sha256(json.dumps(e.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------- in memory

class P2MemoryStore(c.InMemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.bookmarks: dict[str, datetime] = {}
        self.match_rows: list[dict] = []
        self.vectors: dict[str, list[float]] = {}

    def write_preference(self, user_id: str, pref: dict) -> str:
        pid = pref_key(user_id, pref)
        rows = [p for p in self.preferences.get(user_id, []) if p.get("pref_id") != pid]
        self.preferences[user_id] = rows + [{**c.scrub(pref), "pref_id": pid}]
        return pid

    def write_interaction(self, user_id: str, interaction: dict) -> str:
        iid = interaction.get("interaction_id") or c.new_id("int")
        self.interactions.setdefault(user_id, []).append({**c.scrub(interaction), "interaction_id": iid})
        return iid

    def last_distilled_at(self, user_id: str) -> datetime | None:
        return self.bookmarks.get(user_id)

    def set_last_distilled(self, user_id: str, at: datetime) -> None:
        self.bookmarks[user_id] = at

    def write_matches(self, run_id: str, matches: Iterable[dict]) -> None:
        self.match_rows.extend({**m, "run_id": run_id} for m in matches)

    def set_snapshot_vectors(self, vectors: dict[str, list[float]]) -> None:
        self.vectors.update(vectors)


# ---------------------------------------------------------------- SingleStore

def _j(v: Any) -> str:
    return json.dumps(v, default=str)


def _load(v: Any, default: Any) -> Any:
    if v is None:
        return default
    return json.loads(v) if isinstance(v, (str, bytes)) else v


def connect():
    import pymysql
    return pymysql.connect(
        host=config.env("SINGLESTORE_HOST"),
        port=int(config.env("SINGLESTORE_PORT", "3306")),
        user=config.env("SINGLESTORE_USER"),
        password=config.env("SINGLESTORE_PASSWORD"),
        database=config.env("SINGLESTORE_DATABASE") or None,
        autocommit=True,
        connect_timeout=10,
        ssl={"ssl": {}} if config.env("SINGLESTORE_SSL", "1") != "0" else None,
        cursorclass=pymysql.cursors.DictCursor,
    )


def schema_statements() -> list[str]:
    sql = (config.ROOT / "data" / "schema.sql").read_text(encoding="utf-8").replace("{dim}", str(config.EMBED_DIM))
    body = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


VECTOR_COLUMNS = (("preferences", "vec"), ("price_snapshots", "dish_vec"))


def create_tables(conn) -> list[str]:
    with conn.cursor() as cur:
        for stmt in schema_statements():
            cur.execute(stmt)
    return list(TABLES)


def ensure_vector_dims(conn) -> list[str]:
    """Rebuild a vector column whose size differs from EMBED_DIM (after an embedding-model switch).
    Old vectors from another model can't be compared with new ones, so they are dropped."""
    want = f"vector({config.EMBED_DIM}"
    changed = []
    with conn.cursor() as cur:
        for table, col in VECTOR_COLUMNS:
            cur.execute("SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s", (table, col))
            row = cur.fetchone()
            if row and not str(row["COLUMN_TYPE"]).lower().startswith(want):
                cur.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} VECTOR({config.EMBED_DIM})")
                changed.append(f"{table}.{col}: {row['COLUMN_TYPE']} -> VECTOR({config.EMBED_DIM})")
    return changed


class SingleStoreStore:
    def __init__(self, conn=None) -> None:
        self.conn = conn or connect()

    def _exec(self, sql: str, args: Any = None, many: bool = False) -> None:
        with self.conn.cursor() as cur:
            (cur.executemany if many else cur.execute)(sql, args)

    def _rows(self, sql: str, args: Any = None) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            return list(cur.fetchall())

    # audit
    _AUDIT_SQL = ("INSERT INTO audit_events (event_id, run_id, node, ts, kind, source_url, actor_run_id, payload, "
                  "evidence_ids, hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")

    @staticmethod
    def _audit_row(e: c.AuditEvent) -> tuple:
        return (e.event_id, e.run_id, e.node, _naive(e.ts), e.kind, e.source_url, e.actor_run_id,
                _j(e.payload), _j(e.evidence_ids), audit_hash(e))

    def add_audit(self, e: c.AuditEvent) -> None:
        self._exec(self._AUDIT_SQL, self._audit_row(e))

    def add_audits(self, events: list[c.AuditEvent]) -> None:
        """One multi-row INSERT: 600 evidence events in one round trip, not 600."""
        for i in range(0, len(events), 500):
            self._exec(self._AUDIT_SQL, [self._audit_row(e) for e in events[i:i + 500]], many=True)

    def get_audit(self, run_id: str) -> list[c.AuditEvent]:
        out = []
        for r in self._rows("SELECT * FROM audit_events WHERE run_id=%s ORDER BY ts, event_id", (run_id,)):
            r.pop("hash", None)
            r["payload"], r["evidence_ids"] = _load(r["payload"], {}), _load(r["evidence_ids"], [])
            r["ts"] = to_dt(r["ts"])
            out.append(c.AuditEvent(**r))
        return out

    # orders
    def write_order(self, user_id: str, order: dict) -> str:
        o = c.scrub(order)
        oid = o.get("order_id") or c.new_id("ord")
        self._exec("REPLACE INTO orders (order_id, user_id, run_id, platform, restaurant, items, cuisine, price_paid, "
                   "fees, eta_min, rating, ordered_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                   (oid, user_id, o.get("run_id"), o.get("platform"), o.get("restaurant"), _j(o.get("items", [])),
                    o.get("cuisine"), o.get("price_paid"), o.get("fees"), o.get("eta_min"), o.get("rating"),
                    _naive(o.get("ordered_at") or c.now())))
        return oid

    def read_orders(self, user_id: str, limit: int = 5) -> list[dict]:
        rows = self._rows("SELECT * FROM orders WHERE user_id=%s ORDER BY ordered_at DESC LIMIT %s", (user_id, limit))
        for r in rows:
            r["items"] = _load(r["items"], [])
            r["ordered_at"] = to_dt(r["ordered_at"])
        return rows

    # interactions
    def write_interaction(self, user_id: str, interaction: dict) -> str:
        i = c.scrub(interaction)
        iid = i.get("interaction_id") or c.new_id("int")
        self._exec("REPLACE INTO interactions (interaction_id, user_id, run_id, shown, selected, rejected, "
                   "rejected_restaurants, reason, at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                   (iid, user_id, i.get("run_id"), _j(i.get("shown", [])), i.get("selected"),
                    _j(i.get("rejected", [])), _j(i.get("rejected_restaurants", [])), i.get("reason"),
                    _naive(i.get("at") or c.now())))
        return iid

    def read_interactions(self, user_id: str, limit: int = 20) -> list[dict]:
        rows = self._rows("SELECT * FROM interactions WHERE user_id=%s ORDER BY at DESC LIMIT %s", (user_id, limit))
        for r in rows:
            for k in ("shown", "rejected", "rejected_restaurants"):
                r[k] = _load(r[k], [])
            r["at"] = to_dt(r["at"])
        return rows

    # preferences
    def write_preference(self, user_id: str, pref: dict) -> str:
        p = c.scrub(pref)
        pid = pref_key(user_id, p)
        self._exec("REPLACE INTO preferences (pref_id, user_id, text, kind, source, confidence, term, episode_ids, "
                   "created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                   (pid, user_id, p.get("text"), p.get("kind", "soft"), p.get("source", "stated"),
                    p.get("confidence", 1.0), p.get("term"), _j(p.get("episode_ids", [])),
                    _naive(p.get("created_at") or c.now())))
        return pid

    def read_preferences(self, user_id: str) -> list[dict]:
        rows = self._rows("SELECT pref_id, user_id, text, kind, source, confidence, term, episode_ids, created_at "
                          "FROM preferences WHERE user_id=%s", (user_id,))
        for r in rows:
            r["episode_ids"] = _load(r["episode_ids"], [])
        return rows

    # price snapshots
    def write_price_snapshots(self, run_id: str, offers: Iterable[c.Offer]) -> None:
        self._exec("REPLACE INTO price_snapshots (offer_id, run_id, source, restaurant, restaurant_address, item, "
                   "description, price, fees, eta_min, url, scraped_at, evidence_id) "
                   "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                   [(o.offer_id, run_id, o.source, o.restaurant, o.restaurant_address, o.item, o.description,
                     o.price, o.fees, o.eta_min, o.url, _naive(o.scraped_at), o.evidence_id) for o in offers],
                   many=True)

    def write_price_snapshot(self, offer: c.Offer) -> str:
        self.write_price_snapshots(c._AUDIT_CTX.get()[0], [offer])
        return offer.offer_id

    def read_price_snapshots(self, run_id: str | None = None) -> list[dict]:
        cols = ("offer_id, run_id, source, restaurant, restaurant_address, item, description, price, fees, "
                "eta_min, url, scraped_at, evidence_id")
        if run_id:
            return self._rows(f"SELECT {cols} FROM price_snapshots WHERE run_id=%s", (run_id,))
        return self._rows(f"SELECT {cols} FROM price_snapshots ORDER BY scraped_at DESC LIMIT 500")

    # extras
    def last_distilled_at(self, user_id: str) -> datetime | None:
        rows = self._rows("SELECT last_distilled_at FROM users WHERE user_id=%s", (user_id,))
        return to_dt(rows[0]["last_distilled_at"]) if rows else None

    def set_last_distilled(self, user_id: str, at: datetime) -> None:
        self._exec("INSERT INTO users (user_id, settings, last_distilled_at) VALUES (%s, %s, %s) "
                   "ON DUPLICATE KEY UPDATE last_distilled_at = VALUES(last_distilled_at)",
                   (user_id, _j({}), _naive(at)))

    def ensure_user(self, user_id: str, address_label: str | None = None) -> None:
        self._exec("INSERT IGNORE INTO users (user_id, address_label, settings) VALUES (%s, %s, %s)",
                   (user_id, address_label, _j({})))

    def write_matches(self, run_id: str, matches: Iterable[dict]) -> None:
        self._exec("REPLACE INTO matches (match_id, run_id, offer_ids, confidence, method, evidence_ids, created_at) "
                   "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                   [(m["match_id"], run_id, _j(m["offer_ids"]), m["confidence"], m.get("method"),
                     _j(m.get("evidence_ids", [])), _naive(c.now())) for m in matches], many=True)

    def set_snapshot_vectors(self, vectors: dict[str, list[float]]) -> None:
        """One UPDATE ... CASE per 100 rows: pymysql's executemany batches only INSERT/REPLACE,
        so a plain UPDATE per row cost ~80 ms each (617 rows: 50 s)."""
        items = list(vectors.items())
        for i in range(0, len(items), 100):
            chunk = items[i:i + 100]
            cases = " ".join("WHEN %s THEN %s" for _ in chunk)
            args: list = []
            for k, v in chunk:
                args += [k, json.dumps([round(float(x), 6) for x in v])]
            args += [k for k, _ in chunk]
            marks = ",".join(["%s"] * len(chunk))
            self._exec(f"UPDATE price_snapshots SET dish_vec = CASE offer_id {cases} END "
                       f"WHERE offer_id IN ({marks})", args)


# ---------------------------------------------------------------- helpers (work on any MemoryStore)

_BOOKMARKS: dict[str, datetime] = {}


def last_distilled_at(user_id: str) -> datetime | None:
    s = c.get_store()
    return to_dt(s.last_distilled_at(user_id)) if hasattr(s, "last_distilled_at") else _BOOKMARKS.get(user_id)


def set_last_distilled(user_id: str, at: datetime) -> None:
    s = c.get_store()
    if hasattr(s, "set_last_distilled"):
        s.set_last_distilled(user_id, at)
    else:
        _BOOKMARKS[user_id] = at


def audit_many(kind: str, items: list[tuple[dict, list[str], str | None]], *, run_id: str, node: str) -> list[str]:
    """Batch version of contracts.audit: items are (payload, evidence_ids, source_url).
    Same event shape and scrubbing as contracts.audit; one write for the whole batch."""
    events = [c.AuditEvent(run_id=run_id, node=node, kind=kind, payload=c.scrub(p), evidence_ids=list(ev),
                           source_url=url) for p, ev, url in items]
    s = c.get_store()
    if hasattr(s, "add_audits"):
        s.add_audits(events)
    else:
        for e in events:
            s.add_audit(e)
    return [e.event_id for e in events]


def write_snapshots(run_id: str, offers: list[c.Offer]) -> None:
    s = c.get_store()
    if hasattr(s, "write_price_snapshots"):
        s.write_price_snapshots(run_id, offers)
    else:
        for o in offers:
            s.write_price_snapshot(o)


def write_matches(run_id: str, matches: list[dict]) -> None:
    s = c.get_store()
    if matches and hasattr(s, "write_matches"):
        s.write_matches(run_id, matches)


def set_snapshot_vectors(vectors: dict[str, list[float]]) -> None:
    s = c.get_store()
    if vectors and hasattr(s, "set_snapshot_vectors"):
        s.set_snapshot_vectors(vectors)


def install() -> None:
    """Set the contract's store: SingleStore when configured, else P2's in-memory store."""
    s = c.get_store()
    if config.memory_backend() == "singlestore":
        if not isinstance(s, SingleStoreStore):
            c.set_store(SingleStoreStore())
    elif type(s) is c.InMemoryStore and not s.audit:
        c.set_store(P2MemoryStore())
