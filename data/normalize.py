"""normalize: raw Actor output from both platforms -> contracts.Offer, with source and time."""
from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime
from typing import Any, Iterable

from contracts import Offer
from data.actors import SearchResult

_MONEY = re.compile(r"(\d+(?:\.\d{1,2})?)")

# contracts.Offer.fees is a float that defaults to 0.0, so it cannot say "unknown".
# The Uber Eats Actor reports no fee: those offer_ids are listed here, and the audit
# payload records fees_known=false. (Asked P1 for Optional[float] fees.)
UNKNOWN_FEES: set[str] = set()


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


def _text(v: Any) -> str:
    """Uber Eats names come HTML-escaped ("Z&amp;Y Restaurant")."""
    return html.unescape(str(v or "")).strip()


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
    """Stable per snapshot: the same scrape always gives the same id."""
    return "off_" + hashlib.sha1(f"{platform}|{store}|{item}|{scraped_at.isoformat()}".encode()).hexdigest()[:12]


def _offer(res: SearchResult, store: dict[str, Any], item: dict[str, Any]) -> Offer | None:
    name = _first(item, "itemName", "item", "name", "title")
    price = parse_money(_first(item, "price", "priceDisplay"))
    restaurant = _first(store, "storeName", "restaurant", "name", "title")
    if not name or price is None or not restaurant:
        return None
    scraped = _time(_first(item, "scrapedAt", "scraped_at") or _first(store, "scrapedAt"), res.scraped_at)
    store_key = str(_first(store, "storeId", "url", "storeUrl") or restaurant)
    fees = parse_money(_first(store, "deliveryFee", "delivery_fee", "fees"))
    o = Offer(
        offer_id=offer_id(res.platform, store_key, str(name), scraped),
        restaurant=_text(restaurant),
        restaurant_address=_text(_first(store, "address", "restaurant_address", "storeAddress")),
        item=_text(name),
        description=_text(_first(item, "description")),
        price=price,
        fees=fees if fees is not None else 0.0,
        eta_min=parse_minutes(_first(store, "etaMinutes", "eta_min", "eta", "etaRange")),
        source=res.platform,
        scraped_at=scraped,
        url=str(_first(store, "storeUrl", "url") or ""),
    )
    if fees is None:
        UNKNOWN_FEES.add(o.offer_id)
    return o


def offers_from_result(res: SearchResult) -> list[Offer]:
    """Both Actor shapes, plus Offer-shaped dicts (Firedrill sim mode)."""
    if res.error:
        return []
    out: list[Offer] = []
    for row in res.items:
        if row.get("error"):
            continue
        if isinstance(row.get("menu"), list):          # DoorDash: one row per store, menu inside
            out.extend(o for item in row["menu"] if (o := _offer(res, row, item)))
        elif isinstance(row.get("items"), list):       # Uber Eats nested mode
            out.extend(o for item in row["items"] if (o := _offer(res, row, item)))
        elif (o := _offer(res, row, row)):              # Uber Eats flat mode, or an Offer-shaped dict
            out.append(o)
    return dedupe(out)


def dedupe(offers: Iterable[Offer]) -> list[Offer]:
    seen: dict[str, Offer] = {}
    for o in offers:
        seen.setdefault(o.offer_id, o)
    return list(seen.values())
