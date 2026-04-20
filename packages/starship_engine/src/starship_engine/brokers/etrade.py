from __future__ import annotations

from dataclasses import dataclass

from starship_engine.apps.auth.settings import AuthSettings


@dataclass(frozen=True)
class ETradeAccessToken:
    consumer_key: str
    consumer_secret: str
    oauth_token: str
    oauth_token_secret: str


def load_etrade_access_token(settings: AuthSettings) -> ETradeAccessToken:
    missing = [
        key
        for key, value in {
            "ETRADE_CONSUMER_KEY": settings.etrade_consumer_key,
            "ETRADE_CONSUMER_SECRET": settings.etrade_consumer_secret,
            "ETRADE_OAUTH_TOKEN": settings.etrade_oauth_token,
            "ETRADE_OAUTH_TOKEN_SECRET": settings.etrade_oauth_token_secret,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required settings: {', '.join(missing)}")

    return ETradeAccessToken(
        consumer_key=settings.etrade_consumer_key,
        consumer_secret=settings.etrade_consumer_secret,
        oauth_token=settings.etrade_oauth_token,
        oauth_token_secret=settings.etrade_oauth_token_secret,
    )
