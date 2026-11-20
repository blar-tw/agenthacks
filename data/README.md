# data/ — P2: data, matching and memory

## Nodes for the graph (`data/nodes.py`)

Importing `data.nodes` registers P2's `fixtures` and `live` tools (`contracts.register_tools`).
It also installs P2's memory store (`contracts.set_store`). Each node takes `AgentState` and
returns a partial update, as dicts.

| Node | Reads | Writes to state | Audit kind |
| --- | --- | --- | --- |
| `load_memory` | `user_id`, `request_text` | `memory` = {orders, rules, soft_prefs, rejections} | evidence + inference |
| `scrape` (both platforms at once, 90 s each) | `constraints` | `offers`, `missing_platforms` | evidence (`scrape_ubereats`, `scrape_doordash`) |
| `normalize` | `offers` | `offers`, each with `evidence_id` | evidence, one per offer |
| `match_offers` | `offers` | `matches` (`offer_ids` = [ubereats, doordash]) | inference |
| `filter_rank` | `offers`, `matches`, `constraints`, `memory`, `rejected`, `ranker` | `ranked`, `ranker` | inference |
| `persist_learn` | `selected`, `approval`, `quote`, `rejected`, `feedback` | nothing | action |
| `distill_preferences` | `user_id` | nothing (runs in a background thread) | inference |

`scrape` replaces two separate scrape nodes, because both would write `offers`.
In a matched pair, `filter_rank` keeps the cheaper platform. `match_id` leads explain to the other one.

For parse_request and handle_feedback, `save_stated_rule(user_id, text)` stores a stated hard rule.
It is the only way a hard rule gets created.

## Modes (`.env`)

- `TOOL_MODE=fixtures | live | sim`. P3 registers `sim`.
- `MEMORY_BACKEND=memory | singlestore`
- `RANKER=reranker | price`. The Statsig flag can also set `state["ranker"]`.
- `DISTILLER=cognee | counting`

## Commands

```
uv run pytest
uv run python smoke/run_all.py              # each smoke check; add --live for Apify
uv run python smoke/apify.py --live --save  # replace the synthetic fixtures with a real run
uv run python -m data.setup_db              # create the 7 SingleStore tables
uv run python -m data.seed_demo             # 5 demo orders, the "no mushrooms" rule, the distiller run
uv sync --extra memory                      # install Cognee
```

## Fallbacks already built in

- SIE embeddings fail: match on fuzzy names inside the same restaurant (cut list item 6).
- Reranker fails or isn't configured: use the price-only ranker. The audit trail records the switch.
- One platform fails or times out after 90 s: continue with the other and record the missing one.
- Cognee fails: use the counting distiller (cut list item 1).

## Known gap in the contract

`Offer.fees` is a float that defaults to 0.0. The Uber Eats Actor reports no fee, so those offers
show $0 fees. The audit payload marks them `fees_known: false`, and verify_quote reads the real total.
P1 has been asked to consider `Optional[float]`.
