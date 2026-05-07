# TradingView Pine -> E*TRADE Bridge

This bridge keeps TradingView/Pine as the signal engine while using live E*TRADE data for:

- contract matching
- live entry preview tickets
- live mark and P/L refresh on manage alerts
- live close preview tickets on close alerts

## What it does

1. Pine sends a webhook alert to the web app.
2. The web app creates or updates a Pine bridge state in `data/pine_bridge_states/`.
3. On `ENTRY`, the app:
   - matches Pine strikes to live E*TRADE contracts
   - builds a trade ticket
   - prepares a live broker preview request
   - submits the preview to E*TRADE
4. On `MANAGE`, the app:
   - refreshes live E*TRADE quotes for the active legs
   - computes current close debit and estimated P/L
5. On `CLOSE`, the app:
   - refreshes live E*TRADE quotes
   - builds a close ticket with reversed actions
   - prepares a live `NET_DEBIT` preview request
   - submits the preview to E*TRADE

This is still preview-first. It does not auto-submit live orders unless you separately use the guarded execution flow.

## Required environment

Set these in your local `.env`:

```env
ETRADE_DEFAULT_ACCOUNT_ID_KEY=...
TRADINGVIEW_WEBHOOK_SECRET=choose-a-secret
PINE_BRIDGE_POINT_VALUE=100
```

Notes:

- `ETRADE_DEFAULT_ACCOUNT_ID_KEY` should already be set to your chosen live account.
- `TRADINGVIEW_WEBHOOK_SECRET` is optional but strongly recommended.
- `PINE_BRIDGE_POINT_VALUE` defaults to `100`.

## Webhook endpoint

TradingView should post alerts to:

```text
POST /webhooks/tradingview
```

If your web app is local-only, expose it the same way you expose the other web endpoints you already use.

## Local smoke test

Before wiring TradingView itself, you can post TradingView-style alerts directly from PowerShell.

Dry-run the payload first:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_pine_bridge_alert.ps1 -EventType ENTRY -Secret "your-secret" -DryRun
```

Send an entry:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_pine_bridge_alert.ps1 -EventType ENTRY -Secret "your-secret"
```

Refresh live mark / P&L:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_pine_bridge_alert.ps1 -EventType MANAGE -Secret "your-secret"
```

Preview a close:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_pine_bridge_alert.ps1 -EventType CLOSE -Secret "your-secret"
```

To test idempotency, rerun the same command with an explicit repeated `-EventId`.

The script defaults to:

- `http://127.0.0.1:8000/webhooks/tradingview`
- strategy name `hermes_v3_2`
- position key `spx-main`
- SPXW same-day-style example strikes

You can override those with flags like `-Url`, `-StrategyName`, `-PositionKey`, `-ShortPut`, `-LongPut`, `-ShortCall`, `-LongCall`, `-TargetEntryCredit`, and `-TargetCloseDebit`.

For after-hours testing of a future-dated structure, you can also pass an explicit expiry:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_pine_bridge_alert.ps1 `
  -Url "https://your-tunnel.trycloudflare.com/webhooks/tradingview" `
  -Secret "your-secret" `
  -EventType ENTRY `
  -StrategyName "hermes_v4_2" `
  -PositionKey "smoke-test-future" `
  -Underlier "SPX" `
  -OptionRoot "SPX" `
  -ExpiryYear 2026 `
  -ExpiryMonth 4 `
  -ExpiryDay 27
```

If you pass any expiry override, you must pass all three of:

- `-ExpiryYear`
- `-ExpiryMonth`
- `-ExpiryDay`

## Pine payload schema

### Entry

```json
{
  "secret": "your-secret",
  "strategy_name": "hermes_v3_2",
  "event_type": "ENTRY",
  "event_id": "hermes-2026-04-20-entry-1",
  "position_key": "spx-main",
  "underlier": "SPX",
  "option_root": "SPXW",
  "bar_time": "2026-04-20T10:35:00-05:00",
  "quantity": 1,
  "short_put": 7105,
  "long_put": 7085,
  "short_call": 7115,
  "long_call": 7135,
  "target_entry_credit": 1.45,
  "vix1d": 18.2,
  "vix": 19.1,
  "vix9d": 17.8,
  "vvix": 86.0,
  "skew": 142.5,
  "add": 950,
  "regime": "PIN"
}
```

### Manage

```json
{
  "secret": "your-secret",
  "strategy_name": "hermes_v3_2",
  "event_type": "MANAGE",
  "event_id": "hermes-2026-04-20-manage-1",
  "position_key": "spx-main",
  "underlier": "SPX",
  "bar_time": "2026-04-20T11:15:00-05:00",
  "vix1d": 17.9,
  "vix": 18.8,
  "add": 640,
  "zTrend": 0.75,
  "zShortRisk": 1.18
}
```

### Close

```json
{
  "secret": "your-secret",
  "strategy_name": "hermes_v3_2",
  "event_type": "CLOSE",
  "event_id": "hermes-2026-04-20-close-1",
  "position_key": "spx-main",
  "underlier": "SPX",
  "bar_time": "2026-04-20T12:05:00-05:00",
  "target_close_debit": 0.65
}
```

## Pine helper snippet

This is a simple pattern you can paste into Pine and adapt inside Hermes. It builds JSON manually so you can keep using Pine as the signal source while Python handles live broker pricing.

```pinescript
f_bridge_str(s) =>
    "\"" + s + "\""

f_bridge_num(x) =>
    na(x) ? "null" : str.tostring(x)

f_bridge_base(eventType, eventId, positionKey) =>
    "{"
    + "\"secret\":" + f_bridge_str("YOUR_SECRET") + ","
    + "\"strategy_name\":" + f_bridge_str("hermes_v3_2") + ","
    + "\"event_type\":" + f_bridge_str(eventType) + ","
    + "\"event_id\":" + f_bridge_str(eventId) + ","
    + "\"position_key\":" + f_bridge_str(positionKey) + ","
    + "\"underlier\":" + f_bridge_str("SPX") + ","
    + "\"option_root\":" + f_bridge_str("SPXW") + ","
    + "\"bar_time\":" + f_bridge_str(str.tostring(time_close)) + ","
    + "\"vix1d\":" + f_bridge_num(vix1d) + ","
    + "\"vix\":" + f_bridge_num(vix) + ","
    + "\"vix9d\":" + f_bridge_num(vix9d_rt) + ","
    + "\"vvix\":" + f_bridge_num(vvix_rt) + ","
    + "\"skew\":" + f_bridge_num(skew_rt) + ","
    + "\"add\":" + f_bridge_num(nyseADD) + ","
    + "\"zTrigger\":" + f_bridge_num(zTrigger) + ","
    + "\"zShape\":" + f_bridge_num(zShape) + ","
    + "\"zTrend\":" + f_bridge_num(zTrend) + ","
    + "\"zVWAPRecenter\":" + f_bridge_num(zVWAPRecenter)

f_bridge_entry(eventId, positionKey, shortPut, longPut, shortCall, longCall, targetCredit) =>
    f_bridge_base("ENTRY", eventId, positionKey)
    + ",\"short_put\":" + f_bridge_num(shortPut)
    + ",\"long_put\":" + f_bridge_num(longPut)
    + ",\"short_call\":" + f_bridge_num(shortCall)
    + ",\"long_call\":" + f_bridge_num(longCall)
    + ",\"target_entry_credit\":" + f_bridge_num(targetCredit)
    + "}"

f_bridge_manage(eventId, positionKey) =>
    f_bridge_base("MANAGE", eventId, positionKey) + "}"

f_bridge_close(eventId, positionKey, targetCloseDebit) =>
    f_bridge_base("CLOSE", eventId, positionKey)
    + ",\"target_close_debit\":" + f_bridge_num(targetCloseDebit)
    + "}"
```

Then call `alert(f_bridge_entry(...))`, `alert(f_bridge_manage(...))`, or `alert(f_bridge_close(...))` where your Hermes logic currently decides to emit an alert.

For the concrete Hermes-specific patch that matches the script you pasted, use:

```text
docs/hermes-pine-bridge-patch.md
```

For the most literal search/replace version, use:

```text
docs/hermes-pine-bridge-copy-paste.md
```

### Suggested Hermes mapping

- First real entry signal:
  call `f_bridge_entry(...)`
- Any intraday risk-management signal where you want live broker P/L refreshed:
  call `f_bridge_manage(...)`
- Any Pine-side exit signal:
  call `f_bridge_close(...)`

If Hermes already has JSON-alert helper code, it is usually better to extend that helper rather than add a second alert path.

## Supported fields

- `strategy_name`
- `event_type`
  - `ENTRY`
  - `MANAGE`
  - `CLOSE`
  - `EXIT` is accepted and normalized to `CLOSE`
- `event_id`
- `position_key`
- `underlier`
- `option_root`
- `bar_time`
- `expiry_year`
- `expiry_month`
- `expiry_day`
- `quantity`
- `short_put`
- `long_put`
- `short_call`
- `long_call`
- `target_entry_credit`
- `target_close_debit`
- `note`
- `aux`

The parser also pulls common aux fields like `vix1d`, `vix`, `vix9d`, `vvix`, `skew`, `add`, `vx1`, `vx2`, `ivrv`, `regime`, and the Hermes z-stack fields into the saved state.

`bar_time` can be:

- an ISO datetime string like `2026-04-20T10:35:00-05:00`
- or a Unix timestamp string from Pine like `str.tostring(time_close)`

If you already know the intended option expiry in Pine, sending `expiry_year`, `expiry_month`, and `expiry_day` is even better because it removes ambiguity.

## State inspection

List all Pine bridge states:

```text
GET /pine-bridge/states
```

Inspect one state:

```text
GET /pine-bridge/states/{state_id}
```

The state response includes:

- active/inactive status
- linked entry ticket id
- linked close ticket id
- last Pine payload
- latest aux snapshot
- latest live mark/P&L snapshot

## Behavior notes

- Idempotency is based on `event_id`.
- One active state is tracked per `strategy_name + position_key`.
- `ENTRY` requires all four strikes.
- `MANAGE` and `CLOSE` reuse the active entry ticket's matched contracts.
- `CLOSE` generates a separate close preview ticket with reversed actions and `NET_DEBIT` pricing.

## Current limitation

Pine still decides when to enter/manage/close. The bridge replaces Pine's modeled option valuation with live E*TRADE chain data, but it does not yet move Hermes decision logic into Python.
