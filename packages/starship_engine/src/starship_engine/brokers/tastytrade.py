from __future__ import annotations

from dataclasses import dataclass

from tastytrade import Session

from starship_engine.apps.auth.settings import AuthSettings


@dataclass(frozen=True)
class TastytradeAuth:
    client_id: str
    client_secret: str
    refresh_token: str
    is_test: bool = False


def load_tastytrade_auth(settings: AuthSettings) -> TastytradeAuth:
    missing = [
        key
        for key, value in {
            "TT_CLIENT_ID": settings.client_id,
            "TT_CLIENT_SECRET": settings.client_secret,
            "TT_REFRESH_TOKEN": settings.refresh_token,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required settings: {', '.join(missing)}")

    return TastytradeAuth(
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        refresh_token=settings.refresh_token,
        is_test=settings.is_test,
    )


def create_tastytrade_session(
    auth: TastytradeAuth | None = None, settings: AuthSettings | None = None
) -> Session:
    if auth is None:
        if settings is None:
            raise RuntimeError("Auth settings required when auth is not provided")
        auth = load_tastytrade_auth(settings)

    return Session(auth.client_secret, auth.refresh_token, is_test=auth.is_test)
