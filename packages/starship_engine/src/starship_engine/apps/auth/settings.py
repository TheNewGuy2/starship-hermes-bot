from __future__ import annotations

from pydantic import BaseModel


class AuthSettings(BaseModel):
    provider: str = "tastytrade"
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""
    is_test: bool = False
    etrade_consumer_key: str = ""
    etrade_consumer_secret: str = ""
    etrade_oauth_token: str = ""
    etrade_oauth_token_secret: str = ""
