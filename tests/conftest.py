"""Offline test setup: in-memory store, fixture tools, and a fake SIE that needs no key."""
from __future__ import annotations

import hashlib
import math
import re

import pytest

from data import sie, store, tools

_SYN = {"szechuan": "sichuan", "hot": "spicy", "mala": "spicy", "chili": "spicy", "noodles": "noodle",
        "w": "", "with": ""}


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z]+", text.lower())
    return [w for w in (_SYN.get(w, w) for w in words) if w]


class FakeSIE:
    """Bag-of-words vectors and token-overlap scores. Deterministic, no network."""
    dim = 128

    def __init__(self):
        self.embed_calls = 0
        self.score_calls = 0

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for t in _tokens(text):
            v[int(hashlib.md5(t.encode()).hexdigest(), 16) % self.dim] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed(self, texts, is_query=False):
        self.embed_calls += 1
        return [self._vec(t.split(".")[0]) for t in texts]   # item name only: keeps the fake strict

    def score(self, query, items):
        self.score_calls += 1
        q = set(_tokens(query))
        return {k: len(q & set(_tokens(v))) / (len(q) or 1) for k, v in items.items()}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TOOL_MODE", "fixtures")
    monkeypatch.setenv("DISTILLER", "counting")
    monkeypatch.setenv("RANKER", "reranker")
    mem = store.InMemoryStore()
    store.set_store(mem)
    tools.set_tools(tools.FixtureShopTools())
    fake = FakeSIE()
    sie.set_sie(fake)
    yield mem
    store.set_store(None)
    tools.set_tools(None)
    sie.set_sie(None)


@pytest.fixture
def fake_sie():
    return sie.get_sie()


@pytest.fixture
def mem():
    return store.get_store()
