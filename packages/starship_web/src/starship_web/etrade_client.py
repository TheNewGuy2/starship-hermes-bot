from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from requests_oauthlib import OAuth1Session

from starship_web.config import load_runtime_env
from starship_web.etrade_auth import ETradeAccessToken, load_access_token

_dotenv_path, _secrets_dir = load_runtime_env()


@dataclass(frozen=True)
class ETradeApiConfig:
    consumer_key: str
    consumer_secret: str
    environment: str
    base_url: str


def load_api_config() -> ETradeApiConfig:
    consumer_key = os.environ.get("ETRADE_CONSUMER_KEY", "").strip()
    consumer_secret = os.environ.get("ETRADE_CONSUMER_SECRET", "").strip()
    environment = os.environ.get("ETRADE_ENV", "sandbox").strip().lower() or "sandbox"
    base_url = (
        "https://apisb.etrade.com/v1"
        if environment == "sandbox"
        else "https://api.etrade.com/v1"
    )

    missing = [
        key
        for key, value in {
            "ETRADE_CONSUMER_KEY": consumer_key,
            "ETRADE_CONSUMER_SECRET": consumer_secret,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required settings: {', '.join(missing)}")

    return ETradeApiConfig(
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        environment=environment,
        base_url=base_url,
    )


class ETradeClient:
    def __init__(self, config: ETradeApiConfig, token: ETradeAccessToken):
        self.config = config
        self.token = token
        self.session = OAuth1Session(
            client_key=config.consumer_key,
            client_secret=config.consumer_secret,
            resource_owner_key=token.oauth_token,
            resource_owner_secret=token.oauth_token_secret,
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
                f"E*TRADE API {method} {path} failed: "
                f"{response.status_code} {response.text}"
            ) from exc
        return response.json()

    def list_accounts(self) -> dict[str, Any]:
        return self._request("GET", "accounts/list.json")

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
        skip_adjusted: bool = True,
        include_weekly: bool = False,
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
        if expiry_day is not None:
            params["expiryDay"] = expiry_day
        return self._request("GET", "market/optionchains.json", params=params)

    def get_balance(
        self,
        account_id_key: str,
        *,
        real_time_nav: bool = True,
        account_type: str | None = None,
        institution_type: str = "BROKERAGE",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "realTimeNAV": str(real_time_nav).lower(),
            "instType": institution_type,
        }
        if account_type:
            params["accountType"] = account_type
        return self._request(
            "GET",
            f"accounts/{account_id_key}/balance.json",
            params=params,
        )

    def list_orders(
        self,
        account_id_key: str,
        *,
        count: int = 25,
        marker: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"count": count}
        if marker:
            params["marker"] = marker
        if status:
            params["status"] = status
        return self._request(
            "GET",
            f"accounts/{account_id_key}/orders.json",
            params=params,
        )

    def preview_order(
        self,
        account_id_key: str,
        preview_order_request: dict[str, Any],
    ) -> dict[str, Any]:
        payload = preview_order_request
        if "PreviewOrderRequest" not in payload:
            payload = {"PreviewOrderRequest": preview_order_request}
        return self._request(
            "POST",
            f"accounts/{account_id_key}/orders/preview.json",
            json_body=payload,
        )

    def place_order(
        self,
        account_id_key: str,
        place_order_request: dict[str, Any],
    ) -> dict[str, Any]:
        payload = place_order_request
        if "PlaceOrderRequest" not in payload:
            payload = {"PlaceOrderRequest": place_order_request}
        return self._request(
            "POST",
            f"accounts/{account_id_key}/orders/place.json",
            json_body=payload,
        )


def load_client() -> ETradeClient:
    token = load_access_token()
    if token is None:
        raise RuntimeError("No stored E*TRADE access token found")
    return ETradeClient(load_api_config(), token)
