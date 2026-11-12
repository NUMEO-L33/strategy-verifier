# Interpreter V1 Specification (Any Codifiable Technique)

Date: 2026-07-30

## The Requirement, Stated Honestly

Users must be able to submit any technique. Fourteen families teach
the true boundary: any technique expressible as when-plus-direction
is verifiable; anything that refuses that form is not a technique
but a feeling, and the product must say so kindly and specifically.
Rejection with reasons is therefore a first-class feature: the triage
this project performed by hand on four paid sources becomes the
product's front door.

## The Primitive Algebra (Derived, Not Imagined)

Every one of the fourteen families decomposes into this set, which
is the sufficiency proof for the known space:

- Frames: raw ticks; time bars of any seconds; daily bars.
- Series: sma, smma, rolling max, min, and median, gap and distance
  (absolute or normalized), returns and mean absolute return,
  volume and volume ratios, Chaikin money flow, cumulative delta,
  confirmed local extrema with alternation.
- Conditions: comparisons, crosses, signed runs of length N,
  within-band and beyond-by-buffer, candle-plus-displacement
  order-block re-entry, lagged references, pattern
  templates over extrema sequences with balance and depth
  constraints.
- Events: an ordered condition list, a direction rule, an episode
  gate, and validity windows.
- Controls: deterministic strides with exclusions, complementary
  arms, or condition-matched samples.
- Splits: categorical joins from a supplied capture (regime files)
  and covariate percentile splits calibrated per market, per the
  recorded lesson.
- Calibration: any threshold may be declared covariate-only and is
  resolved in an outcome-free pre-pass.
- Outcomes and bars: the race sweep, a primary cell, Wilson
  exclusion or separation, market direction counts, freeze limits,
  and the two-stage windows.

## Acceptance (The Strangler Cut)

Status: done. This repository ships the generic `graph_v1` engine only;
the per-family dispatch engines were retired.


V1 is done when the three pilot definitions are re-expressed in pure
primitives, the engine dispatch path is deleted for them, and byte
parity with the archived reports holds a second time through the
generic interpreter. From that day, a user definition and a pilot
definition ride identical code.

## Extension And Refusal

A plugin seam admits new series primitives under the same oracle
discipline (an independent recomputation test per primitive). The
loader rejects, with named reasons, definitions that omit direction,
reference unavailable data, or hide discretion in prose; the error
text teaches the user how to make the claim measurable, which is the
product's honest pedagogy.

## Boundaries

Research diagnostics only: no orders, no keys, no advice.
