# data/ — P2: data, matching and memory

## Nodes for the graph (`data/nodes.py`)

Each node takes `AgentState` and returns a partial update.

| Node | Reads | Writes to state | Audit kind |
| --- | --- | --- | --- |
| `load_memory` | `user_id`, `request` | `memory` | evidence + inference |
| `scrape` (async; or `scrape_ubereats` / `scrape_doordash`) | `constraints.location` | `search_results`, `missing_platforms` | evidence |
| `normalize` | `search_results` | `offers` (each with `evidence_id`) | evidence, one per offer |
| `match_offers` | `offers` | `matches` | inference |
| `filter_rank` | `offers`, `matches`, `constraints`, `memory`, `rejected`, `ranker` | `ranked`, `ranker` | inference |
| `persist_learn` | `selected`, `approved`, `quote`, `rejected`, `rejection_reason` | `episode_ids` | action |
| `distill_preferences` | `user_id` | nothing (runs in a background thread) | inference |

For parse_request and handle_feedback, `save_stated_rule(user_id, text)` stores a stated hard rule.
It is the only way a hard rule gets created.

## Modes (`.env`)

- `TOOL_MODE=fixtures | live | sim`. Use `set_tools()` to register P3's Firedrill tools for sim mode.
- `MEMORY_BACKEND=memory | singlestore`
- `RANKER=reranker | price`. The Statsig flag can also set `state["ranker"]`.
- `DISTILLER=cognee | counting`

## Commands

```
uv run pytest
uv run python smoke/run_all.py           # each smoke check; add --live for Apify
uv run python smoke/apify.py --live --save   # replace the synthetic fixtures with a real run
uv run python -m data.setup_db           # create the 7 SingleStore tables
uv run python -m data.seed_demo          # 5 demo orders, the "no mushrooms" rule, the distiller run
uv sync --extra memory                   # install Cognee
```

## Fallbacks already built in

- SIE embeddings fail: match on fuzzy names inside the same restaurant (cut list item 6).
- Reranker fails: use the price-only ranker. The audit trail records the switch.
- One Actor fails or times out after 90 s: continue with the other platform and record the missing one.
- Cognee fails: use the counting distiller (cut list item 1).
