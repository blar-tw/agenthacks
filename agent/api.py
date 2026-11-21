"""HTTP entry point for the UI and Firedrill (doc 5.1, 10.2).

Run: uv run uvicorn agent.api:app --port 8000, then open http://localhost:8000/
POST /chat {"message": str, "thread_id": str} -> {"reply": str, ...}
The web UI (sim_ui/static/index.html) may send an `action` instead of text;
it is turned into the same words a user would type (contract: sim_ui/README.md).
Firedrill's http.v1 driver passes its session key as thread_id, so both
interrupts carry across turns. Do not restart the server mid-drill: the
in-memory checkpointer loses paused runs.
"""

from __future__ import annotations

import contextvars
import hashlib
import threading
import uuid
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

load_dotenv()

from agent.graph import NODE_SOURCES, chat, get_graph  # noqa: E402
from contracts import get_audit, get_store, read_orders  # noqa: E402

try:   # P3's Statsig ranker flag and event sink; without a key the agent keeps its defaults
    from sim_ui import analytics  # noqa: E402
    analytics.install()
except Exception:  # analytics must never stop the API from starting
    analytics = None

UI_PAGE = Path(__file__).resolve().parent.parent / "sim_ui" / "static" / "index.html"

app = FastAPI(title="MealMatch")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])


class Action(BaseModel):
    type: Literal["select", "reject", "approve", "decline"]
    offer_id: Optional[str] = None
    reason: Optional[str] = None


class ChatIn(BaseModel):
    message: str = ""
    thread_id: Optional[str] = Field(None, description="Conversation key; Firedrill session key")
    session_id: Optional[str] = None  # alias some drivers send
    user_id: str = "demo"
    action: Optional[Action] = None  # from the web UI's buttons


def _action_text(action: Action, values: dict, waiting_for: Optional[str]) -> str:
    """The UI's button press, as the words the graph already understands."""
    want = {"select": "select_option", "reject": "select_option",
            "approve": "approve_purchase", "decline": "approve_purchase"}[action.type]
    if waiting_for != want:
        raise HTTPException(409, f"'{action.type}' needs a run waiting at {want}; this thread is at "
                                 f"{waiting_for or 'no paused step'}")
    reason = (action.reason or "").strip()
    if action.type == "approve":
        return "yes"
    if action.type == "decline":
        return f"no, {reason}" if reason else "no"
    ids = [o["offer"]["offer_id"] for o in values.get("shown") or []]
    if action.offer_id not in ids:
        raise HTTPException(400, f"offer {action.offer_id} is not one of the options shown")
    n = ids.index(action.offer_id) + 1
    if action.type == "select":
        return str(n)
    return f"not {n}: {reason}" if reason else f"not {n}"


# ---- live audit tap: the UI polls GET /live/{thread_id} while POST /chat is still running.
# It copies each audit event as the store receives it; the store itself is unchanged.
_LIVE_THREAD: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("live_thread", default=None)
_LIVE: dict[str, list[dict]] = {}          # run_id -> events in arrival order
_THREAD_RUN: dict[str, Optional[str]] = {}  # thread_id -> run_id of its current turn
_BUSY: set[str] = set()
_LIVE_LOCK = threading.Lock()


def _live_copy(events) -> None:
    thread = _LIVE_THREAD.get()
    with _LIVE_LOCK:
        for e in events:
            _LIVE.setdefault(e.run_id, []).append(e.model_dump(mode="json"))
            if thread:
                _THREAD_RUN[thread] = e.run_id


def _installed_store():
    try:   # P2's store picks SingleStore or memory on first use; do it before tapping or reading
        from data import store as data_store
        data_store.install()
    except Exception:
        pass
    return get_store()


def _tap_store() -> None:
    store = _installed_store()
    if getattr(store, "_live_tapped", False):
        return
    add_one = store.add_audit

    def add_audit(event):
        add_one(event)
        _live_copy([event])
    store.add_audit = add_audit
    if hasattr(store, "add_audits"):
        add_many = store.add_audits

        def add_audits(events):
            add_many(events)
            _live_copy(events)
        store.add_audits = add_audits
    store._live_tapped = True


@app.post("/chat")
def post_chat(body: ChatIn) -> dict:
    thread = body.thread_id or body.session_id or f"t_{uuid.uuid4().hex[:10]}"
    config = {"configurable": {"thread_id": thread}}
    message = body.message
    snap = get_graph().get_state(config)
    paused = bool(snap.next and snap.interrupts)
    if body.action is not None:
        waiting = snap.interrupts[0].value.get("type") if paused else None
        message = _action_text(body.action, snap.values, waiting)
    if not message.strip():
        raise HTTPException(400, "send a message or an action")

    _tap_store()
    with _LIVE_LOCK:
        _THREAD_RUN[thread] = snap.values.get("run_id") if paused else None
        _BUSY.add(thread)
    token = _LIVE_THREAD.set(thread)
    try:
        out = chat(thread, message, body.user_id)
    finally:
        _LIVE_THREAD.reset(token)
        with _LIVE_LOCK:
            _THREAD_RUN[thread] = _THREAD_RUN.get(thread) or None
            _BUSY.discard(thread)
    v = get_graph().get_state(config).values
    status = out["status"]
    return {
        **out,
        "thread_id": thread,
        "constraints": v.get("constraints") or {},
        "ranked": (v.get("shown") or []) if status == "awaiting_selection" else [],
        "missing_platforms": v.get("missing_platforms") or [],
        "cart": v.get("cart"),
        "quote": v.get("quote"),
        "quote_ok": v.get("quote_ok"),
        "handoff_url": out.get("handoff_url") or v.get("handoff_url") or None,
        "audit": [e.model_dump(mode="json") for e in get_audit(out["run_id"])],
    }


@app.get("/audit/{run_id}")
def audit_trail(run_id: str) -> dict:
    events = get_audit(run_id)
    if not events:
        raise HTTPException(404, f"no audit events for {run_id}")
    return {"run_id": run_id, "events": [e.model_dump(mode="json") for e in events]}


@app.get("/live/{thread_id}")
def live(thread_id: str, after: int = 0) -> dict:
    """Audit events of the thread's current turn, from index `after` on, while it runs."""
    with _LIVE_LOCK:
        run_id = _THREAD_RUN.get(thread_id)
        events = list(_LIVE.get(run_id, [])[after:]) if run_id else []
        busy = thread_id in _BUSY
    return {"run_id": run_id, "after": after, "events": events, "busy": busy}


@app.get("/memory/{user_id}")
def memory(user_id: str) -> dict:
    """What the agent can recall about a user, for the UI's memory view."""
    store = _installed_store()
    out = {"user_id": user_id, "orders": [], "interactions": [], "preferences": []}
    for key, fn, args in (("orders", "read_orders", (user_id, 8)), ("interactions", "read_interactions", (user_id, 8)),
                          ("preferences", "read_preferences", (user_id,))):
        try:
            out[key] = getattr(store, fn)(*args)
        except Exception as e:  # the view is optional; never fail the page over it
            out[key + "_error"] = type(e).__name__
    return out


_MEMORY_TABLES = {   # the three reads load_memory makes (data/nodes.py), in the order it makes them
    "orders": "SELECT * FROM orders WHERE user_id=%s ORDER BY ordered_at DESC LIMIT 5",
    "preferences": "SELECT pref_id, user_id, text, kind, source, confidence, term, episode_ids, created_at "
                   "FROM preferences WHERE user_id=%s",
    "interactions": "SELECT * FROM interactions WHERE user_id=%s ORDER BY at DESC LIMIT 20",
}
_ALL_TABLES = ["users", "orders", "preferences", "interactions", "price_snapshots", "matches", "audit_events"]


def _cell(v):
    if isinstance(v, (bytes, bytearray)):
        return f"<{len(v)} bytes>"
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


@app.get("/memory/{user_id}/tables")
def memory_tables(user_id: str, limit: int = 60) -> dict:
    """Raw rows of the memory tables (every user, not only this one) and the size of every table,
    so the UI can replay load_memory's queries over what is really in the database."""
    store = _installed_store()
    out = {"user_id": user_id, "backend": type(store).__name__, "tables": [], "queries": []}
    rows_of = getattr(store, "_rows", None)
    if rows_of is None:   # in-memory store: only what this process holds
        out["queries"] = [
            {"table": "orders", "sql": _MEMORY_TABLES["orders"], "rows": [r for rs in getattr(store, "orders", {}).values() for r in rs]},
            {"table": "preferences", "sql": _MEMORY_TABLES["preferences"], "rows": [r for rs in getattr(store, "preferences", {}).values() for r in rs]},
            {"table": "interactions", "sql": _MEMORY_TABLES["interactions"], "rows": [r for rs in getattr(store, "interactions", {}).values() for r in rs]},
        ]
        return _jsonable_rows(out)
    for t in _ALL_TABLES:
        try:
            out["tables"].append({"name": t, "rows": rows_of(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]})
        except Exception:
            pass
    for t, sql in _MEMORY_TABLES.items():
        try:
            cols = "pref_id, user_id, text, kind, source, confidence, term, episode_ids, created_at, vec" if t == "preferences" else "*"
            mine = rows_of(f"SELECT {cols} FROM {t} WHERE user_id=%s", (user_id,))
            others = rows_of(f"SELECT {cols} FROM {t} WHERE user_id<>%s LIMIT %s", (user_id, limit))
            key = next(iter(mine[0] if mine else others[0] if others else {"x": 0}))
            rows = sorted(mine + others, key=lambda r: hashlib.md5(str(r.get(key)).encode()).hexdigest())
            out["queries"].append({"table": t, "sql": sql.replace("%s", f"'{user_id}'", 1).replace("%s", "", 1), "rows": rows})
        except Exception as e:
            out["queries"].append({"table": t, "sql": sql, "rows": [], "error": type(e).__name__})
    return _jsonable_rows(out)


def _jsonable_rows(out: dict) -> dict:
    for q in out["queries"]:
        q["rows"] = [{k: _cell(v) for k, v in r.items()} for r in q["rows"]]
    return out


UE_ONLY_COORDS = {   # street addresses from the Uber Eats store pages, geocoded by hand
    "taishancuisine": (37.7978, -122.4096), "dumplinghousesf": (37.7641, -122.4334),
    "dumplingstorybydumplinghome": (37.7896, -122.4337), "uniteddumplingschestnut": (37.8007, -122.4370),
    "sichuantastyrestaurant": (37.7822, -122.4805), "littlesichuan": (37.7981, -122.4054),
    "yuanbaojiaozi": (37.7635, -122.4807), "houseofdimsum": (37.7959, -122.4066),
}


@app.get("/places")
def places() -> dict:
    """Every restaurant in the saved Apify runs, with DoorDash's coordinates and which apps list it (for the map)."""
    import json
    import re
    fx = Path(__file__).resolve().parent.parent / "data" / "fixtures"
    import html
    key = lambda s: re.sub("[^a-z]", "", html.unescape(str(s)).lower())  # noqa: E731
    out: dict[str, dict] = {}
    try:
        for s in json.loads((fx / "doordash.json").read_text(encoding="utf-8")):
            if s.get("latitude") is not None and s.get("menu"):
                out[key(s["name"])] = {"key": key(s["name"]), "name": s["name"], "lat": s["latitude"], "lng": s["longitude"],
                                       "platforms": ["doordash"], "cuisines": s.get("cuisines") or []}
        for it in json.loads((fx / "ubereats.json").read_text(encoding="utf-8")):
            k = key(it.get("storeName", ""))
            p = out.get(k) or next((v for kk, v in out.items() if k.startswith(kk) or kk.startswith(k)), None)
            if p is None and k in UE_ONLY_COORDS:   # Uber Eats gives no coordinates; these are the store addresses
                lat, lng = UE_ONLY_COORDS[k]
                p = out[k] = {"key": k, "name": html.unescape(it.get("storeName", "")), "lat": lat, "lng": lng,
                              "platforms": [], "cuisines": []}
            if p and "ubereats" not in p["platforms"]:
                p["platforms"].append("ubereats")
    except Exception as e:
        return {"places": [], "error": type(e).__name__}
    return {"places": list(out.values())}


@app.get("/orders/{user_id}")
def orders(user_id: str) -> dict:
    return {"user_id": user_id, "orders": read_orders(user_id, 5)}


@app.get("/health")
def health() -> dict:
    get_graph()
    statsig = dict(analytics.STATE) if analytics else {"installed": False}
    return {"ok": True, "node_sources": NODE_SOURCES, "statsig": statsig}


@app.get("/app", include_in_schema=False)
def customer_ui() -> FileResponse:
    """The customer view: the same page without the process visuals and the audit trail."""
    return ui()


@app.get("/", include_in_schema=False)
def ui() -> FileResponse:
    if not UI_PAGE.exists():
        raise HTTPException(404, "web UI not found (sim_ui/static/index.html)")
    return FileResponse(UI_PAGE)
