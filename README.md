# MealMatch

MealMatch is a food-ordering agent. It compares Uber Eats and DoorDash for the same dish, remembers what the user likes, builds a verified cart and asks for approval. Then it hands the user a link to pay on the platform. It never pays. Every step leaves an audit trail. That trail splits evidence (scraped prices, with source and time) from inference (matches, rankings, learned preferences), and every inference cites the evidence behind it.

Built for the Personal Agents Hackathon, November 20, 2026.

## How a request flows

Request: "Spicy Chinese food for two, under $40, within 40 minutes, no mushrooms."

1. `load_memory` reads past orders and hard rules from SingleStore, and learned preferences from SingleStore and Cognee.
2. `parse_request` turns the text into constraints (SIE, Qwen3.8 27B).
3. `scrape` runs the Uber Eats and DoorDash Apify Actors at the same time, 90 s each. One platform is enough.
4. `normalize` turns both into one offer format. Each price becomes an evidence event and a price snapshot.
5. `match_offers` pairs the same dish across platforms. It matches the restaurant first, then the dish (SIE embeddings, price ratio, main ingredient).
6. `filter_rank` removes anything that breaks a hard rule, the budget, the ETA or the request, then ranks the rest. The SIE reranker or the price-only ranker does the ranking, and a Statsig flag picks which.
7. `explain` gives reasons marked as evidence or inference. The user picks an option, or says "none of these".
8. `build_cart` and `verify_quote` read the real total. A price more than 10% over the estimate blocks the cart.
9. `approve_purchase` waits for an explicit yes ("yes", "yes please"). `submit_or_handoff` returns the deep link.
10. `persist_learn` saves the order or the rejection. `distill_preferences` turns repeated episodes into learned soft preferences in the background. It never creates a hard rule.

## Tools

| Tool | Job in MealMatch |
| --- | --- |
| Apify | `corent1robert/uber-eats-menu-scraper` and `haketa/doordash-scraper` fetch menus and prices |
| LangGraph | The graph, its checkpointer and the 2 pauses for the user |
| FastAPI | `POST /chat`, `GET /audit/{run_id}`, and the web UI at `/` |
| Superlinked SIE | Chat (Qwen3.8 27B), dish embeddings (`tencent/R3-embedding-0.6b`), reranker (Qwen3 Reranker 4B) |
| SingleStore | Orders, interactions, rules, price snapshots with dish vectors, matches, and the audit log |
| Cognee Cloud | Learned soft preferences, with episode IDs in the text |
| Firedrill | Full-conversation drills against a simulated shop |
| Statsig | The ranker flag and events |
| OpenRouter | Fallback chat model, capped at $20 |

## Layout

| Folder | Owner | Contents |
| --- | --- | --- |
| `agent/` | P1 | Graph, LLM calls and prompts, API |
| `data/` | P2 | Actors, normalize, matching, ranking, memory store, distiller ([data/README.md](data/README.md)) |
| `sim_ui/` | P3 | Web UI and Firedrill tools ([sim_ui/README.md](sim_ui/README.md)) |
| `contracts.py` | P1 | Shared data shapes, tool interface, `audit()` |
| `smoke/` | all | Checks against the real services |

## Results

### Ranker comparison

The same 6 requests went through both rankers on real menus: 5 San Francisco Sichuan and Chinese restaurants scraped from both platforms, 617 offers. The demo user's memory includes a stated "no mushrooms" rule and the learned preference "likes spicy noodles". To reproduce it, run `uv run python -m data.compare_rankers`.

| Request | Ranker | Top pick | Avg total, top 3 | Matches request | Matches learned prefs | Rule breaks |
| --- | --- | --- | --- | --- | --- | --- |
| Spicy Chinese noodles for two, under $40, no mushrooms | price | House Cold Noodle, appetizer size (Z&Y Restaurant, Uber Eats) | $21.96 | 67% | 67% | 0 |
| | reranker | Tan Tan Noodle (Z&Y Restaurant, Uber Eats) | $31.93 | 100% | 100% | 0 |
| Dumplings for one, under $20 | price | Pot Sticker, 6 pcs (Sichuan Home, Uber Eats) | $12.28 | 100% | 0% | 0 |
| | reranker | Spicy Dumpling, 6 pcs (Z & Y Peking Duck, Uber Eats) | $15.65 | 100% | 33% | 0 |
| Something spicy with chicken for two, under $60 | price | House Cold Noodle, appetizer size (Z&Y Restaurant, Uber Eats) | $21.96 | 50% | 67% | 0 |
| | reranker | Spicy Chicken with Peppercorns (Z&Y Restaurant, Uber Eats) | $45.93 | 100% | 50% | 0 |
| Tofu dish, no pork, under $25 | price | Tofu and Preserved Eggs (Sichuan Home, Uber Eats) | $14.95 | 100% | 33% | 0 |
| | reranker | General Chow's Tofu (Sichuan Home, Uber Eats) | $17.28 | 100% | 33% | 0 |
| Soup for two, under $50, no peanuts | price | Sweet Sticky Rice Ball with Soup (Z&Y Restaurant, Uber Eats) | $28.57 | 100% | 17% | 0 |
| | reranker | Hot & Sour Soup (Spices, DoorDash) | $33.29 | 100% | 67% | 0 |
| Cheap noodles, under $15 | price | House Cold Noodle, appetizer size (Z&Y Restaurant, Uber Eats) | $12.28 | 100% | 100% | 0 |
| | reranker | House Cold Noodle, appetizer size (Z&Y Restaurant, Uber Eats) | $12.28 | 100% | 100% | 0 |

Averaged over the 6 requests, the top 3 picks scored as follows:

| Ranker | Avg total | Matches request | Matches learned prefs | Rule breaks |
| --- | --- | --- | --- | --- |
| price | $18.67 | 86% | 47% | 0 |
| reranker | $26.06 | 100% | 64% | 0 |

The reranker matched every request word and more of the learned preferences. The price ranker was $7.39 cheaper on average, because it picks the cheapest dish that passes the filters. A dish passes when it matches any one request word. So for "something spicy with chicken", the price ranker picked a cold noodle appetizer: "Sichuan" counts as spicy, and it was the cheapest. Neither ranker broke a hard rule: hard rules are filtered out before ranking.

"Matches request" is the share of the request's words, with food synonyms, found in the dish name and description. "Matches learned prefs" uses the same check against the stated and learned soft preferences.

### On SingleStore

- One run over the real offers wrote 617 evidence events, 109 cross-platform matches and 617 price snapshots with dish vectors. `normalize` takes about 1 s, because the writes are batched.
- `smoke/audit_integrity.py` checks one full run of 742 audit events. Every inference cites evidence that exists, and no submit comes before an approval.
- The counting distiller learned "likes spicy noodles" from orders ord-12, ord-15, ord-19 and ord-21. A Cognee Cloud recall answered: "likes Dan Dan Noodles and Spicy Wonton Noodle Soup [ord-12] [ord-15], and dislikes food that's too oily [int-golden-wok-1]."

### Known limits

- With store URLs, neither platform returns a delivery fee or an ETA. Offers carry them as unknown and `verify_quote` reads the real total from the cart.
- The DoorDash Actor returns at most 25 menu items per store, against 61 to 144 on Uber Eats. So many dishes exist only in the Uber Eats data, and most top picks come from Uber Eats. When a dish has the same price on both platforms (96 of 109 matched pairs), the option shown is the one with more verified facts, here DoorDash, which gives the store address.
- The Brief chose Qwen3 Embedding 4B, but it was stuck provisioning on SIE, so embeddings use `tencent/R3-embedding-0.6b`. The dish-match threshold (0.83) is calibrated for R3.

## Run it

```bash
uv sync --extra memory                 # Cognee is an optional extra
cp .env.example .env                   # fill in the keys; never commit .env
uv run python -m data.setup_db         # create or migrate the 7 SingleStore tables
uv run python -m data.seed_demo        # the demo user: 5 orders, 1 hard rule, learned preferences
uv run uvicorn agent.api:app --port 8000   # agent + web UI at http://localhost:8000/
uv run pytest                          # offline tests
uv run python smoke/run_all.py         # checks against the real services (sie_chat.py calls the OpenRouter fallback)
```

`TOOL_MODE=fixtures` uses the saved Apify runs in `data/fixtures/`. `TOOL_MODE=live` scrapes again.
