"""Environment settings. Values come from .env; nothing here is ever printed."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "data" / "fixtures"
load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    """An env var, or the default when it is unset or blank (P1's .env.example has blank lines)."""
    return os.environ.get(name, "").strip() or default


def env_list(name: str) -> list[str]:
    return [x.strip() for x in env(name).split(",") if x.strip()]


SIE_BASE_URL = env("SIE_BASE_URL", "https://api.superlinked.com").rstrip("/")
SIE_EMBED_MODEL = env("SIE_EMBED_MODEL", "Qwen/Qwen3-Embedding-4B")
SIE_RERANK_MODEL = env("SIE_RERANK_MODEL", "Qwen/Qwen3-Reranker-4B")
SIE_COGNEE_MODEL = env("SIE_COGNEE_MODEL", "zai-org/GLM-5.3-Flash")
EMBED_DIM = int(env("EMBED_DIM", "2560"))

# Demo stores: 5 San Francisco Sichuan/Chinese restaurants listed on both platforms, so
# match_offers has real pairs. Used when UBEREATS_STORE_URLS / DOORDASH_STORE_URLS are unset.
# (The Uber Eats Actor's city mode finds no US restaurants, so it needs store URLs.)
DEMO_STORES = {
    "ubereats": [
        "https://www.ubereats.com/store/z&y-restaurant/4bIAq_kBSn6FuGWMkMHjdw",
        "https://www.ubereats.com/store/z-&-y-peking-duck/IOWfqXENQs6KBQCjL4S-fQ",
        "https://www.ubereats.com/store/spices/lszSibfTSlu-uoUiB1g2aA",
        "https://www.ubereats.com/store/china-live/nIGcpTenQ4iZEeDXLEHtbw",
        "https://www.ubereats.com/store/sichuan-home/aMer0uP0SIuU5kR76h5Z_A",
    ],
    "doordash": [
        "https://www.doordash.com/store/z-&-y-restaurant-san-francisco-768793/",
        "https://www.doordash.com/store/z-&-y-peking-duck-san-francisco-682066/",
        "https://www.doordash.com/store/spices-san-francisco-8150",
        "https://www.doordash.com/store/china-live-san-francisco-102102/",
        "https://www.doordash.com/store/sichuan-home-san-francisco-78137/",
    ],
}

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
