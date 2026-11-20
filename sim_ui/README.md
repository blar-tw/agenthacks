# MealMatch UI

`static/index.html` is the whole web app: one file, no build step, no framework.
It talks to the agent over one endpoint, `POST /chat`. Until P1's LangGraph
server is up, `mock_agent.py` serves the same contract on top of
`contracts.py` (StubTools + real `audit()` events).

```bash
uv run uvicorn sim_ui.mock_agent:app --reload --port 8000
```

Open http://localhost:8000/. To point the page at another server, add
`?api=http://localhost:9000` (that server needs CORS, or serve
`static/index.html` from it at `/`).

## Contract the UI expects

`POST /chat`

```jsonc
{
  "thread_id": "thr_…" | null,   // null starts a thread; reuse it to resume
  "user_id": "demo",
  "message": "spicy noodles for 2 under $40, no pork",  // free-text turn
  "action": null                  // or one of:
    // {"type": "select",  "offer_id": "off_…"}           resumes select_option
    // {"type": "reject",  "offer_id": "off_…", "reason": "too slow"}
    // {"type": "approve"}                                 resumes the approval interrupt
    // {"type": "decline", "reason": "…"}
}
```

Response:

```jsonc
{
  "thread_id": "thr_…",
  "run_id": "run_…",
  "status": "awaiting_selection" | "awaiting_approval" | "awaiting_message" | "done",
  "reply": "text for the chat bubble",
  "constraints": { /* Constraints */ },
  "ranked": [ /* RankedOption, best first; reasons = [{"text", "evidence_ids"}] */ ],
  "missing_platforms": ["doordash"],
  "cart": { /* Cart before quote */ },
  "quote": { /* Cart after quote */ },
  "quote_ok": true,
  "handoff_url": "https://…",     // set when status == "done"
  "audit": [ /* AuditEvent, oldest first, for run_id */ ]
}
```

What the UI shows from it:

| status | UI |
|---|---|
| `awaiting_selection` | constraint pills, up to 3 option cards with Choose / Not this |
| `awaiting_approval` | cart with live quote, quote check, Approve / Decline |
| `done` | "Open <platform> to pay" link |
| any | `reply` bubble; audit trail panel rebuilt from `audit` |

Each reason's `evidence_ids` render as chips. Clicking one scrolls the audit
panel to that event. The page never sends or shows cookies, tokens or
payment details; payment happens on the platform after the handoff link.
