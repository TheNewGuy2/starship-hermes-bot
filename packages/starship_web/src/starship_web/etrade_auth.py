from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from requests_oauthlib import OAuth1Session

from starship_web.config import get_data_dir, load_runtime_env


_dotenv_path, _secrets_dir = load_runtime_env()


REQUEST_TOKEN_URL = "https://api.etrade.com/oauth/request_token"
AUTHORIZE_URL = "https://us.etrade.com/e/t/etws/authorize"
ACCESS_TOKEN_URL = "https://api.etrade.com/oauth/access_token"
RENEW_ACCESS_TOKEN_URL = "https://api.etrade.com/oauth/renew_access_token"


@dataclass(frozen=True)
class ETradeConsumerConfig:
    consumer_key: str
    consumer_secret: str
    callback_url: str = "oob"


@dataclass(frozen=True)
class ETradeRequestToken:
    oauth_token: str
    oauth_token_secret: str
    oauth_callback_confirmed: str = "false"


@dataclass(frozen=True)
class ETradeAccessToken:
    oauth_token: str
    oauth_token_secret: str
    issued_at: str | None = None


def _data_dir() -> Path:
    return get_data_dir()


def _session_file() -> Path:
    return _data_dir() / "etrade_session.json"


def _pending_request_file() -> Path:
    return _data_dir() / "etrade_request_token.json"


def get_session_file_path() -> Path:
    return _session_file()


def load_consumer_config() -> ETradeConsumerConfig:
    consumer_key = os.environ.get("ETRADE_CONSUMER_KEY", "").strip()
    consumer_secret = os.environ.get("ETRADE_CONSUMER_SECRET", "").strip()
    callback_url = os.environ.get("ETRADE_CALLBACK_URL", "").strip() or "oob"

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

    return ETradeConsumerConfig(
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        callback_url=callback_url,
    )


def request_token(config: ETradeConsumerConfig) -> ETradeRequestToken:
    client = OAuth1Session(
        client_key=config.consumer_key,
        client_secret=config.consumer_secret,
        callback_uri=config.callback_url,
        signature_type="AUTH_HEADER",
    )
    token = client.fetch_request_token(REQUEST_TOKEN_URL)
    return ETradeRequestToken(
        oauth_token=token["oauth_token"],
        oauth_token_secret=token["oauth_token_secret"],
        oauth_callback_confirmed=token.get("oauth_callback_confirmed", "false"),
    )


def build_authorize_url(config: ETradeConsumerConfig, token: ETradeRequestToken) -> str:
    return (
        f"{AUTHORIZE_URL}?key={config.consumer_key}"
        f"&token={token.oauth_token}"
    )


def exchange_access_token(
    config: ETradeConsumerConfig,
    request_token: ETradeRequestToken,
    oauth_verifier: str,
) -> ETradeAccessToken:
    client = OAuth1Session(
        client_key=config.consumer_key,
        client_secret=config.consumer_secret,
        resource_owner_key=request_token.oauth_token,
        resource_owner_secret=request_token.oauth_token_secret,
        verifier=oauth_verifier,
        signature_type="AUTH_HEADER",
    )
    token = client.fetch_access_token(ACCESS_TOKEN_URL)
    return ETradeAccessToken(
        oauth_token=token["oauth_token"],
        oauth_token_secret=token["oauth_token_secret"],
    )


def renew_access_token(
    config: ETradeConsumerConfig, access_token: ETradeAccessToken
) -> str:
    client = OAuth1Session(
        client_key=config.consumer_key,
        client_secret=config.consumer_secret,
        resource_owner_key=access_token.oauth_token,
        resource_owner_secret=access_token.oauth_token_secret,
        signature_type="AUTH_HEADER",
    )
    response = client.get(RENEW_ACCESS_TOKEN_URL, timeout=15)
    response.raise_for_status()
    return response.text.strip()


def save_pending_request_token(token: ETradeRequestToken) -> None:
    _pending_request_file().write_text(
        json.dumps(asdict(token), indent=2), encoding="utf-8"
    )


def load_pending_request_token() -> ETradeRequestToken | None:
    path = _pending_request_file()
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return ETradeRequestToken(**data)


def save_access_token(token: ETradeAccessToken) -> None:
    issued_at = token.issued_at or datetime.now(timezone.utc).isoformat()
    stored = ETradeAccessToken(
        oauth_token=token.oauth_token,
        oauth_token_secret=token.oauth_token_secret,
        issued_at=issued_at,
    )
    _session_file().write_text(json.dumps(asdict(stored), indent=2), encoding="utf-8")


def load_access_token() -> ETradeAccessToken | None:
    path = _session_file()
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("issued_at"):
        data["issued_at"] = datetime.fromtimestamp(
            path.stat().st_mtime,
            tz=timezone.utc,
        ).isoformat()
    return ETradeAccessToken(**data)


def clear_access_token() -> None:
    path = _session_file()
    if path.exists():
        path.unlink()


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> datetime:
    first = datetime(year, month, 1)
    first_delta = (weekday - first.weekday()) % 7
    day = 1 + first_delta + (n - 1) * 7
    return datetime(year, month, day)


def _is_eastern_dst_local(local_dt: datetime) -> bool:
    year = local_dt.year
    dst_start = _nth_weekday_of_month(year, 3, 6, 2).replace(hour=2)
    dst_end = _nth_weekday_of_month(year, 11, 6, 1).replace(hour=2)
    return dst_start <= local_dt < dst_end


def _eastern_tz_for_local(local_dt: datetime) -> timezone:
    hours = -4 if _is_eastern_dst_local(local_dt) else -5
    return timezone(timedelta(hours=hours), name="ET")


def _utc_to_eastern(utc_dt: datetime) -> datetime:
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    standard_guess = utc_dt.astimezone(timezone(timedelta(hours=-5)))
    local_guess = standard_guess.replace(tzinfo=None)
    tz = _eastern_tz_for_local(local_guess)
    return utc_dt.astimezone(tz)


def token_expiry_status(token: ETradeAccessToken | None) -> dict[str, object]:
    if token is None:
        return {
            "has_token": False,
            "issued_at": None,
            "expires_at_eastern": None,
            "expired": False,
        }
    issued_at = token.issued_at
    issued_dt = None
    if issued_at:
        try:
            issued_dt = datetime.fromisoformat(issued_at)
        except Exception:
            issued_dt = None
    now_utc = datetime.now(timezone.utc)
    now_et = _utc_to_eastern(now_utc)
    expires_et = datetime.combine(
        now_et.date() + timedelta(days=1),
        time.min,
        tzinfo=_eastern_tz_for_local(
            datetime.combine(now_et.date() + timedelta(days=1), time.min)
        ),
    )
    if issued_dt is not None:
        issued_et = _utc_to_eastern(issued_dt)
        expiry_local = datetime.combine(issued_et.date() + timedelta(days=1), time.min)
        expires_et = datetime.combine(
            expiry_local.date(),
            time.min,
            tzinfo=_eastern_tz_for_local(expiry_local),
        )
    expired = now_et >= expires_et
    return {
        "has_token": True,
        "issued_at": issued_at,
        "expires_at_eastern": expires_et.isoformat(),
        "expired": expired,
    }


def clear_pending_request_token() -> None:
    path = _pending_request_file()
    if path.exists():
        path.unlink()
