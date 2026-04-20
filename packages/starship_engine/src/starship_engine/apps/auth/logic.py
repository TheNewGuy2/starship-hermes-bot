from __future__ import annotations

from starship_engine.apps.auth.settings import AuthSettings
from starship_engine.brokers.base import BrokerProvider, BrokerSessionBundle
from starship_engine.brokers.factory import create_broker_session
from starship_engine.brokers.tastytrade import (
    TastytradeAuth as TTAuth,
    create_tastytrade_session as create_tt_session,
    load_tastytrade_auth as load_auth_from_settings,
)


def create_session_bundle(settings: AuthSettings) -> BrokerSessionBundle:
    return create_broker_session(settings)


def require_tastytrade_session(settings: AuthSettings):
    bundle = create_broker_session(settings)
    if bundle.provider is not BrokerProvider.TASTYTRADE:
        raise RuntimeError(
            "The current stream engine only supports provider='tastytrade'. "
            "E*TRADE auth is now available, but market-data wiring is not finished yet."
        )
    return bundle.session
