"""Offline test setup for P2: in-memory store, fixture tools, and a fake SIE that needs no key."""
from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

import pytest

import contracts as c
from data import nodes, sie, store, tools  # noqa: F401  (importing nodes registers P2's tools)

_SYN = {"szechuan": "sichuan", "hot": "spicy", "mala": "spicy", "chili": "spicy", "noodles": "noodle",
        "w": "", "with": ""}


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z]+", text.lower())
    return [w for w in (_SYN.get(w, w) for w in words) if w]


class FakeSIE:
    """Bag-of-words vectors and token-overlap scores. Deterministic, no network."""
    dim = 128

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for t in _tokens(text):
            v[int(hashlib.md5(t.encode()).hexdigest(), 16) % self.dim] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed(self, texts, is_query=False, max_wait_s=90.0):
        return [self._vec(t.split(".")[0]) for t in texts]   # item name only: keeps the fake strict

    def score(self, query, items, max_wait_s=90.0):
        q = set(_tokens(query))
        return {k: len(q & set(_tokens(v))) / (len(q) or 1) for k, v in items.items()}


TEST_FIXTURES = Path(__file__).resolve().parent / "fixtures"   # hand-made; data/fixtures holds real runs


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    from data import config
    monkeypatch.setattr(config, "FIXTURES", TEST_FIXTURES)
    monkeypatch.setenv("TOOL_MODE", "fixtures")
    monkeypatch.setenv("DISTILLER", "counting")
    monkeypatch.setenv("RANKER", "reranker")
    monkeypatch.setenv("MEMORY_BACKEND", "memory")
    mem = store.P2MemoryStore()
    c.set_store(mem)
    tools.register()
    c.reset_tools()
    sie.set_sie(FakeSIE())
    store._BOOKMARKS.clear()
    yield mem
    sie.set_sie(None)
    c.reset_tools()


@pytest.fixture
def mem():
    return c.get_store()


@pytest.fixture
def fake_sie():
    return sie.get_sie()


def scraped_state(run_id: str, **extra) -> dict:
    """State after scrape + normalize on the fixtures."""
    state = {"run_id": run_id, "user_id": "demo", **extra}
    state.update(nodes.scrape(state))
    state.update(nodes.normalize(state))
    return state
