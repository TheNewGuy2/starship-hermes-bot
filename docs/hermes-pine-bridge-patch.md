# Hermes Pine -> E*TRADE Bridge Patch

This guide is the concrete Pine-side patch for the Hermes script you pasted in chat.

If you want the most literal search/replace version instead of the explanatory guide, use:

```text
docs/hermes-pine-bridge-copy-paste.md
```

The goal is:

- keep Hermes as the signal engine
- keep using TradingView for aux data like VIX/VIX1D and internals
- stop relying on Pine's modeled option pricing for live trade valuation
- send `ENTRY`, `MANAGE`, and `CLOSE` alerts to the Python bridge

The Python bridge endpoint is:

```text
POST /webhooks/tradingview
```

## What to add to Hermes

Add a small bridge input group plus helper functions. These are designed to fit your current Hermes style because your script already has:

- `f_json_str(...)`
- `f_json_num(...)`
- `f_json_bool(...)`
- `f_alert_freq()`

## 1. Add inputs

Paste this near your alert inputs.

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

## 2. Add helper functions

Paste this below your existing JSON helpers.

```pinescript
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
```

Why `entryPlan*` matters in this Hermes build:

- Hermes freezes the chosen condor geometry into `entryPlanSPut`, `entryPlanLPut`, `entryPlanSCall`, `entryPlanLCall`, and `entryPlanCreditPts`.
- That is exactly what the Python bridge should receive.
- Using the rolling `currentCandidate*` values can drift if later bars rebuild a different candidate while the trade is already active.

## 3. Wire the entry alert

Inside your existing `if entryEvent` block, add a `bridgeFirstEntry` guard and only fire the bridge on the first flat-to-open transition.

This is important because your Hermes script supports async legging. The Python bridge currently expects one full condor `ENTRY`, not repeated partial leg entries.

Use this exact pattern:

```pinescript
if entryEvent
    bool bridgeFirstEntry = not inTrade
    if not inTrade
        enteredToday := true
        tradeDay := time("D")
        entryBarTime := time_close
        currentIsAfternoon := currentWindowIsAfternoon
        lastEntryCreditPts := 0.0
        entryMinsLeftRTH := minsLeftRTH
        callEntryMinsLeftRTH := na
        putEntryMinsLeftRTH := na
        morphCreditCollected := 0.0
        callMorphed := false
        putMorphed := false
        callCreditPts := na
        putCreditPts := na
        peakModelClosePnlPts := na
        peakModelCaptureFrac := na
        givebackArmed := false

        // Freeze the geometry selected at the moment of the FIRST leg entry.
        entryPlanM         := currentCandidateM
        entryPlanSigmaPts  := currentCandidateSigmaPts
        entryPlanSPut      := currentCandidateSPut
        entryPlanLPut      := currentCandidateLPut
        entryPlanSCall     := currentCandidateSCall
        entryPlanLCall     := currentCandidateLCall
        entryPlanWidth     := currentCandidateWidth
        entryPlanCreditPts := currentCandidateCreditPtsUsed
        ...

    ...

    if enableAlerts
        alert(f_build_payload("ENTRY", currentIsAfternoon ? "ENTRY_PM" : "ENTRY_AM", float(na)), f_alert_freq())

    if enableAlerts and enableTvBridge and bridgeFirstEntry
        alert(f_bridge_entry_payload(currentIsAfternoon ? "ENTRY_PM" : "ENTRY_AM"), f_alert_freq())
```

That should sit alongside your current Hermes JSON alert, or replace it if you want the bridge to be the only webhook payload.

### Minimal diff version

If you prefer a surgical patch instead of re-reading the whole block, make these two changes.

1. At the top of the `if entryEvent` block:

```pinescript
    bool bridgeFirstEntry = not inTrade
```

2. Right after the existing Hermes entry alert:

```pinescript
    if enableAlerts and enableTvBridge and bridgeFirstEntry
        alert(f_bridge_entry_payload(currentIsAfternoon ? "ENTRY_PM" : "ENTRY_AM"), f_alert_freq())
```

## 4. Wire manage alerts

You already compute:

- `holdEvent`
- `cautionEvent`
- `dangerEvent`

That is a nice clean place to trigger live broker mark refreshes.

Add:

```pinescript
if enableAlerts and enableTvBridge and tvBridgeSendManage
    bool fireManage = tvBridgeSendStateChangeOnly ? stateChange and stateCode > 0 : inTrade
    if fireManage
        string manageWhy = stateCode == 1 ? "HOLD" : stateCode == 2 ? "CAUTION" : stateCode == 3 ? "DANGER" : "MANAGE"
        alert(f_bridge_manage_payload(manageWhy), f_alert_freq())
```

If you want fewer webhook hits, leave `tvBridgeSendStateChangeOnly = true`.

### Exact insertion point

In your current Hermes script, place that immediately after this existing block:

```pinescript
if enableAlerts and enableManageAlerts and stateChange and stateCode > 0
    why = stateCode == 1 ? "HOLD" : stateCode == 2 ? "CAUTION" : "DANGER"
    alert(f_build_payload("MANAGE", why, float(na)), f_alert_freq())
```

## 5. Wire close alerts

In each exit branch, add the bridge close alert right next to the existing Hermes alert.

Examples:

```pinescript
    if enableAlerts and enableTvBridge
        alert(f_bridge_close_payload("BREACH_PUT"), f_alert_freq())
```

```pinescript
    if enableAlerts and enableTvBridge
        alert(f_bridge_close_payload("BREACH_CALL"), f_alert_freq())
```

```pinescript
    if enableAlerts and enableTvBridge
        alert(f_bridge_close_payload(useModelExitLadderEff ? tpExitReasonNow : "TAKE_PROFIT"), f_alert_freq())
```

```pinescript
    if enableAlerts and enableTvBridge
        alert(f_bridge_close_payload("TIME_STOP"), f_alert_freq())
```

```pinescript
    if enableAlerts and enableTvBridge
        alert(f_bridge_close_payload("EOD_SETTLE"), f_alert_freq())
```

### Exact insertion points in your current script

Add the bridge alert immediately after each existing `f_build_payload("EXIT", ...)` alert.

#### Breach put branch

```pinescript
    if enableAlerts
        alert(f_build_payload("EXIT", "BREACH_PUT", dollarsPerCondor), f_alert_freq())
    if enableAlerts and enableTvBridge
        alert(f_bridge_close_payload("BREACH_PUT"), f_alert_freq())
```

#### Breach call branch

```pinescript
    if enableAlerts
        alert(f_build_payload("EXIT", "BREACH_CALL", dollarsPerCondor), f_alert_freq())
    if enableAlerts and enableTvBridge
        alert(f_bridge_close_payload("BREACH_CALL"), f_alert_freq())
```

#### TP branch

```pinescript
    if enableAlerts
        alert(f_build_payload("EXIT", useModelExitLadderEff ? tpExitReasonNow : "TAKE_PROFIT", dollarsPerCondor), f_alert_freq())
    if enableAlerts and enableTvBridge
        alert(f_bridge_close_payload(useModelExitLadderEff ? tpExitReasonNow : "TAKE_PROFIT"), f_alert_freq())
```

#### Time stop branch

```pinescript
    if enableAlerts
        alert(f_build_payload("EXIT", "TIME_STOP", dollarsPerCondor), f_alert_freq())
    if enableAlerts and enableTvBridge
        alert(f_bridge_close_payload("TIME_STOP"), f_alert_freq())
```

#### EOD branch

```pinescript
    if enableAlerts
        alert(f_build_payload("EXIT", "EOD_SETTLE", dollarsPerCondor), f_alert_freq())
    if enableAlerts and enableTvBridge
        alert(f_bridge_close_payload("EOD_SETTLE"), f_alert_freq())
```

## 6. Optional aux fields

If your local Hermes copy already has explicit runtime series for:

- `vix1d`
- `vix`
- `vix9d`
- `vvix`
- `skew`
- `vx1`
- `vx2`

add them directly into `f_bridge_base(...)` as top-level fields:

```pinescript
    s := s + ",\"vix1d\":" + f_json_num(YOUR_VIX1D_RUNTIME_SERIES)
    s := s + ",\"vix\":" + f_json_num(YOUR_VIX_RUNTIME_SERIES)
```

I left those as optional here because runtime variable names can differ a bit across Pine revisions, but the Python bridge is already ready to store them once you send them.

## 7. Important notes for your current Hermes build

- `ENTRY` should only fire once per new trade state from the bridge's point of view.
- If `useLegging` is on, later same-trade leg additions should not send a second bridge `ENTRY`.
- The bridge is currently a four-leg condor bridge, not a partial-leg execution bridge.
- `MANAGE` is the safe place to ask Python to refresh real E*TRADE mark/P&L while Hermes keeps controlling the decision logic.
- `CLOSE` should fire for the same reasons Hermes already exits, but Python should be the source of truth for the live close preview.

## Why this mapping works well

- `ENTRY` sends the exact candidate condor that Hermes chose.
- `MANAGE` does not trust Pine option pricing. It simply tells Python to refresh live E*TRADE quotes and recompute mark/P&L.
- `CLOSE` carries the exit reason, but Python still derives the real close preview from live broker quotes.

That means Hermes keeps all the candle logic, vol-proxy logic, profile logic, and timing logic, while Python/E*TRADE becomes the source of truth for option valuation.

## Recommended rollout

1. Enable the bridge with preview-only flow.
2. Send real Hermes alerts into `/webhooks/tradingview`.
3. Inspect:
   - `GET /pine-bridge/states`
   - `GET /pine-bridge/states/{state_id}`
4. Compare Pine intent vs live E*TRADE marks for a while.
5. Only after that, consider guarded order submission.
