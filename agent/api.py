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
from contracts import get_audit, read_orders  # noqa: E402

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


@app.post("/chat")
def post_chat(body: ChatIn) -> dict:
    thread = body.thread_id or body.session_id or f"t_{uuid.uuid4().hex[:10]}"
    config = {"configurable": {"thread_id": thread}}
    message = body.message
    if body.action is not None:
        snap = get_graph().get_state(config)
        waiting = snap.interrupts[0].value.get("type") if snap.next and snap.interrupts else None
        message = _action_text(body.action, snap.values, waiting)
    if not message.strip():
        raise HTTPException(400, "send a message or an action")

    out = chat(thread, message, body.user_id)
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


@app.get("/orders/{user_id}")
def orders(user_id: str) -> dict:
    return {"user_id": user_id, "orders": read_orders(user_id, 5)}


@app.get("/health")
def health() -> dict:
    get_graph()
    return {"ok": True, "node_sources": NODE_SOURCES}


@app.get("/", include_in_schema=False)
def ui() -> FileResponse:
    if not UI_PAGE.exists():
        raise HTTPException(404, "web UI not found (sim_ui/static/index.html)")
    return FileResponse(UI_PAGE)
