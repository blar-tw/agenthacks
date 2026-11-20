"""Tool interface, P2's part: search and get_menu in live and fixtures mode.

build_cart, quote and submit belong to P1 (Playwright or deep link) and P3 (Firedrill sim).
"""
from __future__ import annotations

from typing import Any

from contracts import Constraints, Offer, Platform, SearchResult
from data import actors, config


class FixtureShopTools:
    """Saved Actor output. For building, tests, and the backup demo."""

    def __init__(self, missing: set[str] | None = None):
        self.missing = missing or set()   # simulate a platform being down

    async def search(self, platform: Platform, location: str, constraints: Constraints) -> SearchResult:
        res = actors.load_fixture(platform)
        if platform in self.missing:
            return SearchResult(platform=platform, scraped_at=res.scraped_at, error="platform down (simulated)")
        return res

    async def get_menu(self, platform: Platform, store_url: str) -> SearchResult:
        res = actors.load_fixture(platform)
        keep = [i for i in res.items if store_url in (i.get("storeUrl"), i.get("url"))]
        return res.model_copy(update={"items": keep})

    async def build_cart(self, offer: Offer, quantity: int) -> dict[str, Any]:
        raise NotImplementedError("build_cart is P1's")

    async def quote(self, cart: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("quote is P1's")

    async def submit(self, cart: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("submit is P1's")


class LiveShopTools(FixtureShopTools):
    """Apify Actors. Only a city or public store URLs go to Apify."""

    async def search(self, platform: Platform, location: str, constraints: Constraints) -> SearchResult:
        return await actors.run_actor(platform, actors.input_for(platform, location))

    async def get_menu(self, platform: Platform, store_url: str) -> SearchResult:
        return await actors.run_actor(platform, actors.input_for(platform, "", [store_url]))


_tools = None


def get_tools():
    global _tools
    if _tools is None:
        mode = config.tool_mode()
        if mode == "live":
            _tools = LiveShopTools()
        elif mode == "sim":
            raise RuntimeError("TOOL_MODE=sim: P3's Firedrill tools must be registered with set_tools()")
        else:
            _tools = FixtureShopTools()
    return _tools


def set_tools(tools) -> None:
    global _tools
    _tools = tools
