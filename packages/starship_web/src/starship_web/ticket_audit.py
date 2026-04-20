from __future__ import annotations

import json
import uuid
from pathlib import Path

from starship_shared.trade_ticket import TradeTicketAuditEvent


def _audit_dir() -> Path:
    path = Path("data") / "trade_ticket_audit"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _audit_path(ticket_id: str) -> Path:
    return _audit_dir() / f"{ticket_id}.jsonl"


def append_ticket_audit_event(
    *,
    ticket_id: str,
    category: str,
    action: str,
    summary: str,
    actor: str = "system",
    details: dict[str, object] | None = None,
) -> TradeTicketAuditEvent:
    event = TradeTicketAuditEvent(
        event_id=f"tte-{uuid.uuid4().hex[:12]}",
        ticket_id=ticket_id,
        category=category,
        action=action,
        summary=summary,
        actor=actor,
        details=details or {},
    )
    with _audit_path(ticket_id).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event.model_dump()) + "\n")
    return event


def list_ticket_audit_events(ticket_id: str, *, limit: int = 50) -> list[TradeTicketAuditEvent]:
    path = _audit_path(ticket_id)
    if not path.exists():
        return []
    events: list[TradeTicketAuditEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(TradeTicketAuditEvent.model_validate_json(line))
        except Exception:
            continue
    return events[-limit:]


def load_latest_ticket_audit_event(ticket_id: str) -> TradeTicketAuditEvent | None:
    events = list_ticket_audit_events(ticket_id, limit=1)
    return events[-1] if events else None
