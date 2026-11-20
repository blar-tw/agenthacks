"""SingleStore: connect; write, read and drop a scratch table; run one DOT_PRODUCT query.
With --tables (automatic from 14:00 local time): the 7 tables from Brief 8.6 exist.
"""
import sys
import uuid
from datetime import datetime

from _common import Smoke

s = Smoke("singlestore")
if s.need("SINGLESTORE_HOST", "SINGLESTORE_USER", "SINGLESTORE_PASSWORD"):
    from data.store import TABLES, connect
    conn = None
    try:
        conn = connect()
        s.ok("connected")
    except Exception as exc:
        s.error("connect", exc)
    if conn is not None:
        name = f"smoke_{uuid.uuid4().hex[:8]}"
        try:
            with conn.cursor() as cur:
                cur.execute(f"CREATE TABLE {name} (id INT PRIMARY KEY, label VARCHAR(32), v VECTOR(3))")
                cur.execute(f"INSERT INTO {name} VALUES (1, 'noodles', '[1,0,0]'), (2, 'salad', '[0,1,0]')")
                cur.execute(f"SELECT label FROM {name} WHERE id = 1")
                row = cur.fetchone()
                if row and row["label"] == "noodles":
                    s.ok("write + read on a scratch table")
                else:
                    s.fail(f"read back {row!r}")
                cur.execute(f"SELECT label, DOT_PRODUCT(v, '[0.9,0.1,0]' :> VECTOR(3)) AS score "
                            f"FROM {name} ORDER BY score DESC LIMIT 1")
                top = cur.fetchone()
                if top and top["label"] == "noodles":
                    s.ok(f"DOT_PRODUCT query ranks 'noodles' first (score {float(top['score']):.2f})")
                else:
                    s.fail(f"DOT_PRODUCT top row {top!r}")
        except Exception as exc:
            s.error("scratch table", exc)
        finally:
            try:
                with conn.cursor() as cur:
                    cur.execute(f"DROP TABLE IF EXISTS {name}")
                s.ok("scratch table dropped")
            except Exception as exc:
                s.error("drop", exc)

        if "--tables" in sys.argv or datetime.now().hour >= 14:
            try:
                with conn.cursor() as cur:
                    cur.execute("SHOW TABLES")
                    have = {list(r.values())[0] for r in cur.fetchall()}
                missing = [t for t in TABLES if t not in have]
                if missing:
                    s.fail(f"missing tables: {', '.join(missing)} (run: uv run python -m data.setup_db)")
                else:
                    s.ok(f"all {len(TABLES)} tables exist")
            except Exception as exc:
                s.error("show tables", exc)
        conn.close()
s.done()
