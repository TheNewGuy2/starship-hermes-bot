# Heston Model + Bot Gate Primer

This bot uses a lightweight “Heston-inspired” gate to decide when it is safer to sell SPX 0DTE premium. The gate is not calibrating a full stochastic volatility model; it borrows the intuition that equity volatility mean-reverts and that implied richness vs realized (the variance risk premium) is a key signal.

## What is the Heston model?
- Spot process (under risk-neutral measure):  
  dS_t = r S_t dt + √v_t · S_t dW_t^S
- Variance process (CIR-style mean reversion):  
  dv_t = κ(θ − v_t) dt + σ √v_t dW_t^v  
  with correlation ρ between dW_t^S and dW_t^v.
- Parameters:  
  - κ: speed of mean reversion of variance  
  - θ: long-run variance level  
  - σ: vol-of-vol (how jumpy variance is)  
  - ρ: correlation between spot and variance shocks  
  - v_0: initial variance
- Why it matters: when variance mean-reverts and IV trades rich to expected realized variance, short-vol trades have positive expectancy; when front variance spikes or vol-of-vol is high, short-vol risk rises sharply.

## Gate signals we use
We approximate regime conditions with simple observables instead of fitting a full model:

- **RV windows (realized variance proxies):**  
  rv30, rv60, rv90 computed from trailing log returns of 5m bars (annualized vol in decimal form, e.g., 0.20 = 20%).
- **IV_ATM:** midpoint of nearest-strike call/put IV around the index level.
- **VRP (variance risk premium):** vrp = IV_ATM / RV60. Richness check: we want implied > realized.  
  - Floor: requires RV60 ≥ rv60_min to avoid division blowups.  
  - Min: vrp_min (default 1.25).  
  - Optional max: vrp_max to reject “fake richness” driven by tiny RV.
- **MR (mean reversion slope):** mr = rv30 − rv90. Front-back slope; we prefer non-positive or small positive values. Default tolerance mr_max = 0.05.
- **VoV proxy:** std dev of absolute returns over the 60m window (vov_proxy_from_returns). Optional ceiling vov_max to skip whipsaw/high vol-of-vol sessions.

## Gate logic (see `packages/starship_engine/src/starship_engine/heston.py`)
1. Require IV and RV60 to be present and RV60 ≥ rv60_min.  
2. Compute VRP; fail if VRP < vrp_min, or if vrp_max is set and VRP > vrp_max.  
3. Require rv30 and rv90 to be finite; fail if missing.  
4. Compute MR; fail if MR > mr_max.  
5. Optionally fail if VoV > vov_max (when configured).  
Result: `ok` + reason + the computed vrp/mr/vov for logging and downstream use.

## Why these checks?
- Selling 0DTE premium is most favorable when implied is meaningfully richer than expected realized (VRP high enough).  
- A steeply positive front-back slope (mr > 0) suggests rising near-term variance; short-vol entries get riskier.  
- Tiny realized vol can create fake VRP; we floor RV60 and optionally cap VRP.  
- Elevated vol-of-vol is associated with regime shifts and whipsaw; the optional VoV cap helps avoid that tape.

## Interpreting the outputs
- `ok=True`: regime is acceptable under current thresholds.  
- `reason`: human-readable gate failure/success reason.  
- `vrp`, `mr`, `vov`: echoed values (NaN when unavailable) for logging, stats, and alerts.

## Tuning suggestions
- Increase `vrp_min` to be pickier; decrease to allow more trades.  
- Adjust `mr_max` up slightly if you want to tolerate mild front-vol elevation.  
- Set `vrp_max` if you observe VRP spikes driven by anomalously low RV.  
- Set `vov_max` if you want to explicitly avoid high vol-of-vol days.  
- Raise `rv60_min` if your RV estimates are noisy; lower it if you trust the feed and want more passes.
