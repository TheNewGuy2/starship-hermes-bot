from __future__ import annotations

from pydantic import BaseModel


class AuthSettings(BaseModel):
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""
    is_test: bool = False
