"""Apify Actors.

Default: the fixtures parse into contracts.Offer (no key needed).
--live: both Actors run at the same time, each must return within 90 s; reports any missing platform.
--live --save: also saves each good run as the new fixtures.
"""
import asyncio
import json
import sys
import time

from _common import Smoke

from contracts import PLATFORMS, Offer
from data import actors, config
from data.normalize import offers_from_result

s = Smoke("apify")
manifest = json.loads((config.FIXTURES / "manifest.json").read_text(encoding="utf-8"))

for p in PLATFORMS:
    try:
        offers = offers_from_result(actors.load_fixture(p))
        if offers and all(isinstance(o, Offer) for o in offers):
            tag = " (SYNTHETIC: replace with --live --save)" if manifest.get(p, {}).get("synthetic") else ""
            s.ok(f"{p} fixtures -> {len(offers)} Offers from {len({o.restaurant for o in offers})} restaurants{tag}")
        else:
            s.fail(f"{p} fixtures gave no Offers")
    except Exception as exc:
        s.error(f"{p} fixtures", exc)

if "--live" in sys.argv and s.need("APIFY_TOKEN"):
    city = config.env("SCRAPE_CITY", "San Francisco, CA")
    t0 = time.perf_counter()
    results = asyncio.run(actors.scrape_both(city))
    s.info(f"both Actors finished in {time.perf_counter() - t0:.0f} s (limit {config.ACTOR_TIMEOUT_S} s each)")
    missing = []
    for p, r in results.items():
        offers = offers_from_result(r)
        if r.error or not offers:
            missing.append(p)
            s.fail(f"{p} ({actors.actor_for(p)}): {r.error or 'returned 0 usable menu items'}")
            if r.items:
                s.info(f"{p} first item keys: {sorted(r.items[0].keys())[:20]}")
        else:
            fees = sum(o.fees is not None for o in offers)
            eta = sum(o.eta_min is not None for o in offers)
            s.ok(f"{p}: {len(r.items)} rows -> {len(offers)} Offers; fees on {fees}, ETA on {eta}; run {r.actor_run_id}")
            if "--save" in sys.argv:
                actors.save_fixture(r)
                s.info(f"{p}: saved as fixtures")
    if missing and len(missing) < len(results):
        s.info(f"missing platform(s): {', '.join(missing)}; the agent continues with the other one")
s.done()
