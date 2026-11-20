"""Superlinked SIE Serverless: embeddings (match_offers) and reranker (filter_rank).

Encode: POST /v1/encode/{model}  body {"items":[{"text":..}], "params":{"output_types":["dense"]}}
Score:  POST /v1/score/{model}   body {"query":{"text":..}, "items":[{"id":..,"text":..}]}
"""
from __future__ import annotations

import math
from typing import Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel

from data import config


class Embedder(Protocol):
    def embed(self, texts: list[str], is_query: bool = False) -> list[list[float]]: ...


class Scorer(Protocol):
    def score(self, query: str, items: dict[str, str]) -> dict[str, float]: ...


# Response shapes, validated with Pydantic.
class _Dense(BaseModel):
    values: list[float]


class _EncItem(BaseModel):
    id: str | None = None
    dense: _Dense


class _EncodeResp(BaseModel):
    items: list[_EncItem]


class _Score(BaseModel):
    item_id: str
    score: float


class _ScoreResp(BaseModel):
    scores: list[_Score]


class SIEClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, timeout: float = 30.0):
        key = api_key or config.env("SIE_API_KEY")
        if not key:
            raise RuntimeError("SIE_API_KEY is not set")
        self.http = httpx.Client(base_url=base_url or config.SIE_BASE_URL, timeout=timeout,
                                 headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})

    def embed(self, texts: list[str], is_query: bool = False) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            batch = texts[i:i + 64]
            body = {"items": [{"id": str(j), "text": t} for j, t in enumerate(batch)],
                    "params": {"output_types": ["dense"], "options": {"is_query": is_query}}}
            r = self.http.post(f"/v1/encode/{quote(config.SIE_EMBED_MODEL, safe='')}", json=body)
            r.raise_for_status()
            out.extend(it.dense.values for it in _EncodeResp.model_validate(r.json()).items)
        return out

    def score(self, query: str, items: dict[str, str]) -> dict[str, float]:
        if not items:
            return {}
        body = {"query": {"text": query}, "items": [{"id": k, "text": v} for k, v in items.items()]}
        r = self.http.post(f"/v1/score/{quote(config.SIE_RERANK_MODEL, safe='')}", json=body)
        r.raise_for_status()
        return {s.item_id: s.score for s in _ScoreResp.model_validate(r.json()).scores}


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


_client = None


def get_sie():
    """The SIE client (embed + score). Raises RuntimeError when SIE_API_KEY is missing."""
    global _client
    if _client is None:
        _client = SIEClient()
    return _client


def set_sie(client) -> None:
    """Swap in a fake for tests, or None to reset."""
    global _client
    _client = client
