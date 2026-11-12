# Case Study: An "SMC + CMF" Trading Strategy, Verified

This is the record of putting a trading strategy — Smart Money Concepts
order blocks plus Chaikin Money Flow, on Bitcoin — through the verifier:
rule sealed before any data was seen, measured against a coin, and
judged on a sample large enough to mean something.

**Verdict: NOT_CONFIRMED.** Across three pre-registered looks the rule
never separated from a coin flip. The one encouraging result (59% on
daily BTC) did not survive when the same sealed rule was tested on more
markets.

## The rule, made mechanical

The strategy as stated: *go long when a blue (bullish) order block
sits in the discount zone and Chaikin Money Flow over 20 bars is above
zero.* Each phrase became a graph_v1 building block
(full design: [smc_cmf_strategy_design.md](smc_cmf_strategy_design.md)):

| Phrase | Mechanical definition |
|---|---|
| CMF(20) > 0 | `cmf` primitive: Σ(money-flow volume) / Σ(volume) over 20 bars |
| Discount zone | position of close inside the 50-bar high–low range < 0.5 — composed from existing primitives, no new code |
| Blue order block | `order_block` condition: a down bar becomes a block once price pushes ≥ 1× its range above it within 6 bars; fires when the close re-enters the block |
| Outcome | favorable if price touches +band before −band within the horizon |

No reported backtest figure was used as a target. The only question
was whether the rule, once sealed, beats a coin.

## Three sealed looks

Each look was a separate family, registered (windows, markets, and the
definition file's SHA-256 sealed) before its data was downloaded. The
confirmation windows were never touched.

| Look | Bars | Sample | Favorable | Wilson 95% | vs coin |
|---|---|---:|---:|---|---|
| 1. BTC, 2-hour, ±1% / 5 days | 2h | 147 | 42.9% | [35.1, 50.9] | no edge |
| 2. BTC, daily, ±5% / 30 days | 1d | 61 | 59.0% | [46.5, 70.5] | straddles 50%, underpowered |
| 3. BTC+ETH+SOL pooled, daily | 1d | **155** | **53.5%** | **[45.7, 61.2]** | **straddles 50%** (p = 0.38) |

Look 3 by market: BTC 36/61 (59.0%), ETH 32/59 (54.2%),
SOL 15/35 (42.9%, below a coin).

Registration records: [2h](SMC_CMF_DISCOUNT_OB_REGISTRATION.md) ·
[daily](SMC_CMF_DISCOUNT_OB_1D_REGISTRATION.md) ·
[pooled](SMC_CMF_DISCOUNT_OB_1D_POOL_REGISTRATION.md) ·
definitions in [`definitions/`](../definitions).

## Reading it honestly

- **The 59% was the trap.** On 61 trades, a fair coin lands at 59% or
  better about one time in ten. Pooling was decided *in advance* as the
  final look precisely to test whether 59% was real. It regressed to
  53.5% — the signature of noise, not edge.
- **Picking BTC alone would be cherry-picking.** The sealed rule was
  the same on all three markets; one market looking good is not a
  property of the rule.
- **The freeze gate was not met** (it needs both an edge and ≥ 100
  occurrences), so the confirmation window was never opened. There was
  nothing to confirm.
- **Even 53.5% at face value is not P&L.** It is a directional-touch
  rate on a symmetric band, before fees and perpetual funding over
  30-day holds.

## Is the "no" trustworthy?

A verdict is only as good as the instrument. The same definition that
produced the "no" was run on pure random walks (where the true answer is
50%): it read **50.4%, 43.7%, and 53.8%** — no invented edge. And the
measurement engine reads **92.0%** when a real edge is planted. See
[VERIFIER_CALIBRATION.md](VERIFIER_CALIBRATION.md); reproduce with
`python scripts/calibrate.py`.

Research diagnostics only. Not investment advice.
