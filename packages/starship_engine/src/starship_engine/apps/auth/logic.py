from __future__ import annotations

from dataclasses import dataclass

# tastytrade library (DXLink-capable)
from tastytrade import Session

from starship_engine.apps.auth.settings import AuthSettings


@dataclass(frozen=True)
class TTAuth:
    client_id: str
    client_secret: str
    refresh_token: str
    is_test: bool = False


def load_auth_from_settings(settings: AuthSettings) -> TTAuth:
    missing = [
        k
        for k, v in {
            "TT_CLIENT_ID": settings.client_id,
            "TT_CLIENT_SECRET": settings.client_secret,
            "TT_REFRESH_TOKEN": settings.refresh_token,
        }.items()
        if not v
    ]

    if missing:
        raise RuntimeError(f"Missing required settings: {', '.join(missing)}")

    return TTAuth(
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        refresh_token=settings.refresh_token,
        is_test=settings.is_test,
    )


def create_tt_session(
    auth: TTAuth | None = None, settings: AuthSettings | None = None
) -> Session:
    """
    Creates an OAuth-backed tastytrade Session.

    Note: many SDK flows only require (client_secret, refresh_token).
    We still keep client_id in config for clarity/future-proofing.
    """
    if auth is None:
        if settings is None:
            raise RuntimeError("Auth settings required when auth is not provided")
        auth = load_auth_from_settings(settings)

    # Common tastytrade python libs accept (client_secret, refresh_token, is_test=?)
    # If your installed library has a different signature, we’ll adjust after a quick run.
    return Session(auth.client_secret, auth.refresh_token, is_test=auth.is_test)
