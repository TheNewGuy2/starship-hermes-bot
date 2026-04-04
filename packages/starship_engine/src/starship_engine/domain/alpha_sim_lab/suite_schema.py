# src/bot/pipeline/alpha_sim_lab/suite_schema.py
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class SuiteTest(BaseModel):
    name: str
    params: dict[str, Any] = Field(default_factory=dict)
    sweep: Optional[dict[str, list[Any]]] = None

    @field_validator("sweep")
    @classmethod
    def _validate_sweep(
        cls, v: Optional[dict[str, list[Any]]]
    ) -> Optional[dict[str, list[Any]]]:
        if v is None:
            return None
        if not isinstance(v, dict):
            raise ValueError("sweep must be a dict of lists")
        for key, val in v.items():
            if not isinstance(val, list) or len(val) == 0:
                raise ValueError(f"sweep[{key}] must be a non-empty list")
        return v


class SuiteConfig(BaseModel):
    schema_version: int
    suite_name: str
    defaults: dict[str, Any]
    tests: list[SuiteTest]

    @field_validator("defaults")
    @classmethod
    def _validate_defaults(cls, v: dict[str, Any]) -> dict[str, Any]:
        plugin = v.get("plugin")
        if not plugin or not isinstance(plugin, str):
            raise ValueError("defaults.plugin is required")
        if not plugin.startswith("alpha_sim_lab."):
            raise ValueError("defaults.plugin must start with 'alpha_sim_lab.'")
        return v

    @model_validator(mode="after")
    def _validate_params(self) -> "SuiteConfig":
        for test in self.tests:
            merged = {**self.defaults, **test.params}
            tp_r = merged.get("tp_r")
            if tp_r is not None:
                if not (0 < float(tp_r) <= 1):
                    raise ValueError("tp_r must be in (0, 1]")
            tp_r_seq = merged.get("tp_r_seq")
            if tp_r_seq:
                if not isinstance(tp_r_seq, list):
                    raise ValueError("tp_r_seq must be a list of floats")
                for v in tp_r_seq:
                    if not (0 < float(v) <= 1):
                        raise ValueError("tp_r_seq values must be in (0, 1]")
            max_trades = merged.get("max_trades_per_day")
            cutoff = merged.get("entry_cutoff_trades")
            if max_trades is not None and cutoff is not None:
                if int(cutoff) > int(max_trades):
                    raise ValueError(
                        "entry_cutoff_trades must be <= max_trades_per_day"
                    )
            phase = merged.get("phase")
            if phase is not None:
                phase_val = float(phase)
                if phase_val < 2.5:
                    if merged.get("tp_r_seq") is not None:
                        raise ValueError("tp_r_seq requires phase 2.5")
                    if merged.get("entry_cutoff_trades") is not None:
                        raise ValueError("entry_cutoff_trades requires phase 2.5")
                    if merged.get("daily_stop_r") is not None:
                        raise ValueError("daily_stop_r requires phase 2.5")
            warmup_entry_mode = merged.get("warmup_entry_mode")
            if warmup_entry_mode is not None and warmup_entry_mode != "slot_proxy":
                raise ValueError("warmup_entry_mode must be 'slot_proxy'")
            warmup_skip_first = merged.get("warmup_skip_first_trade")
            if warmup_skip_first is not None and not isinstance(
                warmup_skip_first, bool
            ):
                raise ValueError("warmup_skip_first_trade must be a bool")
        return self
