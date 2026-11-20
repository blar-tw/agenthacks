"""P2's tool modes for contracts.ShopTools: "fixtures" (saved Actor JSON) and "live" (Apify).

search and get_menu return contracts.Offer. build_cart, quote and submit come from
contracts.StubTools: a cart built from the offer, the quote read back, and submit returns
the store deep link for the user to pay on the platform. It never pays.
"""
from __future__ import annotations

import asyncio

import contracts as c
from data import actors, config
from data.actors import SearchResult
from data.normalize import offers_from_result


class FixtureTools(c.StubTools):
    """Saved Actor output. For building, tests, and the backup demo."""

    def __init__(self, missing: set[str] | None = None) -> None:
        super().__init__()
        self.fail_platforms = set(missing or ())
        self.last: dict[str, SearchResult] = {}   # platform -> raw result, for the audit trail

    def _raw(self, platform: c.Platform, constraints: c.Constraints) -> SearchResult:
        return actors.load_fixture(platform)

    def search(self, constraints: c.Constraints, platform: c.Platform) -> list[c.Offer]:
        self.calls["search"] += 1
        if platform in self.fail_platforms:
            raise TimeoutError(f"{platform} search failed")
        res = self._raw(platform, constraints)
        self.last[platform] = res
        if res.error:
            raise RuntimeError(f"{platform}: {res.error}")
        return offers_from_result(res)

    def get_menu(self, store_url: str, platform: c.Platform) -> list[c.Offer]:
        self.calls["get_menu"] += 1
        return [o for o in offers_from_result(actors.load_fixture(platform)) if o.url == store_url]


class LiveTools(FixtureTools):
    """Apify Actors. Only a city or public store URLs go to Apify, never cookies or tokens."""

    def _raw(self, platform: c.Platform, constraints: c.Constraints) -> SearchResult:
        city = config.env("SCRAPE_CITY", "San Francisco, CA")
        return asyncio.run(actors.run_actor(platform, actors.input_for(platform, city)))

    def get_menu(self, store_url: str, platform: c.Platform) -> list[c.Offer]:
        self.calls["get_menu"] += 1
        res = asyncio.run(actors.run_actor(platform, actors.input_for(platform, "", [store_url])))
        return offers_from_result(res)


def register() -> None:
    c.register_tools("fixtures", FixtureTools)
    c.register_tools("live", LiveTools)
