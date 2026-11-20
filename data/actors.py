"""Apify Actors for Uber Eats and DoorDash. Both run at the same time; each has 90 s.

Never pass cookies, passwords or tokens in run_input: the Actors only get a city or
public store URLs.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from contracts import PLATFORMS, Platform, SearchResult
from data import config


def ubereats_input(city: str, store_urls: list[str]) -> dict[str, Any]:
    if store_urls:
        return {"mode": "urls", "startUrls": [{"url": u} for u in store_urls],
                "includeDescriptions": True, "outputMode": "flat"}
    name = city.split(",")[0].strip()
    return {"mode": "city", "cityNames": [f"us:{name}"], "maxResults": 15, "categories": [],
            "includeDescriptions": True, "outputMode": "flat"}


def doordash_input(city: str, store_urls: list[str]) -> dict[str, Any]:
    base: dict[str, Any] = {"includeMenu": True, "includeReviews": False,
                            "proxyConfiguration": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"],
                                                   "apifyProxyCountry": "US"}}
    if store_urls:
        return {**base, "storeUrls": store_urls}
    return {**base, "location": [city], "maxStores": 15}


def actor_for(platform: Platform) -> str:
    return config.UBEREATS_ACTOR if platform == "ubereats" else config.DOORDASH_ACTOR


def input_for(platform: Platform, city: str, store_urls: list[str] | None = None) -> dict[str, Any]:
    if store_urls is None:
        store_urls = config.env_list("UBEREATS_STORE_URLS" if platform == "ubereats" else "DOORDASH_STORE_URLS")
    return ubereats_input(city, store_urls) if platform == "ubereats" else doordash_input(city, store_urls)


async def run_actor(platform: Platform, run_input: dict[str, Any], timeout_s: int = config.ACTOR_TIMEOUT_S) -> SearchResult:
    from apify_client import ApifyClientAsync

    started = datetime.now(timezone.utc)
    token = config.env("APIFY_TOKEN")
    if not token:
        return SearchResult(platform=platform, scraped_at=started, error="APIFY_TOKEN is not set")
    client = ApifyClientAsync(token)

    async def _go() -> SearchResult:
        run = await client.actor(actor_for(platform)).call(
            run_input=run_input, run_timeout=timedelta(seconds=timeout_s), wait_duration=timedelta(seconds=timeout_s))
        if run is None:
            return SearchResult(platform=platform, scraped_at=started, error="Actor run did not start")
        status = str(getattr(run, "status", "")).upper()
        run_id = getattr(run, "id", None)
        ds = getattr(run, "default_dataset_id", None)
        if "SUCCEEDED" not in status:
            return SearchResult(platform=platform, scraped_at=started, actor_run_id=run_id,
                                error=f"Actor run ended with status {status or 'unknown'}")
        page = await client.dataset(ds).list_items(clean=True)
        return SearchResult(platform=platform, items=list(page.items), actor_run_id=run_id, scraped_at=started)

    try:
        return await asyncio.wait_for(_go(), timeout=timeout_s + 5)
    except asyncio.TimeoutError:
        return SearchResult(platform=platform, scraped_at=started, error=f"timed out after {timeout_s} s")
    except Exception as exc:  # one platform failing must not stop the other
        return SearchResult(platform=platform, scraped_at=started, error=f"{type(exc).__name__}: {str(exc)[:200]}")


async def scrape_both(city: str) -> dict[str, SearchResult]:
    """Run both Actors at the same time. One result is enough."""
    results = await asyncio.gather(*(run_actor(p, input_for(p, city)) for p in PLATFORMS))
    return {r.platform: r for r in results}


def save_fixture(result: SearchResult) -> None:
    """Save one good run as fixtures (Brief section 11)."""
    path = config.FIXTURES / f"{result.platform}.json"
    path.write_text(json.dumps(result.items, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    manifest_path = config.FIXTURES / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest[result.platform] = {"synthetic": False, "actor": actor_for(result.platform),
                                 "actor_run_id": result.actor_run_id, "scraped_at": result.scraped_at.isoformat(),
                                 "items": len(result.items)}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def load_fixture(platform: Platform) -> SearchResult:
    items = json.loads((config.FIXTURES / f"{platform}.json").read_text(encoding="utf-8"))
    manifest_path = config.FIXTURES / "manifest.json"
    meta = json.loads(manifest_path.read_text(encoding="utf-8")).get(platform, {}) if manifest_path.exists() else {}
    scraped = meta.get("scraped_at")
    return SearchResult(platform=platform, items=items, actor_run_id=meta.get("actor_run_id"),
                        scraped_at=datetime.fromisoformat(scraped) if scraped else datetime.now(timezone.utc))
