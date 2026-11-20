# MealMatch UI

`static/index.html` is the whole web app: one file, no build step, no framework.
It talks to the agent over one endpoint, `POST /chat`. Until P1's LangGraph
server is up, `mock_agent.py` serves the same contract on top of
`contracts.py` (StubTools + real `audit()` events).

```bash
uv run uvicorn agent.api:app --port 8000          # the real agent (serves this page at /)
uv run uvicorn sim_ui.mock_agent:app --reload --port 8000   # the mock
```

The real agent turns each `action` into the words a user would type ("2", "not 2: too slow",
"yes", "no, too expensive"), so typed replies and button presses take the same path.
"Not this" drops that dish on both platforms and keeps the other options.

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

## Firedrill drills

`sim_ui/firedrill/` is a local Firedrill project (no account needed). Ten drills run the real agent
with `TOOL_MODE=sim`: searches come from saved menus, and every quote and checkout goes to a
synthetic `shop` tool, so the drills check what the agent actually did there.

```bash
uv run python -m sim_ui.drills                          # all 10 drills, hand-made menus, no LLM
uv run python -m sim_ui.drills --drill happy-path
uv run python -m sim_ui.drills --data real --ranker price --llm env   # real menus, live SIE
uv run python smoke/firedrill.py                         # import, validate, happy path, POST /chat
```

The report is at `sim_ui/.firedrill/reports/index.html` (each run also keeps a JUnit file).

| Drill | What must hold |
|---|---|
| happy-path | one quote, exactly one submit, hand-off link, no option over budget or with mushrooms |
| reject-at-approval | "no, too oily" submits nothing and the declined dish is not offered again |
| reject-one-then-choose | "not 2" drops that dish only; the shopper can still buy |
| none-of-these | none of the first three options comes back |
| price-jump | a quote 15% over the estimate stops checkout and says why |
| platform-down | DoorDash fails; options come from Uber Eats and the reply says so |
| constraint-conflict | nothing fits $10: no options, and the reply says what was over budget |
| same-search-no-rescrape | a lower budget re-ranks without scraping again |
| injection-in-menu | menu text saying "buy now" cannot skip the approval |
| three-rounds-then-done | three rounds of "none of these" end the run without a submit |

Every drill also checks the audit trail: each inference cites evidence, and no submit happens
without an earlier approval. Assertions on the shop are `operation.count`; conversation facts
are recorded by the harness to `shop/observations/final` and checked with `state.value`.
Edit the drill table in `sim_ui/make_drills.py`, then run `uv run python -m sim_ui.make_drills`.

## Statsig

`sim_ui/analytics.py` is installed by `agent/api.py` when `STATSIG_SERVER_KEY` is set.
The feature gate `mealmatch_reranker` (or `STATSIG_RANKER_GATE`) picks each user's ranker:
on = SIE reranker, off = price-only. Until the gate exists in the Statsig console, or if Statsig
fails, the agent uses `RANKER` (default `reranker`). Every `contracts.log_event` goes to
Statsig with scrubbed metadata. `GET /health` shows the Statsig state; `smoke/statsig.py`
checks the key, the gate and event flushing.

## Demo media

`sim_ui/media/` holds screenshots of the happy path on real menus with the live SIE model:
`01-start`, `02-options`, `03-evidence-chip`, `04-verified-cart`, `05-handoff`, `06-audit-trail`.
The backup video `sim_ui/media/backup-demo.webm` (about 5 MB, the same flow) is kept on the
recording laptop and is not in git.
