# Hermes Pine Bridge Copy/Paste Patch

This is the most literal version of the Hermes Pine patch.

Use this if you are editing the exact Hermes script you pasted in chat and want to:

- search for a block
- paste a block
- replace a block

without having to reinterpret the more general patch guide.

For the higher-level explanation, see:

- [docs/hermes-pine-bridge-patch.md](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/docs/hermes-pine-bridge-patch.md)
- [docs/tradingview-bridge.md](C:/Users/Gethe/starship-alpha-bot-main/starship-alpha-bot-main/docs/tradingview-bridge.md)

## Step 1. Add the new input block

### Find this exact block

```pinescript
groupManage = "Management Alerts"
enableManageAlerts = input.bool(true, "Enable HOLD/CAUTION/DANGER alerts?", group=groupManage, tooltip="Turns ON alerting for management-state transitions.\n\nUseful for monitoring how the risk engine is behaving live.")
warnCushionSigma   = input.float(0.25, "CAUTION if zShortRisk <= X", step=0.05, group=groupManage, tooltip="Caution threshold based on zShortRisk.\n\nHigher = earlier warnings.\nLower = fewer warnings.")
warnCushionPct     = input.float(0.15, "CAUTION if cushion < X of short width", step=0.01, group=groupManage, tooltip="Caution threshold based on the nearest short-side cushion as a fraction of its width.")
warnZAbs           = input.float(1.50, "CAUTION if |zTrend| >= X", step=0.10, group=groupManage, tooltip="Caution threshold based on absolute zTrend.")
warnLateMin        = input.int(330, "CAUTION if minutes since open >= N", minval=0, maxval=389, group=groupManage, tooltip="Late-day caution threshold measured in minutes after the open.")
```

### Paste this immediately after it

```pinescript
groupTvBridge = "TradingView -> Python Bridge"
enableTvBridge = input.bool(false, "Enable Python bridge alerts?", group=groupTvBridge)
tvBridgeSecret = input.string("", "Bridge secret", group=groupTvBridge)
tvBridgeStrategyName = input.string("hermes_v3_2", "Bridge strategy name", group=groupTvBridge)
tvBridgePositionKey = input.string("spx-main", "Bridge position key", group=groupTvBridge)
tvBridgeOptionRoot = input.string("SPXW", "Bridge option root", group=groupTvBridge)
tvBridgeSendManage = input.bool(true, "Send MANAGE alerts?", group=groupTvBridge)
tvBridgeSendStateChangeOnly = input.bool(true, "Only send MANAGE on HOLD/CAUTION/DANGER change?", group=groupTvBridge)
```

## Step 2. Add the bridge helper functions

### Find this exact block

```pinescript
f_json_str(string s)  => "\"" + s + "\""
f_json_num(float x)   => na(x) ? "null" : str.tostring(x, "0.00##")
f_json_int(float x)   => na(x) ? "null" : str.tostring(int(x))
f_json_bool(bool b)   => b ? "true" : "false"

f_bandcode() => bandMethod == "ATR" ? 1 : 0
```

### Replace it with this exact block

```pinescript
f_json_str(string s)  => "\"" + s + "\""
f_json_num(float x)   => na(x) ? "null" : str.tostring(x, "0.00##")
f_json_int(float x)   => na(x) ? "null" : str.tostring(int(x))
f_json_bool(bool b)   => b ? "true" : "false"

f_bridge_underlier() =>
    isIndexSPX ? "SPX" : syminfo.ticker

f_bridge_event_id(string eventType, string reason) =>
    eventType + "|" + reason + "|" + str.tostring(time_close) + "|" + str.tostring(bar_index)

f_bridge_regime() =>
    pin ? "PIN" : "EXPAND"

f_bridge_base(string eventType, string reason) =>
    string s = "{"
    s := s + "\"secret\":" + f_json_str(tvBridgeSecret) + ","
    s := s + "\"strategy_name\":" + f_json_str(tvBridgeStrategyName) + ","
    s := s + "\"event_type\":" + f_json_str(eventType) + ","
    s := s + "\"event_id\":" + f_json_str(f_bridge_event_id(eventType, reason)) + ","
    s := s + "\"position_key\":" + f_json_str(tvBridgePositionKey) + ","
    s := s + "\"underlier\":" + f_json_str(f_bridge_underlier()) + ","
    s := s + "\"option_root\":" + f_json_str(tvBridgeOptionRoot) + ","
    s := s + "\"bar_time\":" + f_json_str(str.tostring(time_close)) + ","
    s := s + "\"expiry_year\":" + f_json_int(year) + ","
    s := s + "\"expiry_month\":" + f_json_int(month) + ","
    s := s + "\"expiry_day\":" + f_json_int(dayofmonth) + ","
    s := s + "\"quantity\":" + f_json_int(lastEntryContracts) + ","
    s := s + "\"note\":" + f_json_str(reason) + ","
    s := s + "\"ivrv\":" + f_json_num(ivOverRv) + ","
    s := s + "\"pop_between\":" + f_json_num(popBetween) + ","
    s := s + "\"regime\":" + f_json_str(f_bridge_regime()) + ","
    s := s + "\"add\":" + f_json_num(nyseADD) + ","
    s := s + "\"zTrigger\":" + f_json_num(zTrigger) + ","
    s := s + "\"zShape\":" + f_json_num(zShape) + ","
    s := s + "\"zTrend\":" + f_json_num(zTrend) + ","
    s := s + "\"zVWAPRecenter\":" + f_json_num(zVWAPRecenter) + ","
    s := s + "\"zBody\":" + f_json_num(zBody) + ","
    s := s + "\"zShortRisk\":" + f_json_num(zShortRisk) + ","
    s := s + "\"surface_edge\":" + f_json_num(surfaceEdgeScore) + ","
    s := s + "\"surface_stress\":" + f_json_num(surfaceStressScore) + ","
    s := s + "\"surface_skew\":" + f_json_num(surfaceSkewScore)
    s

f_bridge_entry_payload(string reason) =>
    string s = f_bridge_base("ENTRY", reason)
    s := s + ",\"short_put\":" + f_json_num(entryPlanSPut)
    s := s + ",\"long_put\":" + f_json_num(entryPlanLPut)
    s := s + ",\"short_call\":" + f_json_num(entryPlanSCall)
    s := s + ",\"long_call\":" + f_json_num(entryPlanLCall)
    s := s + ",\"target_entry_credit\":" + f_json_num(entryPlanCreditPts)
    s := s + "}"
    s

f_bridge_manage_payload(string reason) =>
    f_bridge_base("MANAGE", reason) + "}"

f_bridge_close_payload(string reason) =>
    string s = f_bridge_base("CLOSE", reason)
    s := s + ",\"target_close_debit\":" + f_json_num(modelCloseDebitPts)
    s := s + "}"
    s

f_bandcode() => bandMethod == "ATR" ? 1 : 0
```

## Step 3. Patch the entry block

This is the most important one.

### Find this exact line pair

```pinescript
if entryEvent
    if not inTrade
```

### Replace it with this

```pinescript
if entryEvent
    bool bridgeFirstEntry = not inTrade
    if not inTrade
```

### Then find this exact existing entry alert block

```pinescript
    if enableAlerts
        alert(f_build_payload("ENTRY", currentIsAfternoon ? "ENTRY_PM" : "ENTRY_AM", float(na)), f_alert_freq())
```

### Replace it with this exact block

```pinescript
    if enableAlerts
        alert(f_build_payload("ENTRY", currentIsAfternoon ? "ENTRY_PM" : "ENTRY_AM", float(na)), f_alert_freq())
    if enableAlerts and enableTvBridge and bridgeFirstEntry
        alert(f_bridge_entry_payload(currentIsAfternoon ? "ENTRY_PM" : "ENTRY_AM"), f_alert_freq())
```

Why this exact patch:

- `bridgeFirstEntry` keeps the Python bridge from receiving a duplicate `ENTRY` when Hermes legs in asynchronously.
- `f_bridge_entry_payload(...)` uses the frozen `entryPlan*` values, which is what you want Python/E*TRADE to treat as the actual chosen condor.

## Step 4. Patch the manage alert block

### Find this exact block

```pinescript
if enableAlerts and enableManageAlerts and stateChange and stateCode > 0
    why = stateCode == 1 ? "HOLD" : stateCode == 2 ? "CAUTION" : "DANGER"
    alert(f_build_payload("MANAGE", why, float(na)), f_alert_freq())
```

### Replace it with this exact block

```pinescript
if enableAlerts and enableManageAlerts and stateChange and stateCode > 0
    why = stateCode == 1 ? "HOLD" : stateCode == 2 ? "CAUTION" : "DANGER"
    alert(f_build_payload("MANAGE", why, float(na)), f_alert_freq())

if enableAlerts and enableTvBridge and tvBridgeSendManage
    bool fireManage = tvBridgeSendStateChangeOnly ? stateChange and stateCode > 0 : inTrade
    if fireManage
        string manageWhy = stateCode == 1 ? "HOLD" : stateCode == 2 ? "CAUTION" : stateCode == 3 ? "DANGER" : "MANAGE"
        alert(f_bridge_manage_payload(manageWhy), f_alert_freq())
```

Recommended starting setting:

- leave `tvBridgeSendStateChangeOnly = true`

That keeps the webhook traffic lighter while still refreshing real broker mark/P&L when Hermes changes management state.

## Step 5. Patch each exit branch

For each branch below, find the exact existing Hermes alert block and replace it with the expanded version.

### 5A. Breach put

#### Find

```pinescript
    if enableAlerts
        alert(f_build_payload("EXIT", "BREACH_PUT", dollarsPerCondor), f_alert_freq())
```

#### Replace with

```pinescript
    if enableAlerts
        alert(f_build_payload("EXIT", "BREACH_PUT", dollarsPerCondor), f_alert_freq())
    if enableAlerts and enableTvBridge
        alert(f_bridge_close_payload("BREACH_PUT"), f_alert_freq())
```

### 5B. Breach call

#### Find

```pinescript
    if enableAlerts
        alert(f_build_payload("EXIT", "BREACH_CALL", dollarsPerCondor), f_alert_freq())
```

#### Replace with

```pinescript
    if enableAlerts
        alert(f_build_payload("EXIT", "BREACH_CALL", dollarsPerCondor), f_alert_freq())
    if enableAlerts and enableTvBridge
        alert(f_bridge_close_payload("BREACH_CALL"), f_alert_freq())
```

### 5C. Take profit / ladder exit

#### Find

```pinescript
    if enableAlerts
        alert(f_build_payload("EXIT", useModelExitLadderEff ? tpExitReasonNow : "TAKE_PROFIT", dollarsPerCondor), f_alert_freq())
```

#### Replace with

```pinescript
    if enableAlerts
        alert(f_build_payload("EXIT", useModelExitLadderEff ? tpExitReasonNow : "TAKE_PROFIT", dollarsPerCondor), f_alert_freq())
    if enableAlerts and enableTvBridge
        alert(f_bridge_close_payload(useModelExitLadderEff ? tpExitReasonNow : "TAKE_PROFIT"), f_alert_freq())
```

### 5D. Time stop

#### Find

```pinescript
    if enableAlerts
        alert(f_build_payload("EXIT", "TIME_STOP", dollarsPerCondor), f_alert_freq())
```

#### Replace with

```pinescript
    if enableAlerts
        alert(f_build_payload("EXIT", "TIME_STOP", dollarsPerCondor), f_alert_freq())
    if enableAlerts and enableTvBridge
        alert(f_bridge_close_payload("TIME_STOP"), f_alert_freq())
```

### 5E. EOD settle

#### Find

```pinescript
    if enableAlerts
        alert(f_build_payload("EXIT", "EOD_SETTLE", dollarsPerCondor), f_alert_freq())
```

#### Replace with

```pinescript
    if enableAlerts
        alert(f_build_payload("EXIT", "EOD_SETTLE", dollarsPerCondor), f_alert_freq())
    if enableAlerts and enableTvBridge
        alert(f_bridge_close_payload("EOD_SETTLE"), f_alert_freq())
```

## Step 6. What not to change

Do not remove these existing Hermes mechanics:

- the current `f_build_payload(...)` alerts
- the existing `ENTRY`, `MANAGE`, and `EXIT` logic
- the `entryPlan*` freezing logic

For now, the bridge should sit alongside the current Hermes alerts, not replace the strategy logic.

## Step 7. Expected result after patching

After these edits:

- Hermes still decides when to enter, manage, and exit
- the Python bridge receives one four-leg `ENTRY`
- `MANAGE` alerts cause Python to refresh live E*TRADE mark/P&L
- `CLOSE` alerts cause Python to build a real live close preview ticket

## Step 8. First test sequence

1. Turn `enableTvBridge = true`
2. Keep `tvBridgeSendStateChangeOnly = true`
3. Point TradingView webhook alerts at:

```text
POST /webhooks/tradingview
```

4. Trigger one real Hermes setup
5. Inspect:

```text
GET /pine-bridge/states
GET /pine-bridge/states/{state_id}
```

If you want, the next thing I can do is make a final "super-short checklist" version with just:

- search text
- replacement text
- no explanation

for each edit.
