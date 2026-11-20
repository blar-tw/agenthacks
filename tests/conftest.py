"""Offline test setup for P1 + P2: no model, no network.

One autouse fixture gives every test a fresh P2MemoryStore (a contracts.InMemoryStore),
P2's fixture tools, a fake SIE, no LLM, and a fresh graph.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path

import pytest

os.environ["LLM_PROVIDER"] = "none"  # tests never call a model or the network
os.environ["TOOL_MODE"] = "fixtures"
os.environ["LANGSMITH_TRACING"] = "false"  # no trace uploads from tests
os.environ["STATSIG_SERVER_KEY"] = ""  # tests never reach Statsig (load_dotenv does not override this)

import contracts  # noqa: E402
import contracts as c  # noqa: E402
from agent import graph as graph_mod  # noqa: E402
from agent import llm  # noqa: E402
from data import nodes, sie, store  # noqa: E402  (importing nodes registers P2's tools)
from data import tools as data_tools  # noqa: E402

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
    data_tools.register()
    c.reset_tools()
    c.EVENTS.clear()
    sie.set_sie(FakeSIE())
    store._BOOKMARKS.clear()
    llm.set_llm(None)
    graph_mod.reset_graph()
    yield mem
    sie.set_sie(None)
    c.reset_tools()


@pytest.fixture
def mem():
    return c.get_store()


@pytest.fixture
def fake_sie():
    return sie.get_sie()


@pytest.fixture
def tools():
    return contracts.get_tools("fixtures")


DEMO = graph_mod.DEMO_REQUEST


def scraped_state(run_id: str, **extra) -> dict:
    """State after scrape + normalize on the fixtures."""
    state = {"run_id": run_id, "user_id": "demo", **extra}
    state.update(nodes.scrape(state))
    state.update(nodes.normalize(state))
    return state
