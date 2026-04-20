from __future__ import annotations

import json
from pathlib import Path

from starship_shared.trade_ticket import TradeTicketV1


def _tickets_dir() -> Path:
    path = Path("data") / "trade_tickets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ticket_path(ticket_id: str) -> Path:
    return _tickets_dir() / f"{ticket_id}.json"


def save_ticket(ticket: TradeTicketV1) -> TradeTicketV1:
    _ticket_path(ticket.ticket_id).write_text(
        json.dumps(ticket.model_dump(), indent=2),
        encoding="utf-8",
    )
    return ticket


def load_ticket(ticket_id: str) -> TradeTicketV1 | None:
    path = _ticket_path(ticket_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return TradeTicketV1.model_validate(data)


def list_tickets() -> list[TradeTicketV1]:
    tickets: list[TradeTicketV1] = []
    for path in sorted(
        _tickets_dir().glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            tickets.append(TradeTicketV1.model_validate(data))
        except Exception:
            continue
    return tickets
