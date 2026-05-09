from pathlib import Path

import pytest

from starship_web.pine_bridge import _parse_bar_date, process_pine_alert
from starship_web.pine_models import parse_pine_alert_payload
from starship_web.pine_state_store import list_pine_states
from starship_web.ticket_store import list_tickets


class FakeClient:
    def get_option_expire_dates(self, symbol, expiry_type="ALL"):
        return {
            "OptionExpireDateResponse": {
                "ExpirationDate": [
                    {"year": 2026, "month": 4, "day": 20},
                    {"year": 2026, "month": 4, "day": 21},
                ]
            }
        }

    def get_option_chain(
        self,
        symbol,
        *,
        expiry_year,
        expiry_month,
        expiry_day,
        include_weekly=True,
        price_type="ALL",
    ):
        pairs = []
        for strike, put_bid, put_ask, call_bid, call_ask in [
            (7085, 0.40, 0.50, 30.00, 31.00),
            (7105, 1.30, 1.50, 12.00, 13.00),
            (7115, 0.90, 1.00, 1.10, 1.30),
            (7135, 0.35, 0.45, 0.30, 0.40),
        ]:
            pairs.append(
                {
                    "Put": {
                        "strikePrice": strike,
                        "bid": put_bid,
                        "ask": put_ask,
                        "lastPrice": round((put_bid + put_ask) / 2.0, 2),
                        "osiKey": f"OSI-P-{strike}",
                        "openInterest": 10,
                        "volume": 5,
                        "timeStamp": "now",
                    },
                    "Call": {
                        "strikePrice": strike,
                        "bid": call_bid,
                        "ask": call_ask,
                        "lastPrice": round((call_bid + call_ask) / 2.0, 2),
                        "osiKey": f"OSI-C-{strike}",
                        "openInterest": 11,
                        "volume": 6,
                        "timeStamp": "now",
                    },
                }
            )
        return {
            "OptionChainResponse": {
                "SelectedED": {
                    "year": expiry_year,
                    "month": expiry_month,
                    "day": expiry_day,
                },
                "OptionPair": pairs,
            }
        }

    def preview_order(self, account_id_key, preview_request):
        root = preview_request["PreviewOrderRequest"]
        order = root["Order"][0]
        return {
            "PreviewOrderResponse": {
                "orderType": root["orderType"],
                "PreviewIds": [{"previewId": "preview-123"}],
                "Order": {
                    "priceType": order["priceType"],
                    "limitPrice": order["limitPrice"],
                    "Instrument": order["Instrument"],
                    "estimatedCommission": 1.25,
                    "estimatedTotalAmount": order["limitPrice"],
                    "netPrice": order["limitPrice"],
                    "netBid": order["limitPrice"],
                    "netAsk": order["limitPrice"] + 0.1,
                },
            }
        }


class StrictSpxChainClient(FakeClient):
    def __init__(self):
        self.option_chain_symbols: list[str] = []

    def get_option_chain(
        self,
        symbol,
        *,
        expiry_year,
        expiry_month,
        expiry_day,
        include_weekly=True,
        price_type="ALL",
    ):
        self.option_chain_symbols.append(symbol)
        if symbol == "SPXW":
            raise RuntimeError("SPXW is invalid for option-chain lookup")
        payload = super().get_option_chain(
            symbol,
            expiry_year=expiry_year,
            expiry_month=expiry_month,
            expiry_day=expiry_day,
            include_weekly=include_weekly,
            price_type=price_type,
        )
        pairs = payload["OptionChainResponse"]["OptionPair"]
        for pair in pairs:
            pair["Put"]["optionRootSymbol"] = "SPXW"
            pair["Call"]["optionRootSymbol"] = "SPXW"
        return payload


class FailingRefreshClient(FakeClient):
    def __init__(self):
        self.fail_chain = False

    def get_option_chain(
        self,
        symbol,
        *,
        expiry_year,
        expiry_month,
        expiry_day,
        include_weekly=True,
        price_type="ALL",
    ):
        if self.fail_chain:
            raise RuntimeError(f"forced option-chain failure for {symbol}")
        return super().get_option_chain(
            symbol,
            expiry_year=expiry_year,
            expiry_month=expiry_month,
            expiry_day=expiry_day,
            include_weekly=include_weekly,
            price_type=price_type,
        )


def _entry_payload():
    return {
        "strategy_name": "hermes_v3_bridge_test",
        "event_type": "ENTRY",
        "event_id": "evt-entry-1",
        "position_key": "spx-main",
        "underlier": "SPX",
        "option_root": "SPXW",
        "bar_time": "2026-04-20T10:35:00-05:00",
        "short_put": 7105,
        "long_put": 7085,
        "short_call": 7115,
        "long_call": 7135,
        "quantity": 1,
        "target_entry_credit": 1.45,
        "vix1d": 18.2,
        "zTrend": 0.65,
    }


def _entry_payload_broken_wing():
    payload = _entry_payload()
    payload.update(
        {
            "event_id": "evt-entry-broken-wing",
            "position_key": "spx-broken-wing",
            "short_put": 7120,
            "long_put": 7095,
            "short_call": 7205,
            "long_call": 7240,
            "target_entry_credit": 2.40,
        }
    )
    return payload


def _entry_payload_needs_rounding():
    payload = _entry_payload()
    payload.update(
        {
            "event_id": "evt-entry-round-1",
            "position_key": "spx-rounding",
            "target_entry_credit": 0.9182,
        }
    )
    return payload


def _manage_payload():
    return {
        "strategy_name": "hermes_v3_bridge_test",
        "event_type": "MANAGE",
        "event_id": "evt-manage-1",
        "position_key": "spx-main",
        "underlier": "SPX",
        "bar_time": "2026-04-20T11:15:00-05:00",
        "vix1d": 17.9,
        "zShortRisk": 1.18,
    }


def _manage_payload_after_close():
    payload = _manage_payload()
    payload.update(
        {
            "event_id": "evt-manage-after-close",
            "bar_time": "2026-04-20T12:10:00-05:00",
        }
    )
    return payload


def _close_payload():
    return {
        "strategy_name": "hermes_v3_bridge_test",
        "event_type": "CLOSE",
        "event_id": "evt-close-1",
        "position_key": "spx-main",
        "underlier": "SPX",
        "bar_time": "2026-04-20T12:05:00-05:00",
        "target_close_debit": 1.45,
    }


def _entry_payload_after_close():
    payload = _entry_payload()
    payload.update(
        {
            "event_id": "evt-entry-2",
            "bar_time": "2026-04-20T13:15:00-05:00",
        }
    )
    return payload


def _close_payload_broken_wing():
    payload = _close_payload()
    payload.update(
        {
            "event_id": "evt-close-broken-wing",
            "position_key": "spx-broken-wing",
            "target_close_debit": 0.15,
        }
    )
    return payload


def _close_payload_needs_rounding():
    payload = _close_payload()
    payload.update(
        {
            "event_id": "evt-close-round-1",
            "position_key": "spx-rounding",
            "target_close_debit": 0.8331,
        }
    )
    return payload


@pytest.fixture()
def isolated_bridge(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ETRADE_DEFAULT_ACCOUNT_ID_KEY", "demo-account")
    monkeypatch.delenv("TRADINGVIEW_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("PINE_BRIDGE_POINT_VALUE", "100")
    return FakeClient()


def test_parse_pine_alert_maps_exit_to_close():
    event = parse_pine_alert_payload(
        {
            "strategy_name": "hermes",
            "event_type": "EXIT",
            "event_id": "evt-1",
            "position_key": "main",
            "underlier": "SPX",
        }
    )
    assert event.event_type == "CLOSE"


def test_parse_bar_date_accepts_unix_millis_string():
    event = parse_pine_alert_payload(
        {
            "strategy_name": "hermes",
            "event_type": "ENTRY",
            "event_id": "evt-2",
            "position_key": "main",
            "underlier": "SPX",
            "bar_time": "1776792900000",
        }
    )
    parsed = _parse_bar_date(event)
    assert parsed is not None


def test_pine_bridge_entry_manage_close_flow(isolated_bridge):
    client = isolated_bridge

    entry = process_pine_alert(_entry_payload(), client=client)
    assert entry["ok"] is True
    assert entry["duplicate"] is False
    assert entry["state"]["status"] == "open"
    assert entry["state"]["active"] is True
    assert entry["ticket"]["status"] == "previewed"
    assert entry["ticket"]["pricing"]["price_type"] == "NET_CREDIT"
    assert (
        entry["ticket"]["preview_request"]["PreviewOrderRequest"]["orderType"]
        == "IRON_CONDOR"
    )
    assert entry["live_mark"]["entry_credit_reference"] == pytest.approx(1.45, abs=1e-9)

    manage = process_pine_alert(_manage_payload(), client=client)
    assert manage["ok"] is True
    assert manage["state"]["status"] == "open"
    assert manage["state"]["latest_aux"]["vix1d"] == pytest.approx(17.9, abs=1e-9)
    assert manage["live_mark"]["close_mid_debit"] is not None

    close = process_pine_alert(_close_payload(), client=client)
    assert close["ok"] is True
    assert close["state"]["status"] == "closed"
    assert close["state"]["active"] is False
    assert close["close_ticket"]["status"] == "previewed"
    assert close["close_ticket"]["pricing"]["price_type"] == "NET_DEBIT"
    assert (
        close["close_ticket"]["preview_request"]["PreviewOrderRequest"]["orderType"]
        == "IRON_CONDOR"
    )

    states = list_pine_states()
    tickets = list_tickets()
    assert len(states) == 1
    assert len(tickets) == 2


def test_pine_bridge_manage_after_close_does_not_reopen_state(isolated_bridge):
    client = isolated_bridge

    process_pine_alert(_entry_payload(), client=client)
    close = process_pine_alert(_close_payload(), client=client)
    stale_manage = process_pine_alert(_manage_payload_after_close(), client=client)

    assert stale_manage["ok"] is True
    assert stale_manage["ignored"] is True
    assert stale_manage["state"]["status"] == "closed"
    assert stale_manage["state"]["active"] is False
    assert stale_manage["state"]["close_ticket_id"] == close["close_ticket"]["ticket_id"]
    assert stale_manage["state"]["last_event_type"] == "MANAGE"
    assert [event["event_type"] for event in stale_manage["state"]["event_log"]][-2:] == [
        "CLOSE",
        "MANAGE",
    ]


def test_pine_bridge_ignores_entry_when_state_already_active(isolated_bridge):
    client = isolated_bridge

    process_pine_alert(_entry_payload(), client=client)
    second_entry = _entry_payload_after_close()
    second_entry["short_put"] = 7000
    ignored = process_pine_alert(second_entry, client=client)

    assert ignored["ok"] is True
    assert ignored["ignored"] is True
    assert ignored["reason"] == "state_already_active"
    assert ignored["state"]["status"] == "open"
    assert ignored["state"]["active"] is True
    assert ignored["state"]["current_strikes"]["short_put"] == 7105


def test_pine_bridge_new_entry_after_close_clears_old_close_ticket(isolated_bridge):
    client = isolated_bridge

    process_pine_alert(_entry_payload(), client=client)
    close = process_pine_alert(_close_payload(), client=client)
    second_entry = process_pine_alert(_entry_payload_after_close(), client=client)

    assert close["state"]["close_ticket_id"] is not None
    assert second_entry["ok"] is True
    assert second_entry["state"]["status"] == "open"
    assert second_entry["state"]["active"] is True
    assert second_entry["state"]["close_ticket_id"] is None
    assert second_entry["state"]["closed_at"] is None
    assert [event["event_type"] for event in second_entry["state"]["event_log"]][-2:] == [
        "CLOSE",
        "ENTRY",
    ]


def test_pine_bridge_live_mark_uses_spx_for_spxw_preview_symbols(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ETRADE_DEFAULT_ACCOUNT_ID_KEY", "demo-account")
    monkeypatch.delenv("TRADINGVIEW_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("PINE_BRIDGE_POINT_VALUE", "100")
    client = StrictSpxChainClient()

    entry = process_pine_alert(_entry_payload(), client=client)
    manage = process_pine_alert(_manage_payload(), client=client)

    assert entry["ok"] is True
    assert {leg["symbol"] for leg in entry["ticket"]["legs"]} == {"SPXW"}
    assert manage["ok"] is True
    assert manage["live_mark"]["matched_symbol"] == "SPX"
    assert "SPXW" not in client.option_chain_symbols


def test_pine_bridge_manage_records_event_when_live_mark_fails(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ETRADE_DEFAULT_ACCOUNT_ID_KEY", "demo-account")
    monkeypatch.delenv("TRADINGVIEW_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("PINE_BRIDGE_POINT_VALUE", "100")
    client = FailingRefreshClient()

    process_pine_alert(_entry_payload(), client=client)
    client.fail_chain = True
    manage = process_pine_alert(_manage_payload(), client=client)

    assert manage["ok"] is True
    assert manage["state"]["status"] == "open"
    assert manage["state"]["active"] is True
    assert manage["state"]["event_log"][-1]["event_type"] == "MANAGE"
    assert "error" in manage["state"]["latest_live_mark"]


def test_pine_bridge_close_builds_ticket_when_live_mark_fails_but_target_debit_exists(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ETRADE_DEFAULT_ACCOUNT_ID_KEY", "demo-account")
    monkeypatch.delenv("TRADINGVIEW_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("PINE_BRIDGE_POINT_VALUE", "100")
    client = FailingRefreshClient()

    process_pine_alert(_entry_payload(), client=client)
    client.fail_chain = True
    close = process_pine_alert(_close_payload(), client=client)

    assert close["ok"] is True
    assert close["state"]["status"] == "closed"
    assert close["state"]["active"] is False
    assert close["state"]["event_log"][-1]["event_type"] == "CLOSE"
    assert close["close_ticket"]["status"] == "previewed"
    assert "error" in close["state"]["latest_live_mark"]


def test_pine_bridge_duplicate_event_is_idempotent(isolated_bridge):
    client = isolated_bridge
    first = process_pine_alert(_entry_payload(), client=client)
    second = process_pine_alert(_entry_payload(), client=client)

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["duplicate"] is True
    assert len(list_pine_states()) == 1
    assert len(list_tickets()) == 1


def test_pine_bridge_entry_snaps_spxw_credit_to_valid_increment(isolated_bridge):
    client = isolated_bridge

    entry = process_pine_alert(_entry_payload_needs_rounding(), client=client)

    assert entry["ok"] is True
    ticket = entry["ticket"]
    assert ticket["pricing"]["limit_price"] == pytest.approx(0.90, abs=1e-9)
    assert (
        ticket["preview_request"]["PreviewOrderRequest"]["Order"][0]["limitPrice"]
        == pytest.approx(0.90, abs=1e-9)
    )
    assert any(
        "SPX/SPXW complex-order $0.05 net price increments" in warning
        for warning in ticket["warnings"]
    )


def test_pine_bridge_close_snaps_spxw_debit_to_valid_increment(isolated_bridge):
    client = isolated_bridge

    process_pine_alert(_entry_payload_needs_rounding(), client=client)
    close = process_pine_alert(_close_payload_needs_rounding(), client=client)

    assert close["ok"] is True
    close_ticket = close["close_ticket"]
    assert close_ticket["pricing"]["limit_price"] == pytest.approx(0.85, abs=1e-9)
    assert (
        close_ticket["preview_request"]["PreviewOrderRequest"]["Order"][0]["limitPrice"]
        == pytest.approx(0.85, abs=1e-9)
    )


def test_pine_bridge_broken_wing_entry_uses_generic_spreads_order_type(isolated_bridge):
    client = isolated_bridge

    entry = process_pine_alert(_entry_payload_broken_wing(), client=client)

    assert entry["ok"] is True
    assert (
        entry["ticket"]["preview_request"]["PreviewOrderRequest"]["orderType"]
        == "SPREADS"
    )


def test_pine_bridge_broken_wing_close_uses_generic_spreads_order_type(isolated_bridge):
    client = isolated_bridge

    process_pine_alert(_entry_payload_broken_wing(), client=client)
    close = process_pine_alert(_close_payload_broken_wing(), client=client)

    assert close["ok"] is True
    assert (
        close["close_ticket"]["preview_request"]["PreviewOrderRequest"]["orderType"]
        == "SPREADS"
    )


def test_pine_bridge_secret_mismatch_raises(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ETRADE_DEFAULT_ACCOUNT_ID_KEY", "demo-account")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "expected-secret")

    with pytest.raises(RuntimeError, match="secret mismatch"):
        process_pine_alert(_entry_payload(), client=FakeClient())
