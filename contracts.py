"""MealMatch shared contract (doc section 9.2). Owner: P1.

Every module imports data shapes and side-effect functions from here.
Do not change a signature without P1; announce changes in the doc's Log tab.

Pluggable parts, so P2 and P3 never edit this file to swap in real backends:
- register_tools(mode, factory)  -> live (P2: Apify/Playwright), sim (P3: Firedrill)
- set_store(store)               -> P2: SingleStore-backed MemoryStore
- set_event_sink(fn)             -> P3: Statsig log_event
"""

from __future__ import annotations

import contextvars
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Optional, Protocol, TypedDict

from pydantic import BaseModel, Field

Platform = Literal["ubereats", "doordash"]
AuditKind = Literal["evidence", "inference", "action", "approval"]
ToolMode = Literal["live", "sim", "fixtures"]


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------- data shapes


class Offer(BaseModel):
    """One dish from either platform (doc 9.2). Every price is evidence."""

    offer_id: str = Field(default_factory=lambda: new_id("off"))
    restaurant: str
    restaurant_address: str = ""
    item: str
    description: str = ""
    price: float  # item price, USD
    fees: float = 0.0  # delivery + service fees, USD
    eta_min: Optional[int] = None  # estimated delivery minutes
    source: Platform
    scraped_at: datetime = Field(default_factory=now)
    url: str = ""  # store or item deep link
    evidence_id: Optional[str] = None  # audit event id of the price snapshot


class Constraints(BaseModel):
    """Structured request produced by parse_request."""

    raw_text: str = ""
    cuisine: Optional[str] = None
    dish_query: str = ""  # free text for matching, e.g. "spicy noodles"
    people: int = 1
    budget_total: Optional[float] = None  # USD, incl. fees
    max_eta_min: Optional[int] = None
    hard_excludes: list[str] = Field(default_factory=list)  # from the user's words only
    soft_prefs: list[str] = Field(default_factory=list)


class Match(BaseModel):
    """The same dish on both platforms (an inference)."""

    match_id: str = Field(default_factory=lambda: new_id("mat"))
    offer_ids: list[str]
    confidence: float
    evidence_ids: list[str] = Field(default_factory=list)


class RankedOption(BaseModel):
    offer: Offer
    score: float
    ranker: Literal["price", "reranker"] = "price"
    match_id: Optional[str] = None
    reasons: list[dict[str, Any]] = Field(default_factory=list)  # filled by explain


class CartLine(BaseModel):
    item: str
    qty: int = 1
    unit_price: float


class Cart(BaseModel):
    cart_id: str = Field(default_factory=lambda: new_id("cart"))
    platform: Platform
    restaurant: str
    lines: list[CartLine]
    subtotal: float
    fees: float = 0.0
    total: float
    address_label: str = ""  # a label like "home", never a full address
    deep_link: str = ""
    quoted_at: Optional[datetime] = None


class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: new_id("evt"))
    run_id: str
    node: str
    kind: AuditKind
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    source_url: Optional[str] = None
    actor_run_id: Optional[str] = None
    ts: datetime = Field(default_factory=now)


class AgentState(TypedDict, total=False):
    """LangGraph state. Every node returns a partial dict of these keys."""

    run_id: str
    thread_id: str
    user_id: str
    request_text: str
    constraints: dict  # Constraints.model_dump()
    prev_constraints: dict
    memory: dict  # load_memory output: orders, rules, soft_prefs
    offers: list[dict]  # Offer dumps
    missing_platforms: list[str]
    matches: list[dict]
    ranked: list[dict]  # RankedOption dumps, best first
    rejected: list[str]  # offer_ids the user rejected
    selected: Optional[dict]  # RankedOption dump
    cart: Optional[dict]
    quote: Optional[dict]
    quote_ok: bool
    approval: Optional[bool]
    feedback: str  # user's rejection reason / follow-up text
    route: str  # next_step decision: more | new | done
    round: int
    reply: str  # text shown to the user for this turn
    handoff_url: str
    ranker: Literal["price", "reranker"]


# ---------------------------------------------------------------- tools


class ShopTools(Protocol):
    """One interface for search and checkout. live / sim / fixtures implementations."""

    def search(self, constraints: Constraints, platform: Platform) -> list[Offer]: ...

    def get_menu(self, store_url: str, platform: Platform) -> list[Offer]: ...

    def build_cart(self, offer: Offer, qty: int = 1) -> Cart: ...

    def quote(self, cart: Cart) -> Cart: ...

    def submit(self, cart: Cart) -> str:
        """Hand-off only: returns the deep link the user opens to pay. Never pays."""
        ...


_STUB_MENU: list[dict[str, Any]] = [
    dict(restaurant="Sichuan Garden", item="Dan Dan Noodles", description="spicy pork, chili oil",
         price=14.5, fees=3.99, eta_min=32, source="doordash"),
    dict(restaurant="Sichuan Garden", item="Dan Dan Noodles (Spicy)", description="chili oil, pork",
         price=15.25, fees=2.49, eta_min=35, source="ubereats"),
    dict(restaurant="Golden Wok", item="Spicy Beef Chow Fun", description="wide rice noodles",
         price=13.0, fees=4.99, eta_min=28, source="doordash"),
    dict(restaurant="Mala House", item="Mushroom Mala Noodles", description="shiitake, mala broth",
         price=12.0, fees=1.99, eta_min=30, source="ubereats"),
    dict(restaurant="Mala House", item="Chongqing Spicy Noodles", description="chili, peanuts",
         price=13.5, fees=1.99, eta_min=38, source="ubereats"),
    dict(restaurant="Slow Dragon", item="Szechuan Beef Noodle Soup", description="spicy broth",
         price=11.0, fees=2.99, eta_min=65, source="doordash"),
]


def stable_offer_id(source: str, restaurant: str, item: str) -> str:
    """Same dish -> same id across scrapes, so rejected[] survives a re-scrape. Real tools should use it."""
    slug = re.sub(r"[^a-z0-9]+", "-", f"{restaurant} {item}".lower()).strip("-")
    return f"{source}:{slug}"


class StubTools:
    """Deterministic in-process shop used until real modes are registered."""

    def __init__(self) -> None:
        self.quote_multiplier = 1.0  # tests set >1.10 to simulate a price jump
        self.calls: dict[str, int] = {"search": 0, "get_menu": 0, "build_cart": 0, "quote": 0, "submit": 0}
        self.fail_platforms = set()

    def search(self, constraints: Constraints, platform: Platform) -> list[Offer]:
        self.calls["search"] += 1
        if platform in self.fail_platforms:
            raise TimeoutError(f"{platform} search failed")
        return [
            Offer(**row, offer_id=stable_offer_id(row["source"], row["restaurant"], row["item"]),
                  url=f"https://{row['source']}.example/store/{row['restaurant'].replace(' ', '-').lower()}")
            for row in _STUB_MENU
            if row["source"] == platform
        ]

    def get_menu(self, store_url: str, platform: Platform) -> list[Offer]:
        self.calls["get_menu"] += 1
        return [o for o in self.search(Constraints(), platform) if o.url == store_url]

    def build_cart(self, offer: Offer, qty: int = 1) -> Cart:
        self.calls["build_cart"] += 1
        subtotal = round(offer.price * qty, 2)
        return Cart(platform=offer.source, restaurant=offer.restaurant,
                    lines=[CartLine(item=offer.item, qty=qty, unit_price=offer.price)],
                    subtotal=subtotal, fees=offer.fees, total=round(subtotal + offer.fees, 2),
                    address_label="home", deep_link=offer.url)

    def quote(self, cart: Cart) -> Cart:
        self.calls["quote"] += 1
        total = round(cart.total * self.quote_multiplier, 2)
        return cart.model_copy(update={"total": total, "quoted_at": now()})

    def submit(self, cart: Cart) -> str:
        self.calls["submit"] += 1
        return cart.deep_link or f"https://{cart.platform}.example/cart/{cart.cart_id}"


_TOOL_FACTORIES: dict[str, Callable[[], ShopTools]] = {}
_TOOL_CACHE: dict[str, ShopTools] = {}


def register_tools(mode: ToolMode, factory: Callable[[], ShopTools]) -> None:
    """P2 registers 'live' and 'fixtures'; P3 registers 'sim'."""
    _TOOL_FACTORIES[mode] = factory
    _TOOL_CACHE.pop(mode, None)


def get_tools(mode: ToolMode) -> ShopTools:
    """Return the tools for a mode; falls back to StubTools until one is registered."""
    if mode not in _TOOL_CACHE:
        _TOOL_CACHE[mode] = _TOOL_FACTORIES.get(mode, StubTools)()
    return _TOOL_CACHE[mode]


def reset_tools() -> None:
    _TOOL_CACHE.clear()


# ---------------------------------------------------------------- memory store

SECRET_KEYS = ("password", "passwd", "pwd", "passcode", "cookie", "token", "secret", "api_key", "apikey",
               "auth", "bearer", "jwt", "csrf", "credential", "private", "session", "card", "cvv", "pan")
_SECRET_VALUE = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|secret-[A-Za-z0-9_-]{8,}|apify_api_[A-Za-z0-9]{8,}|Bearer\s+[A-Za-z0-9._-]{8,}"
    r"|\b(?:\d[ -]?){13,19}\b)")


def scrub(obj: Any) -> Any:
    """Drop secret-looking keys and redact secret-looking values before anything leaves the process."""
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()
                if not any(s in str(k).lower() for s in SECRET_KEYS)}
    if isinstance(obj, (list, tuple, set)):
        return [scrub(v) for v in obj]
    if isinstance(obj, str):
        return _SECRET_VALUE.sub("<redacted>", obj)
    return obj


class MemoryStore(Protocol):
    """P2 implements this on SingleStore. Nodes never write SQL."""

    def add_audit(self, event: AuditEvent) -> None: ...
    def get_audit(self, run_id: str) -> list[AuditEvent]: ...
    def read_orders(self, user_id: str, limit: int = 5) -> list[dict]: ...
    def write_order(self, user_id: str, order: dict) -> str: ...
    def read_interactions(self, user_id: str, limit: int = 20) -> list[dict]: ...
    def write_interaction(self, user_id: str, interaction: dict) -> str: ...
    def read_preferences(self, user_id: str) -> list[dict]: ...
    def write_preference(self, user_id: str, pref: dict) -> str: ...
    def write_price_snapshot(self, offer: Offer) -> str: ...
    def read_price_snapshots(self, run_id: str | None = None) -> list[dict]: ...


class InMemoryStore:
    """Default store until P2's SingleStore store is set (doc 9.3 note)."""

    def __init__(self) -> None:
        self.audit: list[AuditEvent] = []
        self.orders: dict[str, list[dict]] = {}
        self.interactions: dict[str, list[dict]] = {}
        self.preferences: dict[str, list[dict]] = {}
        self.snapshots: list[dict] = []

    def add_audit(self, event: AuditEvent) -> None:
        self.audit.append(event)

    def get_audit(self, run_id: str) -> list[AuditEvent]:
        return [e for e in self.audit if e.run_id == run_id]

    def read_orders(self, user_id: str, limit: int = 5) -> list[dict]:
        return self.orders.get(user_id, [])[-limit:][::-1]

    def write_order(self, user_id: str, order: dict) -> str:
        oid = order.get("order_id") or new_id("ord")
        self.orders.setdefault(user_id, []).append({**scrub(order), "order_id": oid})
        return oid

    def read_interactions(self, user_id: str, limit: int = 20) -> list[dict]:
        return self.interactions.get(user_id, [])[-limit:][::-1]

    def write_interaction(self, user_id: str, interaction: dict) -> str:
        iid = new_id("int")
        self.interactions.setdefault(user_id, []).append({**scrub(interaction), "interaction_id": iid})
        return iid

    def read_preferences(self, user_id: str) -> list[dict]:
        return list(self.preferences.get(user_id, []))

    def write_preference(self, user_id: str, pref: dict) -> str:
        pid = new_id("pref")
        self.preferences.setdefault(user_id, []).append({**scrub(pref), "pref_id": pid})
        return pid

    def write_price_snapshot(self, offer: Offer) -> str:
        sid = new_id("snap")
        self.snapshots.append({**offer.model_dump(mode="json"), "snapshot_id": sid})
        return sid

    def read_price_snapshots(self, run_id: str | None = None) -> list[dict]:
        return list(self.snapshots)


_STORE: MemoryStore = InMemoryStore()


def set_store(store: MemoryStore) -> None:
    global _STORE
    _STORE = store


def get_store() -> MemoryStore:
    return _STORE


# Thin memory functions (doc 9.2). Nodes call these, never SQL.
def read_orders(user_id: str, limit: int = 5) -> list[dict]:
    return _STORE.read_orders(user_id, limit)


def write_order(user_id: str, order: dict) -> str:
    return _STORE.write_order(user_id, order)


def read_interactions(user_id: str, limit: int = 20) -> list[dict]:
    return _STORE.read_interactions(user_id, limit)


def write_interaction(user_id: str, interaction: dict) -> str:
    return _STORE.write_interaction(user_id, interaction)


def read_preferences(user_id: str) -> list[dict]:
    return _STORE.read_preferences(user_id)


def write_preference(user_id: str, pref: dict) -> str:
    return _STORE.write_preference(user_id, pref)


def write_price_snapshot(offer: Offer) -> str:
    return _STORE.write_price_snapshot(offer)


def read_price_snapshots(run_id: str | None = None) -> list[dict]:
    return _STORE.read_price_snapshots(run_id)


# ---------------------------------------------------------------- audit trail

_AUDIT_CTX: contextvars.ContextVar[tuple[str, str]] = contextvars.ContextVar(
    "audit_ctx", default=("no-run", "unknown"))


def set_audit_context(run_id: str, node: str) -> contextvars.Token:
    """The graph sets this before each node so audit() knows run_id and node."""
    return _AUDIT_CTX.set((run_id, node))


def audit(kind: AuditKind, payload: dict, evidence_ids: list[str] | None = None, *,
          source_url: str | None = None, actor_run_id: str | None = None,
          run_id: str | None = None, node: str | None = None) -> str:
    """Append one audit event; returns its event_id (doc 9.2). Payload is scrubbed."""
    ctx_run, ctx_node = _AUDIT_CTX.get()
    event = AuditEvent(run_id=run_id or ctx_run, node=node or ctx_node, kind=kind,
                       payload=scrub(payload), evidence_ids=list(evidence_ids or []),
                       source_url=source_url, actor_run_id=actor_run_id)
    _STORE.add_audit(event)
    return event.event_id


def get_audit(run_id: str) -> list[AuditEvent]:
    return sorted(_STORE.get_audit(run_id), key=lambda e: e.ts)


# ---------------------------------------------------------------- analytics

_EVENT_SINK: Callable[[str, dict], None] = lambda name, metadata: None  # noqa: E731
EVENTS: list[tuple[str, dict]] = []  # local copy for tests


def set_event_sink(fn: Callable[[str, dict], None]) -> None:
    """P3 sets this to the Statsig sender."""
    global _EVENT_SINK
    _EVENT_SINK = fn


def log_event(name: str, metadata: dict | None = None) -> None:
    """Send one analytics event (doc 9.2). Never raises; never sends secrets."""
    clean = scrub(metadata or {})
    EVENTS.append((name, clean))
    try:
        _EVENT_SINK(name, clean)
    except Exception:  # analytics must never break the agent
        pass
