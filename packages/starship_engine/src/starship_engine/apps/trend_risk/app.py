from __future__ import annotations

from starship_engine.apps.captain_log.logging import get_logger
from starship_engine.apps.trend_risk.logic import TrendRiskConfig, compute_trend_risk
from starship_engine.apps.trend_risk.settings import TrendRiskSettings


class TrendRiskApp:
    name = "trend_risk"
    settings = TrendRiskSettings()
    logger = get_logger(__name__)


app = TrendRiskApp()
name = app.name


def to_config(settings: TrendRiskSettings) -> TrendRiskConfig:
    return TrendRiskConfig(
        enabled=settings.enabled,
        adx_len=settings.adx_len,
        di_len=settings.di_len,
        slope_lookback=settings.slope_lookback,
        use_rv_modifier=settings.use_rv_modifier,
        block_threshold=settings.block,
        caution_threshold=settings.caution,
        stretch_atr_len=settings.stretch_atr_len,
        stretch_low=settings.stretch_low,
        stretch_high=settings.stretch_high,
        di_spread_low=settings.di_spread_low,
        di_spread_high=settings.di_spread_high,
        adx_slope_low=settings.adx_slope_low,
        adx_slope_high=settings.adx_slope_high,
        adx_b1=settings.adx_b1,
        adx_b2=settings.adx_b2,
        adx_b3=settings.adx_b3,
        adx_b4=settings.adx_b4,
        policy=settings.policy,
    )
