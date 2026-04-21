from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from requests_oauthlib import OAuth1Session

from starship_engine.apps.auth.settings import AuthSettings
from starship_engine.config import load_runtime_env


_dotenv_path, _secrets_dir = load_runtime_env()


@dataclass(frozen=True)
class ETradeProbeConfig:
    consumer_key: str
    consumer_secret: str
    oauth_token: str
    oauth_token_secret: str
    environment: str
    base_url: str


def _session_file() -> Path:
    raw = os.environ.get("ETRADE_SESSION_FILE", "").strip() or "data/etrade_session.json"
    return Path(raw)


def _load_session_token() -> tuple[str, str] | None:
    path = _session_file()
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    oauth_token = str(data.get("oauth_token", "")).strip()
    oauth_token_secret = str(data.get("oauth_token_secret", "")).strip()
    if not oauth_token or not oauth_token_secret:
        return None
    return oauth_token, oauth_token_secret


def load_probe_config(settings: AuthSettings) -> ETradeProbeConfig:
    session_token = _load_session_token()
    oauth_token = settings.etrade_oauth_token.strip()
    oauth_token_secret = settings.etrade_oauth_token_secret.strip()
    if (not oauth_token or not oauth_token_secret) and session_token is not None:
        oauth_token, oauth_token_secret = session_token

    environment = os.environ.get("ETRADE_ENV", "sandbox").strip().lower() or "sandbox"
    base_url = (
        "https://apisb.etrade.com/v1"
        if environment == "sandbox"
        else "https://api.etrade.com/v1"
    )
    missing = [
        key
        for key, value in {
            "ETRADE_CONSUMER_KEY": settings.etrade_consumer_key,
            "ETRADE_CONSUMER_SECRET": settings.etrade_consumer_secret,
            "ETRADE_OAUTH_TOKEN": oauth_token,
            "ETRADE_OAUTH_TOKEN_SECRET": oauth_token_secret,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required settings: {', '.join(missing)}")

    return ETradeProbeConfig(
        consumer_key=settings.etrade_consumer_key,
        consumer_secret=settings.etrade_consumer_secret,
        oauth_token=oauth_token,
        oauth_token_secret=oauth_token_secret,
        environment=environment,
        base_url=base_url,
    )


class ETradeProbeClient:
    def __init__(self, config: ETradeProbeConfig):
        self.config = config
        self.session = OAuth1Session(
            client_key=config.consumer_key,
            client_secret=config.consumer_secret,
            resource_owner_key=config.oauth_token,
            resource_owner_secret=config.oauth_token_secret,
            signature_type="AUTH_HEADER",
        )

    def _url(self, path: str) -> str:
        if path.startswith("/"):
            path = path[1:]
        return f"{self.config.base_url}/{path}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self.session.request(
            method=method,
            url=self._url(path),
            params=params,
            json=json_body,
            timeout=20,
        )
        try:
            response.raise_for_status()
        except Exception as exc:
            raise RuntimeError(
                f"E*TRADE API {method} {path} failed: {response.status_code} {response.text}"
            ) from exc
        return response.json()

    def get_quote(self, symbol: str, *, detail_flag: str = "ALL") -> dict[str, Any]:
        return self._request(
            "GET",
            f"market/quote/{symbol}.json",
            params={"detailFlag": detail_flag},
        )

    def get_option_expire_dates(
        self,
        symbol: str,
        *,
        expiry_type: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"symbol": symbol}
        if expiry_type:
            params["expiryType"] = expiry_type
        return self._request("GET", "market/optionexpiredate.json", params=params)

    def get_option_chain(
        self,
        symbol: str,
        *,
        expiry_year: int,
        expiry_month: int,
        expiry_day: int | None = None,
        chain_type: str = "CALLPUT",
        price_type: str = "ATNM",
        strike_price_near: float | None = None,
        no_of_strikes: int | None = None,
        skip_adjusted: bool = True,
        include_weekly: bool = True,
        option_category: str = "STANDARD",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "symbol": symbol,
            "expiryYear": expiry_year,
            "expiryMonth": expiry_month,
            "chainType": chain_type,
            "priceType": price_type,
            "skipAdjusted": str(skip_adjusted).lower(),
            "includeWeekly": str(include_weekly).lower(),
            "optionCategory": option_category,
        }
        if strike_price_near is not None:
            params["strikePriceNear"] = strike_price_near
        if no_of_strikes is not None:
            params["noOfStrikes"] = no_of_strikes
        if expiry_day is not None:
            params["expiryDay"] = expiry_day
        return self._request("GET", "market/optionchains.json", params=params)
