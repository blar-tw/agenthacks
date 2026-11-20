"""SIE embeddings and reranker work on food text.

- "Szechuan Spicy Beef Noodle Soup" is closer to "Sichuan Beef Noodles — Hot" than to "Caesar Salad".
- The reranker ranks the noodle dish first for a spicy-noodle query.
"""
import time

from _common import Smoke

from data import config
from data.sie import SIEClient, cosine

s = Smoke("sie_vectors")
if s.need("SIE_API_KEY"):
    try:
        sie = SIEClient()
        a, b, c = "Szechuan Spicy Beef Noodle Soup", "Sichuan Beef Noodles — Hot", "Caesar Salad"
        t0 = time.perf_counter()
        va, vb, vc = sie.embed([a, b, c])
        ms = (time.perf_counter() - t0) * 1000
        ab, ac = cosine(va, vb), cosine(va, vc)
        s.info(f"model {config.SIE_EMBED_MODEL}, dims {len(va)}, {ms:.0f} ms")
        if len(va) != config.EMBED_DIM:
            s.fail(f"vector has {len(va)} dims but EMBED_DIM={config.EMBED_DIM}; fix EMBED_DIM before creating tables")
        if ab > ac:
            s.ok(f"noodles~noodles {ab:.3f} > noodles~salad {ac:.3f}")
        else:
            s.fail(f"noodles~noodles {ab:.3f} <= noodles~salad {ac:.3f}")
    except Exception as exc:
        s.error("embed", exc)

    try:
        items = {"salad": "Caesar Salad: romaine, parmesan, croutons",
                 "noodles": "Szechuan Spicy Beef Noodle Soup: chili oil broth, hand-pulled noodles",
                 "chicken": "General Tso's Chicken: crispy chicken, sweet glaze"}
        t0 = time.perf_counter()
        scores = sie.score("spicy noodles", items)
        ms = (time.perf_counter() - t0) * 1000
        top = max(scores, key=scores.get) if scores else None
        s.info(f"model {config.SIE_RERANK_MODEL}, {ms:.0f} ms, scores {{{', '.join(f'{k}: {v:.3f}' for k, v in scores.items())}}}")
        if top == "noodles":
            s.ok("reranker puts the noodle dish first")
        else:
            s.fail(f"reranker put {top!r} first")
    except Exception as exc:
        s.error("score", exc)
s.done()
