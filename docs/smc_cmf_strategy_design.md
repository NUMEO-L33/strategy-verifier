# SMC + CMF Strategy — Verification Design

Purpose: put the owner's SMC + CMF strategy through the verifier's
pre-registration path (claim -> register -> measure -> judge), so its
edge is judged independently rather than taken from a backtest on a
chosen window.

Source: an SMC + CMF trading strategy. Bitcoin two-hour bars. Long when
a blue order block sits in the discount zone and CMF over twenty bars
is above zero; exit when price breaks below the most recent blue order
block. No reported backtest figure is the target of reproduction — a
backtest on a chosen window is exactly the kind of claim this tool
exists to test. We rebuild the rule from scratch on
pre-sealed windows and let the verdict fall where it falls.

## Three pillars

1. CMF > 0 — DONE. The `cmf` primitive (Chaikin Money Flow over N
   bars) lands on branch feat/cmf-primitive. Reads high, low, close,
   volume per bar; bounded in [-1, 1]; oracle-tested.

2. Discount zone — DONE, no new primitive. The premium/discount axis
   is the position of the close inside the recent dealing range:
   position = (close - min low) / (max high - min low) over a
   lookback. It composes from existing primitives, so the primitive
   algebra stays sufficient:

   ```
   lo       = rolling_min(input=low,  period=N)
   hi       = rolling_max(input=high, period=N)
   num      = distance(a=close, b=lo, denominator=close)   # (close-lo)/close
   den      = distance(a=hi,    b=lo, denominator=close)   # (hi-lo)/close
   position = ratio(a=num, b=den)                          # close cancels
   ```

   Below 0.5 is discount, above is premium. Verified by
   test_discount_zone_composition. (A residual ~1e-28 rounding from the
   double division is deterministic and far below any real threshold.)
   The lookback N stands in for "recent swing range"; a
   confirmed-pivot version is a possible later refinement.

3. Blue order block — DONE (Option A, owner's choice 2026-09-29). The
   `order_block` condition (candle + displacement) is a streaming state
   machine: a down bar is a candidate; if within push_window later bars
   price rises at least displacement times the down bar's range above
   its high, the down bar's high-low band becomes an active order
   block; the condition fires on a later bar whose close re-enters a
   band it was outside of, and a band is dropped once a close falls
   below its low. Reads open/high/low/close; deterministic and causal;
   oracle-tested (fires exactly on re-entry, expires without
   displacement, rejects bad params) and integration-tested (one long
   event on the re-entry bar). Params: push_window (bars for the push),
   displacement (factor of the down bar's range). Full high-low band in
   this version; body-only band and a bearish mirror are possible
   refinements.

## Order block — mechanical options (owner picked A, 2026-09-29)

A bullish (blue) order block, in plain terms, is the down-candle just
before a strong push up; its price band later acts as support, and the
strategy waits for price to re-enter that band.

- Option A — candle + displacement: mark the last down bar (close <
  open) that is immediately followed, within M bars, by an up move
  whose size exceeds the down bar's range by a factor D. The down
  bar's high-low band is the order block. Re-entry = a later close
  back inside that band. Knobs: M (push window), D (displacement
  factor).

- Option B — swing pivot: use the last confirmed swing low
  (confirmed_extrema) as the order-block reference level, and treat a
  return to within a tolerance band around it as re-entry. Simpler,
  fewer knobs (confirm_bars, band width), but coarser than a candle
  band.

- Option C — displacement by body and volume: mark a bar whose body
  and volume both exceed a multiple of their recent medians as the
  institutional footprint, and take the prior opposite-color bar as
  the order block. Knobs: body multiple, volume multiple, lookback for
  the medians.

Recommendation: Option A is closest to the source's picture and fully
expressible from existing primitives plus a small pattern node; Option
B is the fastest to stand up if we want a first end-to-end verdict
sooner. Either way, the band and re-entry must be observable and
reproducible — no discretion.

## After the pillars

All three pillars now stand: CMF (primitive), discount (composed), and
the order block (condition). The next step is to assemble one graph_v1
definition — long when the order block re-enters AND the close is in
the discount half AND CMF > 0 — screen it at the front door (claim),
register it on disjoint exploration and confirmation windows, measure,
and adjudicate. The verdict, CONFIRMED or NOT_CONFIRMED against a coin,
is the whole point. Windows and parameters (lookback N, push_window,
displacement, CMF period) are registration choices to seal before the
data, not knobs to tune afterward.

Research diagnostics only. No advice. No order paths.
