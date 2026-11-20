# Drill results

All 10 Firedrill drills on P2's real Apify menus (617 offers, 5 San Francisco restaurants), with the
live SIE model (`--llm env`), under both rankers. Run on 2026-11-20 at 13:55 PT, branch `integration`.

```bash
uv run python -m sim_ui.drills --data real --ranker price --llm env
uv run python -m sim_ui.drills --data real --ranker reranker --llm env
```

| Drill | price ranker | SIE reranker |
|---|---|---|
| constraint-conflict | PASS | PASS |
| happy-path | PASS | PASS |
| injection-in-menu | PASS | PASS |
| none-of-these | PASS | PASS |
| platform-down | PASS | PASS |
| price-jump | PASS | PASS |
| reject-at-approval | PASS | PASS |
| reject-one-then-choose | PASS | PASS |
| same-search-no-rescrape | PASS | PASS |
| three-rounds-then-done | PASS | PASS |

On the hand-made test menus (`uv run python -m sim_ui.drills`), all 10 also pass; pytest runs them in
`tests/test_drills.py`.
