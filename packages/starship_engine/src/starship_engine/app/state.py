from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class EngineState:
    buf: Any
    stop_event: Any
    publish_seq: int = 0
    same_bar_updates: int = 0
    accepted_bars: int = 0
    stats: Any = None
    state_log_handler: Any = None
    last_alert_date: Optional[str] = None
    latest_spx_mid: Optional[float] = None
    greeks_by_symbol: dict[str, object] = field(default_factory=dict)
    warmup_done: bool = False
    context_handler: Any = None
    stream_health: Any = None
    early_config: Any = None
    trend_risk_vov_series: Any = None
    exit_planner: Any = None
    last_gate: Any = None
    last_features: Any = None
    last_decision: Any = None
    last_snapshot: Any = None
    latest_context_priors: Any = None
    backfill_mode: bool = True
    backfill_by_time: dict[int, Any] = field(default_factory=dict)
    backfill_retries_left: int = 1
    backfill_retry_deadline_ms: Optional[int] = None
    backfill_wait_deadline_ms: Optional[int] = None
    current_forming: Any = None
    current_forming_t: Optional[int] = None
    last_closed_t: Optional[int] = None
    seen_quote: bool = False
    seen_greeks: bool = False
    seen_valid_candle: bool = False
    exit_reason: str = "unknown"
    exit_error: Optional[str] = None
    context_enabled: bool = False
    context_symbol: str = ""
    exec_symbol: str = ""
    signal_mode: str = ""
    ignore_time_window: bool = False
    dry_run: bool = False
    warmup_bars: int = 0
    backfill_bars: int = 0
    candle_minutes_back_override: int = 0
    backfill_prev_session_open: bool = True
    context_exchange: str = ""
    context_code: str = ""
    engine_run_id: str = ""
