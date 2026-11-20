"""Environment settings. Values come from .env; nothing here is ever printed."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "data" / "fixtures"
load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_list(name: str) -> list[str]:
    return [x.strip() for x in env(name).split(",") if x.strip()]


SIE_BASE_URL = env("SIE_BASE_URL", "https://api.superlinked.com").rstrip("/")
SIE_EMBED_MODEL = env("SIE_EMBED_MODEL", "Qwen/Qwen3-Embedding-4B")
SIE_RERANK_MODEL = env("SIE_RERANK_MODEL", "Qwen/Qwen3-Reranker-4B")
SIE_COGNEE_MODEL = env("SIE_COGNEE_MODEL", "zai-org/GLM-5.3-Flash")
EMBED_DIM = int(env("EMBED_DIM", "2560"))

UBEREATS_ACTOR = env("UBEREATS_ACTOR", "corent1robert/uber-eats-menu-scraper")
DOORDASH_ACTOR = env("DOORDASH_ACTOR", "haketa/doordash-scraper")
ACTOR_TIMEOUT_S = 90


def tool_mode() -> str:
    return env("TOOL_MODE", "fixtures")


def memory_backend() -> str:
    return env("MEMORY_BACKEND", "memory")


def distiller_name() -> str:
    return env("DISTILLER", "counting")


def ranker_name() -> str:
    return env("RANKER", "reranker")
