"""HTTP entry point for the UI and Firedrill (doc 5.1, 10.2).

Run: uv run uvicorn agent.api:app --port 8000
POST /chat {"message": str, "thread_id": str} -> {"reply": str, ...}
Firedrill's http.v1 driver passes its session key as thread_id, so both
interrupts carry across turns. Do not restart the server mid-drill: the
in-memory checkpointer loses paused runs.
"""

from __future__ import annotations

import uuid
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

load_dotenv()

from agent.graph import NODE_SOURCES, chat, get_graph  # noqa: E402
from contracts import get_audit  # noqa: E402

app = FastAPI(title="MealMatch")


class ChatIn(BaseModel):
    message: str
    thread_id: Optional[str] = Field(None, description="Conversation key; Firedrill session key")
    session_id: Optional[str] = None  # alias some drivers send
    user_id: str = "demo"


@app.post("/chat")
def post_chat(body: ChatIn) -> dict:
    thread = body.thread_id or body.session_id or f"t_{uuid.uuid4().hex[:10]}"
    out = chat(thread, body.message, body.user_id)
    return {**out, "thread_id": thread}


@app.get("/audit/{run_id}")
def audit_trail(run_id: str) -> dict:
    events = get_audit(run_id)
    if not events:
        raise HTTPException(404, f"no audit events for {run_id}")
    return {"run_id": run_id, "events": [e.model_dump(mode="json") for e in events]}


@app.get("/health")
def health() -> dict:
    get_graph()
    return {"ok": True, "node_sources": NODE_SOURCES}
