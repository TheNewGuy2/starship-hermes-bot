import pytest

from starship_web import etrade_auth, etrade_client
from starship_web.etrade_auth import ETradeAccessToken, ETradeConsumerConfig
from starship_web.etrade_client import ETradeApiConfig, ETradeClient


class FakeResponse:
    def __init__(self, status_code: int, text: str, payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _config() -> ETradeApiConfig:
    return ETradeApiConfig(
        consumer_key="consumer-key",
        consumer_secret="consumer-secret",
        environment="live",
        base_url="https://api.etrade.com/v1",
    )


def test_client_auto_renews_rejected_token_and_retries(monkeypatch):
    responses = [
        FakeResponse(401, '{"Error":{"message":"oauth_problem=token_rejected"}}'),
        FakeResponse(200, "{}", {"ok": True}),
    ]
    requests_seen = []
    renewals = []

    class FakeSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def request(self, **kwargs):
            requests_seen.append(kwargs)
            return responses.pop(0)

    monkeypatch.setattr(etrade_client, "OAuth1Session", FakeSession)
    monkeypatch.setattr(
        etrade_client,
        "renew_stored_access_token",
        lambda token: renewals.append(token.oauth_token) or "renewed",
    )
    monkeypatch.setattr(
        etrade_client,
        "load_access_token",
        lambda: ETradeAccessToken("token", "secret"),
    )

    client = ETradeClient(_config(), ETradeAccessToken("token", "secret"))

    assert client.list_accounts() == {"ok": True}
    assert len(requests_seen) == 2
    assert renewals == ["token"]


def test_client_does_not_renew_non_token_auth_error(monkeypatch):
    renewals = []

    class FakeSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def request(self, **kwargs):
            return FakeResponse(
                401,
                '{"Error":{"message":"oauth_problem=signature_invalid"}}',
            )

    monkeypatch.setattr(etrade_client, "OAuth1Session", FakeSession)
    monkeypatch.setattr(
        etrade_client,
        "renew_stored_access_token",
        lambda token: renewals.append(token.oauth_token) or "renewed",
    )

    client = ETradeClient(_config(), ETradeAccessToken("token", "secret"))

    with pytest.raises(RuntimeError, match="signature_invalid"):
        client.list_accounts()
    assert renewals == []


def test_renew_stored_access_token_updates_stored_timestamp(monkeypatch):
    saved_tokens = []
    token = ETradeAccessToken("token", "secret", issued_at="2026-05-12T13:00:00+00:00")

    monkeypatch.setattr(etrade_auth, "load_access_token", lambda: token)
    monkeypatch.setattr(
        etrade_auth,
        "load_consumer_config",
        lambda: ETradeConsumerConfig("consumer-key", "consumer-secret"),
    )
    monkeypatch.setattr(
        etrade_auth,
        "renew_access_token",
        lambda config, access_token: "Access Token has been renewed",
    )
    monkeypatch.setattr(etrade_auth, "save_access_token", saved_tokens.append)

    assert etrade_auth.renew_stored_access_token() == "Access Token has been renewed"
    assert len(saved_tokens) == 1
    assert saved_tokens[0].oauth_token == "token"
    assert saved_tokens[0].oauth_token_secret == "secret"
    assert saved_tokens[0].issued_at is None
