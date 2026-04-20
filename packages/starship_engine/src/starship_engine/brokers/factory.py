from __future__ import annotations

from starship_engine.apps.auth.settings import AuthSettings
from starship_engine.brokers.base import BrokerProvider, BrokerSessionBundle
from starship_engine.brokers.etrade import load_etrade_access_token
from starship_engine.brokers.tastytrade import create_tastytrade_session


def create_broker_session(settings: AuthSettings) -> BrokerSessionBundle:
    try:
        provider = BrokerProvider(settings.provider.strip().lower())
    except ValueError as exc:
        raise RuntimeError(
            f"Unsupported broker provider: {settings.provider!r}"
        ) from exc

    if provider is BrokerProvider.TASTYTRADE:
        return BrokerSessionBundle(
            provider=provider,
            session=create_tastytrade_session(settings=settings),
        )

    if provider is BrokerProvider.ETRADE:
        return BrokerSessionBundle(
            provider=provider,
            session=load_etrade_access_token(settings),
        )

    raise RuntimeError(f"Unsupported broker provider: {provider.value!r}")
