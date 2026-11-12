# Strategy Verifier

**Does a trading rule have real edge — or is it a coin flip?**

Strategy Verifier takes any mechanical trading rule ("*when X, go
long/short*"), seals it before looking at the data, measures it on
historical market data, and judges it against a coin with a confidence
interval. It is built to say **"no"** honestly — because most rules,
especially the ones that are sold, are patterns found in noise.

```
claim → triage → register (seal) → explore → freeze → confirm → verdict → certificate
```

- **Any rule is a file.** A composable engine (`graph_v1`) turns rules into TOML: indicators, conditions, a direction rule, an episode gate, and outcome bands. No new code per strategy.
- **Pre-registration, enforced.** The rule, markets, and disjoint exploration/confirmation windows are sealed (SHA-256) before measurement. Registrations are immutable; a new look needs a new name.
- **Judged against a coin.** Outcomes are *favorable* (target band hit first) vs *adverse*; the favorable share gets a Wilson 95% interval against 50%.
- **The instrument is itself verified.** A calibration harness plants a known edge (reads **92%**) and feeds pure noise (reads **~50%**) through the real pipeline.
- **Read-only by construction.** A built-in safety review fails the build on order, broker, key, or network-client vocabulary.

## 한국어 요약

**매매 규칙에 진짜 우위가 있는지, 아니면 동전 던지기인지** 판정하는
검증 시스템입니다. 규칙을 데이터를 보기 전에 봉인(사전등록)하고, 과거
시세로 측정한 뒤, 동전(50%) 대비 신뢰구간으로 판정합니다. 판정 계기
자체도 "답을 아는 데이터"로 검증했습니다(심은 우위 → 92%, 노이즈 →
약 50%). 실제로 유튜브에서 판매되던 전략(수익률 200% 주장)을 세 번의
사전등록 검증에 걸었고, 결론은 **우위 없음(NOT_CONFIRMED)** 이었습니다
→ [사례 연구](docs/CASE_STUDY_SMC_CMF.md).

## Why this exists

A backtest on a self-chosen window proves almost nothing. Tune a rule on
one chart, report the best number, and noise looks like skill. The
verifier forces the scientific method onto trading ideas:

1. **Say exactly what the rule is** — vague words ("when it feels
   strong") are refused by a triage step with a reason and a fix.
2. **Seal it before looking** — rule, markets, and windows are frozen.
3. **Explore on one window, confirm on another** — the confirmation
   window stays unopened until the rule earns it (an edge *and* a
   sample of at least 100).
4. **Judge against a coin** — not against hope.

## Case study: a paid strategy, verified

A YouTube "Smart Money Concepts + Chaikin Money Flow" strategy claimed a
200% return. Rebuilt mechanically and run through three sealed looks:

| Look | Sample | Favorable | Wilson 95% |
|---|---:|---:|---|
| BTC, 2-hour | 147 | 42.9% | [35.1, 50.9] |
| BTC, daily | 61 | 59.0% | [46.5, 70.5] |
| BTC + ETH + SOL, daily (final, pre-committed) | **155** | **53.5%** | **[45.7, 61.2]** |

**NOT_CONFIRMED.** The tempting 59% did not replicate once the same
sealed rule met more markets (ETH 54%, SOL 43%). The confirmation window
was never opened. Full write-up: [docs/CASE_STUDY_SMC_CMF.md](docs/CASE_STUDY_SMC_CMF.md).

The sealed definition hashes in the [registration records](docs/SMC_CMF_DISCOUNT_OB_1D_POOL_REGISTRATION.md)
match the files in [`definitions/`](definitions) — check with `sha256sum`.

## Is the instrument honest? Calibration

| Input (known answer) | Favorable share | Wilson 95% |
|---|---:|---|
| Planted upward edge | 92.0% | [86.5, 95.4] — excludes 50% |
| Pure random walk, 5 seeds | 44.8 – 54.7% | every interval covers 50% |
| The SMC definition on random walks, 3 seeds | 43.7 – 53.8% | every interval covers 50% |

The engine separates real edge from noise by ~41 points and does not
invent edge from noise — so a "no" is a measurement, not a malfunction.
Reproduce: `python scripts/calibrate.py`. Pinned by
`tests/test_verifier_calibration.py`. Details and the audit that came
with it: [docs/VERIFIER_CALIBRATION.md](docs/VERIFIER_CALIBRATION.md).

## A rule is a file

The SMC rule above, in full (excerpt):

```toml
engine = "graph_v1"

[frame]
bar_seconds = 86400                      # daily bars

[[series]]                               # Chaikin Money Flow, 20 bars
name = "cmf"
primitive = "cmf"
period = 20

[[series]]                               # where the close sits in the 50-bar range
name = "position"
primitive = "ratio"
a = "num"
b = "den"

[[conditions]]                           # bullish order block re-entry
name = "ob_reentry"
type = "order_block"
push_window = 6
displacement = "1.0"

[direction]
long_when = ["ob_reentry", "cmf_bull", "discount"]

[outcomes]                               # +/-5% band within 30 days
horizons = [120]
threshold_pcts = ["0.05"]
```

Vocabulary: series (`sma`, `smma`, rolling max/min/median, `distance`,
`ratio`, `cmf`, returns, lags, confirmed extrema), conditions
(`compare`, `crosses`, runs, `within`, `beyond_by`, `order_block`,
head-and-shoulders pattern node), direction rules, episode gates,
stride controls, and split axes. Spec: [docs/INTERPRETER_V1_SPEC.md](docs/INTERPRETER_V1_SPEC.md).

## Quick start

Requires Python 3.11+. No third-party runtime dependencies.

```bash
pip install -e ".[dev]"

verifier init --dir ./workspace && cd workspace
verifier verify-triage --claim claims/example_squeeze_claim.toml
verifier verify-run --definition definitions/demo_quickstart.toml \
  --input data/captures/demo-900s-1.jsonl --input-session-mode single
verifier ui            # local web workspace
```

Real data: download Binance USD-M kline zips from data.binance.vision
and convert them with `verifier convert-binance-um-klines --bar-path ohlc`.
The full walkthrough (Korean) is in [docs/USER_GUIDE.md](docs/USER_GUIDE.md).

| Command | Purpose |
|---|---|
| `verify-triage` | Refuse vague claims with a reason and a fix |
| `verify-register` | Seal rule, markets, and windows before measurement |
| `verify-run` / `verify-read` | Measure; list individual occurrences |
| `verify-advance` / `verify-status` | Move a family through its lifecycle |
| `verify-verdict` / `verify-certificate` | Render the verdict and a certificate |
| `verify-attest` / `verify-diagnose` | Bind result to inputs by content hash; show where a rule is strong and where it breaks |
| `ui` / `serve` / `record` | Web workspace, single-user hosting, live recording |
| `safety-review` | Fail on order/key/network-client vocabulary |

## Deploy

A Dockerfile runs the web workspace with HTTP Basic Auth and a
persistent volume. Guide: [docs/DEPLOY_RAILWAY.md](docs/DEPLOY_RAILWAY.md).
Secrets (the web password, an optional LLM key for the chat tier) are
read from environment variables only — never committed.

## Project layout

```
src/strategy_verifier/
  verifier/            engine: primitives, conditions, graph_v1, registration,
                       lifecycle, verdicts, certificates, attestation, chat
  public_market_data/  capture reader, integrity gate, session grouping,
                       Binance kline/aggTrade converters
  ui/                  web workspace (server-sent events, live reader)
  safety/              read-only safety review
definitions/           rule files (graph_v1) and sealed claims
docs/                  spec, user guide, calibration, case study, registrations
scripts/calibrate.py   reproduces the calibration table
tests/                 141 tests
```

## Engineering notes

- **Session isolation fix.** An audit found that pooling several
  markets in one run let one market's outcome window and indicator
  warm-up leak into the next. Runs are now split into per-market
  sessions and summed; an invariant test proves pooled = sum of
  per-market runs.
- **An audit claim, corrected.** The same audit first reported the
  integrity gate as inactive for `verify-run`; it was in fact applied
  by a shared input-preparation step the audit had missed. The record
  in [VERIFIER_CALIBRATION.md](docs/VERIFIER_CALIBRATION.md) says so,
  and tests now pin the gate's behavior.

## Boundaries

Research diagnostics only. No orders, no exchange keys, no account
access, no live trading, no investment advice. Outputs are past-tense
statistical statements under a pre-registered bar. See
[docs/DISCLAIMER.md](docs/DISCLAIMER.md).

## How it was built

Designed and directed by the author; implemented with AI coding agents
(Claude, Codex). Every claim in this README is reproducible from the
repository: the tests, the calibration script, and the sealed hashes.

## License

All rights reserved — see [LICENSE](LICENSE). Source is public for
review; reuse requires permission.
