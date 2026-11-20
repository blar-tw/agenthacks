"""Ranker comparison (Brief 10.3): the same requests through the price-only ranker and the SIE reranker.

    uv run python -m data.compare_rankers            # real fixtures, real SIE; prints a markdown table

Offline and in memory: no SingleStore writes, no LLM. Uses SIE (embeddings for matching, reranker).
"""
from __future__ import annotations

import re
from statistics import mean

import contracts as c
from data import nodes, store
from data.rank import RELEVANCE_TERMS, est_total, hard_terms, violates
from data.seed_demo import seed

SCENARIOS = [
    ("Demo: spicy Chinese noodles for two, under $40, no mushrooms",
     c.Constraints(dish_query="spicy Chinese noodles", cuisine="chinese", people=2, budget_total=40,
                   hard_excludes=["mushrooms"], soft_prefs=["noodles"])),
    ("Dumplings for one, under $20",
     c.Constraints(dish_query="dumplings", people=1, budget_total=20)),
    ("Something spicy with chicken for two, under $60",
     c.Constraints(dish_query="spicy chicken", people=2, budget_total=60)),
    ("Tofu dish, no pork, under $25",
     c.Constraints(dish_query="tofu", people=1, budget_total=25, hard_excludes=["pork"])),
    ("Soup for two, under $50, no peanuts",
     c.Constraints(dish_query="soup", people=2, budget_total=50, hard_excludes=["peanuts"])),
    ("Cheap noodles, under $15",
     c.Constraints(dish_query="noodles", people=1, budget_total=15, soft_prefs=["spicy"])),
]


def _hits(text: str, words: list[str]) -> float:
    """Share of the request's words (with food synonyms) found in a dish."""
    if not words:
        return 1.0
    t = text.lower()
    return sum(any(re.search(rf"\b{re.escape(x)}", t) for x in RELEVANCE_TERMS.get(w, [w])) for w in words) / len(words)


_FILLER = {"chinese", "like", "likes", "dislike", "dislikes", "often", "order", "orders", "food", "something",
           "with", "dish"}


def _words(s: str) -> list[str]:
    return sorted({w.rstrip("s") for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 2 and w not in _FILLER})


def run() -> list[dict]:
    c.set_store(store.P2MemoryStore())
    seed()                                   # the demo user's memory: hard rule + learned prefs
    base = {"run_id": "compare", "user_id": "demo"}
    base.update(nodes.load_memory(base))
    base.update(nodes.scrape(base))
    base.update(nodes.normalize(base))
    base.update(nodes.match_offers(base))    # matching is shared by both rankers
    rows = []
    for title, cons in SCENARIOS:
        st = {**base, "constraints": cons.model_dump()}
        hard = hard_terms(cons, st["memory"])
        soft = _words(" ".join([*cons.soft_prefs, *st["memory"].get("soft_prefs", [])]))
        for ranker in ("price", "reranker"):
            out = nodes.filter_rank({**st, "ranker": ranker})
            top = [c.RankedOption(**r).offer for r in out["ranked"][:3]]
            rows.append({
                "scenario": title, "ranker": out["ranker"],
                "top1": f"{top[0].item} ({top[0].restaurant}, {top[0].source})" if top else "none",
                "avg_total": round(mean(est_total(o, cons.people) for o in top), 2) if top else None,
                "on_request": round(mean(_hits(f"{o.item} {o.description}", _words(cons.dish_query)) for o in top), 2) if top else 0,
                "on_prefs": round(mean(_hits(f"{o.item} {o.description}", soft) for o in top), 2) if top and soft else None,
                "violations": sum(1 for o in top if violates(o, hard)),
            })
    return rows


def markdown(rows: list[dict]) -> str:
    out = ["| Request | Ranker | Top pick | Avg total, top 3 | Matches request | Matches learned prefs | Rule breaks |",
           "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        prefs = "n/a" if r["on_prefs"] is None else f"{r['on_prefs']:.0%}"
        total = "—" if r["avg_total"] is None else f"${r['avg_total']:.2f}"
        out.append(f"| {r['scenario']} | {r['ranker']} | {r['top1']} | {total} | {r['on_request']:.0%} | {prefs} | {r['violations']} |")
    by = {k: [r for r in rows if r["ranker"] == k] for k in ("price", "reranker")}
    out.append("")
    for k, rs in by.items():
        totals = [r["avg_total"] for r in rs if r["avg_total"] is not None]
        prefs = [r["on_prefs"] for r in rs if r["on_prefs"] is not None]
        out.append(f"- **{k}**: avg total ${mean(totals):.2f}, matches request {mean(r['on_request'] for r in rs):.0%}, "
                   f"matches learned prefs {mean(prefs):.0%}, rule breaks {sum(r['violations'] for r in rs)}")
    return "\n".join(out)


if __name__ == "__main__":
    print(markdown(run()))
