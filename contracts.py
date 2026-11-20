"""MealMatch contract: shared data shapes and function names.

STAND-IN. P2 wrote this from Brief section 9.2 because P1's contracts.py was
not on main yet. When P1's version lands, P1's file wins; P2 adapts data/ to it.
Do not change this file without P1.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional, Protocol, TypedDict

from pydantic import BaseModel, Field

Platform = Literal["ubereats", "doordash"]
PLATFORMS: tuple[Platform, ...] = ("ubereats", "doordash")
AuditKind = Literal["evidence", "inference", "action", "approval"]
RankerName = Literal["reranker", "price"]


# ---------- Scraped data ----------

class Offer(BaseModel):
    """One dish from either platform. Every Offer is a price snapshot (evidence)."""
    id: str
    platform: Platform
    restaurant: str
    restaurant_id: Optional[str] = None
    address: Optional[str] = None
    item: str
    description: str = ""
    price: float
    fees: Optional[float] = None      # delivery fee; None when the platform doesn't report it
    eta_min: Optional[int] = None     # None when the platform doesn't report it
    currency: str = "USD"
    cuisines: list[str] = Field(default_factory=list)
    rating: Optional[float] = None
    source_url: Optional[str] = None  # store URL the price came from
    actor_run_id: Optional[str] = None
    scraped_at: datetime
    evidence_id: Optional[str] = None  # audit event that records this snapshot


class SearchResult(BaseModel):
    """Raw output of one platform search, before normalize."""
    platform: Platform
    items: list[dict[str, Any]] = Field(default_factory=list)
    actor_run_id: Optional[str] = None
    scraped_at: datetime
    error: Optional[str] = None       # set when the platform failed or timed out


class Match(BaseModel):
    """The same dish on both platforms. An inference."""
    id: str
    ubereats_offer_id: str
    doordash_offer_id: str
    confidence: float                 # 0..1
    method: Literal["vector", "fuzzy"]
    evidence_ids: list[str] = Field(default_factory=list)


class RankedOption(BaseModel):
    offer: Offer
    score: float
    ranker: RankerName
    est_total: float                  # price * party_size + known fees
    fees_known: bool
    eta_known: bool
    match_id: Optional[str] = None
    alt_offer: Optional[Offer] = None  # same dish on the other platform
    evidence_ids: list[str] = Field(default_factory=list)


# ---------- Request ----------

class Constraints(BaseModel):
    """Output of parse_request."""
    query: str = ""                   # the request text, for the reranker
    cuisine: Optional[str] = None
    party_size: int = 1
    budget: Optional[float] = None    # total, including fees
    max_eta_min: Optional[int] = None
    exclusions: list[str] = Field(default_factory=list)  # hard: "mushrooms", "peanuts"
    wants: list[str] = Field(default_factory=list)       # soft: "noodles", "spicy"
    location: Optional[str] = None


# ---------- Memory ----------

class Preference(BaseModel):
    id: str
    user_id: str
    text: str                         # "dislikes mushrooms"
    kind: Literal["hard", "soft"]
    source: Literal["stated", "learned"]
    confidence: float = 1.0
    term: Optional[str] = None        # normalized keyword for filtering, e.g. "mushroom"
    episode_ids: list[str] = Field(default_factory=list)  # learned only
    created_at: Optional[datetime] = None


class OrderRecord(BaseModel):
    """Episode: one food order."""
    id: str
    user_id: str
    run_id: Optional[str] = None
    platform: Platform
    restaurant: str
    items: list[str]
    cuisine: Optional[str] = None
    price_paid: float
    fees: Optional[float] = None
    eta_min: Optional[int] = None
    rating: Optional[int] = None
    ordered_at: datetime


class Interaction(BaseModel):
    """Episode when it holds a rejection: options shown, selected, rejected, why."""
    id: str
    user_id: str
    run_id: Optional[str] = None
    shown: list[str] = Field(default_factory=list)      # offer ids
    selected: Optional[str] = None
    rejected: list[str] = Field(default_factory=list)   # offer ids
    rejected_restaurants: list[str] = Field(default_factory=list)
    reason: Optional[str] = None
    at: datetime


class MemoryContext(BaseModel):
    """Output of load_memory."""
    recent_orders: list[OrderRecord] = Field(default_factory=list)
    hard_rules: list[Preference] = Field(default_factory=list)
    soft_prefs: list[Preference] = Field(default_factory=list)
    recent_rejections: list[Interaction] = Field(default_factory=list)


class AuditEvent(BaseModel):
    id: str
    run_id: str
    node: str
    at: datetime
    kind: AuditKind
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    source_url: Optional[str] = None
    actor_run_id: Optional[str] = None
    hash: str = ""


# ---------- Graph state ----------

class AgentState(TypedDict, total=False):
    run_id: str
    thread_id: str
    user_id: str
    request: str
    constraints: Constraints
    memory: MemoryContext
    search_results: dict[str, SearchResult]
    missing_platforms: list[str]
    offers: list[Offer]
    matches: list[Match]
    ranked: list[RankedOption]
    ranker: RankerName
    rejected: list[str]               # offer ids the user rejected this session
    rejected_restaurants: list[str]
    rejection_reason: Optional[str]
    selected: Optional[str]           # offer id
    quote: Optional[dict[str, Any]]
    approved: Optional[bool]
    round: int
    episode_ids: list[str]


# ---------- Tool interface ----------

class ShopTools(Protocol):
    """Each search or checkout call goes through this. Live mode: Apify and
    Playwright. Sim mode: the Firedrill custom tool. Fixtures mode: saved JSON."""

    async def search(self, platform: Platform, location: str, constraints: Constraints) -> SearchResult: ...
    async def get_menu(self, platform: Platform, store_url: str) -> SearchResult: ...
    async def build_cart(self, offer: Offer, quantity: int) -> dict[str, Any]: ...
    async def quote(self, cart: dict[str, Any]) -> dict[str, Any]: ...
    async def submit(self, cart: dict[str, Any]) -> dict[str, Any]: ...


# ---------- Shared functions ----------

def audit(kind: AuditKind, payload: dict[str, Any], evidence_ids: list[str] | None = None, *,
          run_id: str, node: str, source_url: str | None = None,
          actor_run_id: str | None = None) -> str:
    """Write one audit event and return its id. Implemented by data/audit.py (P2)."""
    from data.audit import write_audit
    return write_audit(kind, payload, evidence_ids or [], run_id=run_id, node=node,
                       source_url=source_url, actor_run_id=actor_run_id)


def log_event(name: str, metadata: dict[str, Any] | None = None) -> None:
    """Send one Statsig event. Implemented by P3; no-op stub until then."""
    return None
