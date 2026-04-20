from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class TradeTicketLeg(BaseModel):
    leg_id: str
    symbol: str
    security_type: str
    order_action: str
    quantity: float
    quantity_type: str = "QUANTITY"
    call_put: str | None = None
    expiry_year: int | None = None
    expiry_month: int | None = None
    expiry_day: int | None = None
    strike_price: float | None = None
    osi_key: str | None = None
    broker_payload: dict[str, object] = Field(default_factory=dict)


class TradeTicketPricing(BaseModel):
    price_type: str
    limit_price: float | None = None
    stop_price: float | None = None
    estimated_total_amount: float | None = None
    estimated_commission: float | None = None
    net_price: float | None = None
    net_bid: float | None = None
    net_ask: float | None = None
    suggested_limit_price: float | None = None
    suggested_net_mid: float | None = None
    suggested_natural_credit: float | None = None


class TradeTicketBrokerRef(BaseModel):
    provider: Literal["etrade"]
    account_id_key: str
    preview_id: str | None = None
    order_type: str
    client_order_id: str | None = None


class TradeTicketExecutionIntent(BaseModel):
    quantity: int | None = None
    price_type: str | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    order_term: str | None = None
    market_session: str | None = None
    all_or_none: bool = False
    user_confirmed: bool = False
    ready_for_execution: bool = False
    prepared_at: str | None = None
    notes: list[str] = Field(default_factory=list)


class TradeTicketAuditEvent(BaseModel):
    event_id: str
    ticket_id: str
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    category: Literal["ticket", "preview", "execution", "ops"]
    action: str
    summary: str
    actor: str = "system"
    details: dict[str, object] = Field(default_factory=dict)


class TradeTicketV1(BaseModel):
    schema_version: int = 1
    ticket_id: str
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    status: Literal[
        "draft",
        "preview_pending",
        "preview_failed",
        "previewed",
        "ready",
        "submitted",
        "executed",
        "expired",
        "cancelled",
    ] = "draft"
    source: Literal["manual", "signal"] = "manual"
    strategy: str | None = None
    underlier: str
    broker: TradeTicketBrokerRef
    pricing: TradeTicketPricing
    execution_intent: TradeTicketExecutionIntent = Field(
        default_factory=TradeTicketExecutionIntent
    )
    legs: list[TradeTicketLeg]
    preview_attempts: int = 0
    last_preview_at: str | None = None
    last_error: str | None = None
    status_reason: str | None = None
    next_retry_at: str | None = None
    warnings: list[str] = Field(default_factory=list)
    broker_suggestions: dict[str, object] = Field(default_factory=dict)
    market_snapshot: dict[str, object] = Field(default_factory=dict)
    preview_response: dict[str, object] = Field(default_factory=dict)
    preview_request: dict[str, object] = Field(default_factory=dict)
    place_order_request: dict[str, object] = Field(default_factory=dict)
    execution_record: dict[str, object] = Field(default_factory=dict)
