# E*TRADE Live Cutover

This repo now has an engine-side E*TRADE probe mode that can publish quote and option-chain snapshots into the existing Slack ingest path.

## What this can do now

- authenticate to E*TRADE using the repo's stored OAuth token/session
- fetch quote and option-chain data from E*TRADE
- try to build a condor candidate from the returned chain deltas
- publish a `STRATEGY_SIGNAL` fact that the local web app formats and relays to Slack

## Important limitation

Sandbox is only useful for wiring tests. It returns stored sample data rather than a realistic live chain, so it is not suitable for validating real spot prices, real deltas, or condor construction quality.

To test real prices and deltas, you need:

- a production E*TRADE app
- production consumer key and secret
- `ETRADE_ENV=live`
- a fresh live OAuth login

## Local cutover steps

1. Update your local `.env`:

```env
BROKER_PROVIDER=etrade
ETRADE_ENV=live
ETRADE_CONSUMER_KEY=...
ETRADE_CONSUMER_SECRET=...
ETRADE_CALLBACK_URL=oob
```

2. Reconnect E*TRADE in the browser so the saved token is for the live environment, not sandbox:

- start the web app
- open `/broker/etrade`
- connect again and complete the verifier flow

3. Make sure Slack is configured in `.env`:

```env
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

4. Start the local web service so it can receive engine facts and forward them to Slack:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_starship_web.ps1
```

5. Run a one-shot E*TRADE probe:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_etrade_probe_once.ps1 -Live
```

## Expected result

If live market data is working, Slack should receive an `E*TRADE Condor Probe` card with:

- current spot
- bid / ask / last
- option root and expiry
- chain Greek count
- condor strikes if enough chain data is available

## Troubleshooting

- `oauth_problem=token_expired`
  Reconnect from `/broker/etrade`.

- sample-looking prices or old expiries
  You are still on sandbox credentials or a sandbox token.

- no Slack message
  Confirm the web app is running and `SLACK_WEBHOOK_URL` is set.

- quote works but condor is missing
  The returned option chain did not include enough strikes or deltas yet. Inspect `data/facts.jsonl` and the Slack warning lines.
