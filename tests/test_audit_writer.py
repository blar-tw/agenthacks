"""The SingleStore audit writer and schema, against a recording fake connection (no database)."""
import json

import contracts as c
from data import store


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, args=None):
        self.log.append((sql, args))

    def executemany(self, sql, rows):
        self.log.extend((sql, r) for r in rows)

    def fetchall(self):
        return []


class FakeConn:
    def __init__(self):
        self.log = []

    def cursor(self):
        return FakeCursor(self.log)


def test_audit_event_becomes_one_row_with_hash():
    conn = FakeConn()
    c.set_store(store.SingleStoreStore(conn))
    eid = c.audit("evidence", {"price": 12.5, "session_cookie": "x"}, ["ev_1"], run_id="ra", node="normalize",
                  source_url="https://x", actor_run_id="act_1")
    sql, args = conn.log[-1]
    assert sql.startswith("INSERT INTO audit_events")
    assert args[0] == eid and args[1:3] == ("ra", "normalize") and args[4] == "evidence"
    assert args[5:7] == ("https://x", "act_1")
    assert json.loads(args[7]) == {"price": 12.5}          # the contract scrubbed the cookie
    assert json.loads(args[8]) == ["ev_1"] and len(args[9]) == 64


def test_schema_has_the_seven_tables_with_vectors():
    stmts = store.schema_statements()
    names = [s.split()[5] for s in stmts]
    assert names == list(store.TABLES)
    from data import config
    assert sum(f"VECTOR({config.EMBED_DIM})" in s for s in stmts) == 2   # preferences.vec, price_snapshots.dish_vec
    joined = " ".join(stmts).lower()
    assert not any(w in joined for w in ("password", "cookie", "token", "card"))


def test_snapshots_and_matches_write_in_batches():
    conn = FakeConn()
    c.set_store(store.SingleStoreStore(conn))
    offers = [c.Offer(restaurant="A", item=f"dish {i}", price=10 + i, source="doordash") for i in range(3)]
    store.write_snapshots("rb", offers)
    store.write_matches("rb", [{"match_id": "m1", "offer_ids": ["a", "b"], "confidence": 0.9, "method": "vector"}])
    assert sum(sql.startswith("REPLACE INTO price_snapshots") for sql, _ in conn.log) == 3
    assert any(sql.startswith("REPLACE INTO matches") for sql, _ in conn.log)
