"""Audit writer. One row in audit_events per call. The audit trail is the product."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from contracts import AuditEvent
from data.store import get_store

KINDS = {"evidence", "inference", "action", "approval"}
_SECRET_KEY = re.compile(r"cookie|password|passwd|token|secret|authorization|api[_-]?key|card|cvv|session", re.I)
_SECRET_VAL = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|apify_api_[A-Za-z0-9]{8,}|Bearer\s+\S+)")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def scrub(value: Any) -> Any:
    """Drop secrets before anything reaches the audit log."""
    if isinstance(value, dict):
        return {k: ("[redacted]" if _SECRET_KEY.search(str(k)) else scrub(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return _SECRET_VAL.sub("[redacted]", value)
    return value


def _hash(e: AuditEvent) -> str:
    body = e.model_dump(mode="json", exclude={"hash"})
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def write_audit(kind: str, payload: dict[str, Any], evidence_ids: list[str], *, run_id: str, node: str,
                source_url: str | None = None, actor_run_id: str | None = None) -> str:
    if kind not in KINDS:
        raise ValueError(f"audit kind must be one of {sorted(KINDS)}, got {kind!r}")
    e = AuditEvent(id=new_id("ev"), run_id=run_id, node=node, at=datetime.now(timezone.utc), kind=kind,
                   payload=scrub(payload), evidence_ids=list(evidence_ids), source_url=source_url,
                   actor_run_id=actor_run_id)
    e.hash = _hash(e)
    get_store().add_audit(e)
    return e.id
