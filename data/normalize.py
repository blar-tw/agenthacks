"""normalize: raw Actor output from both platforms -> contracts.Offer, with source and time."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Iterable

from contracts import Offer, SearchResult

_MONEY = re.compile(r"(\d+(?:[.,]\d{1,2})?)")


def parse_money(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower()
    if not s:
        return None
    if "free" in s:
        return 0.0
    m = _MONEY.search(s.replace(",", ""))
    return float(m.group(1)) if m else None


def parse_minutes(v: Any) -> int | None:
    """etaMinutes: 32, "25-35 min" (take the upper bound), "30 min"."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    nums = [int(n) for n in re.findall(r"\d+", str(v))]
    return max(nums) if nums else None


def _first(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _time(v: Any, default: datetime) -> datetime:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            pass
    return default


def offer_id(platform: str, store: str, item: str, scraped_at: datetime) -> str:
    h = hashlib.sha1(f"{platform}|{store}|{item}|{scraped_at.isoformat()}".encode()).hexdigest()[:12]
    return f"{'ue' if platform == 'ubereats' else 'dd'}-{h}"


def _offer(platform: str, res: SearchResult, store: dict[str, Any], item: dict[str, Any]) -> Offer | None:
    name = _first(item, "itemName", "name", "title")
    price = parse_money(_first(item, "price", "priceDisplay"))
    restaurant = _first(store, "storeName", "name", "restaurant", "title")
    if not name or price is None or not restaurant:
        return None
    scraped = _time(_first(item, "scrapedAt") or _first(store, "scrapedAt"), res.scraped_at)
    store_key = str(_first(store, "storeId", "url", "storeUrl") or restaurant)
    cuisines = _first(store, "cuisines") or []
    return Offer(
        id=offer_id(platform, store_key, str(name), scraped),
        platform=platform,  # type: ignore[arg-type]
        restaurant=str(restaurant).strip(),
        restaurant_id=_first(store, "storeId"),
        address=_first(store, "address", "storeAddress"),
        item=str(name).strip(),
        description=str(_first(item, "description") or "").strip(),
        price=price,
        fees=parse_money(_first(store, "deliveryFee", "delivery_fee")),
        eta_min=parse_minutes(_first(store, "etaMinutes", "eta", "etaRange")),
        currency=str(_first(item, "currency") or "USD"),
        cuisines=list(cuisines) if isinstance(cuisines, list) else [str(cuisines)],
        rating=parse_money(_first(store, "averageRating", "rating")),
        source_url=_first(store, "storeUrl", "url"),
        actor_run_id=res.actor_run_id,
        scraped_at=scraped,
    )


def offers_from_result(res: SearchResult) -> list[Offer]:
    """Both Actor shapes, plus Offer-shaped dicts (Firedrill sim mode)."""
    if res.error:
        return []
    out: list[Offer] = []
    for row in res.items:
        if row.get("error"):
            continue
        if {"restaurant", "item", "price"} <= row.keys():          # already Offer-shaped
            row = {**row, "storeName": row["restaurant"], "itemName": row["item"]}
            o = _offer(res.platform, res, row, row)
        elif isinstance(row.get("menu"), list):                   # DoorDash: one row per store
            for item in row["menu"]:
                if (o := _offer(res.platform, res, row, item)):
                    out.append(o)
            continue
        elif isinstance(row.get("items"), list):                  # Uber Eats nested mode
            for item in row["items"]:
                if (o := _offer(res.platform, res, row, item)):
                    out.append(o)
            continue
        else:                                                     # Uber Eats flat mode: one row per item
            o = _offer(res.platform, res, row, row)
        if o:
            out.append(o)
    return _dedupe(out)


def _dedupe(offers: Iterable[Offer]) -> list[Offer]:
    seen: dict[str, Offer] = {}
    for o in offers:
        seen.setdefault(o.id, o)
    return list(seen.values())


def normalize_results(results: dict[str, SearchResult]) -> list[Offer]:
    out: list[Offer] = []
    for res in results.values():
        out.extend(offers_from_result(res))
    return out
