"""Calibration harness: does the graph verifier read true?

An instrument that judges edge is only trustworthy if it passes known
weights. These tests feed the *real* conversion + graph measurement
path two synthetic markets with analytically known answers:

- a driftless random walk (no edge): the favorable share of a symmetric
  band must land near a coin, and its Wilson interval must cover 0.5;
- the same process with a strong upward drift (a planted edge): the
  favorable share must sit well above a coin, with its Wilson interval
  entirely above 0.5.

If the engine were stuck emitting a constant (say 0.5), the negative
control would pass but the positive would fail; if it manufactured edge
from noise, the negative would fail. Passing both proves the pipeline
*discriminates* real edge from noise, which is what makes any single
verdict (including a NOT_CONFIRMED) worth believing.

Deterministic: fixed seeds, generous bands, no network. Research
diagnostics only.
"""

from __future__ import annotations

import csv
import math
import random
import re
from io import StringIO
from pathlib import Path

from strategy_verifier.public_market_data.binance_um_daily_klines import (
    BinanceUmKlinesConfig,
    run_binance_um_klines_conversion,
)
import strategy_verifier.cli as cli
from strategy_verifier.verifier.definition import (
    load_verifier_definition,
    run_verifier_definition,
)
from strategy_verifier.verifier.graph import (
    render_graph_report,
    run_graph,
    run_graph_grouped,
)

_DAY_MS = 86400000
_START_MS = 1_600_000_000_000

# A minimal graph_v1 probe: every bar is a long-directional bar (high is
# always >= low), the episode gate spaces entries 40 bars apart so their
# 30-bar (120-event) outcome windows do not overlap, and the outcome is a
# symmetric +/-5% band. The probe isolates the shared measurement engine
# from any one strategy's condition logic.
_PROBE_DEF = """
family = "calib_probe"
engine = "graph_v1"

[frame]
bar_seconds = 86400

[[conditions]]
name = "always"
type = "compare"
left = "high"
op = ">="
right = "low"

[direction]
long_when = ["high >= low"]
short_when = ["low > high"]

[event]
when = ["always"]
gate_bars = 40

[controls]
stride = 500
offset = 7
min_distance_bars = 35

[outcomes]
horizons = [120]
threshold_pcts = ["0.05"]
"""


def _write_klines_csv(
    path: Path, n: int, mu: float, sigma: float, seed: int, intrabar: float = 0.003
) -> None:
    rng = random.Random(seed)
    price = 100.0
    open_ms = _START_MS
    rows = []
    for _ in range(n):
        open_price = price
        log_return = mu + rng.gauss(0.0, sigma)
        close_price = open_price * math.exp(log_return)
        high = max(open_price, close_price) * (1 + abs(rng.gauss(0.0, intrabar)))
        low = min(open_price, close_price) * (1 - abs(rng.gauss(0.0, intrabar)))
        rows.append([
            open_ms,
            f"{open_price:.4f}",
            f"{high:.4f}",
            f"{low:.4f}",
            f"{close_price:.4f}",
            "1000",
            open_ms + _DAY_MS - 1,
            "0", 0, "0", "0", "0",
        ])
        price = close_price
        open_ms += _DAY_MS
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "count", "taker_buy_volume",
            "taker_buy_quote_volume", "ignore",
        ])
        writer.writerows(rows)


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    if n == 0:
        return 0.0, 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, center - half, center + half


def _measure(def_path: Path, jsonl_path: Path) -> tuple[int, int, int]:
    definition = load_verifier_definition(def_path)
    report = run_verifier_definition(
        definition,
        input_paths=[jsonl_path],
        input_session_mode="single",
        calibrate_only=False,
    )
    horizon = definition.horizons[0]
    threshold = str(definition.threshold_pcts[0])
    base_grid = report.split("base_grid")[1]
    match = re.search(
        rf"- horizon={horizon} [^\n]*?{re.escape(threshold)}: (\d+)/(\d+)/(\d+)",
        base_grid,
    )
    assert match is not None, f"could not parse base grid from:\n{report}"
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _prepare(tmp_path: Path, name: str, mu: float, seed: int) -> tuple[int, int, int]:
    csv_path = tmp_path / f"{name}.csv"
    jsonl_path = tmp_path / f"{name}.jsonl"
    _write_klines_csv(csv_path, n=5000, mu=mu, sigma=0.02, seed=seed)
    result = run_binance_um_klines_conversion(
        BinanceUmKlinesConfig(
            input_path=csv_path,
            output_path=jsonl_path,
            symbol="BTCUSDT",
            overwrite=True,
            bar_path="ohlc",
        )
    )
    assert result.malformed_count == 0
    assert result.out_of_order_count == 0
    return _measure(_probe_path(tmp_path), jsonl_path)


def _probe_path(tmp_path: Path) -> Path:
    path = tmp_path / "calib_probe.toml"
    if not path.exists():
        path.write_text(_PROBE_DEF)
    return path


def test_graph_verifier_discriminates_edge_from_noise(tmp_path):
    # Negative control: a driftless random walk carries no edge.
    neg_fav, neg_adv, _neg_unt = _prepare(tmp_path, "neg", mu=0.0, seed=12345)
    neg_res = neg_fav + neg_adv
    neg_share, neg_lo, neg_hi = _wilson(neg_fav, neg_res)

    # Positive control: the same process with a planted upward drift.
    pos_fav, pos_adv, _pos_unt = _prepare(tmp_path, "pos", mu=0.010, seed=12345)
    pos_res = pos_fav + pos_adv
    pos_share, pos_lo, pos_hi = _wilson(pos_fav, pos_res)

    # Enough resolved occurrences on each side for the shares to be meaningful.
    assert neg_res >= 80
    assert pos_res >= 80

    # Noise reads like a coin: near 0.5 and its interval straddles 0.5.
    assert 0.40 <= neg_share <= 0.60
    assert neg_lo <= 0.5 <= neg_hi

    # A planted edge reads high: well above a coin, interval entirely above.
    assert pos_share >= 0.75
    assert pos_lo > 0.5

    # And the instrument separates the two cases by a wide margin.
    assert pos_share - neg_share >= 0.20


def _write_flat_klines_csv(
    path: Path, n: int, level: float, seed: int
) -> None:
    """A flat market at `level` (no bar ever moves +/-5% on its own)."""
    rng = random.Random(seed)
    open_ms = _START_MS
    rows = []
    for _ in range(n):
        open_price = level * (1 + rng.gauss(0.0, 0.0005))
        close_price = level * (1 + rng.gauss(0.0, 0.0005))
        high = max(open_price, close_price) * (1 + abs(rng.gauss(0.0, 0.0003)))
        low = min(open_price, close_price) * (1 - abs(rng.gauss(0.0, 0.0003)))
        rows.append([
            open_ms, f"{open_price:.4f}", f"{high:.4f}", f"{low:.4f}",
            f"{close_price:.4f}", "1000", open_ms + _DAY_MS - 1,
            "0", 0, "0", "0", "0",
        ])
        open_ms += _DAY_MS
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "count", "taker_buy_volume",
            "taker_buy_quote_volume", "ignore",
        ])
        writer.writerows(rows)


def _flat_market_jsonl(tmp_path: Path, name: str, n: int, level: float, seed: int) -> Path:
    csv_path = tmp_path / f"{name}.csv"
    jsonl_path = tmp_path / f"{name}.jsonl"
    _write_flat_klines_csv(csv_path, n=n, level=level, seed=seed)
    run_binance_um_klines_conversion(
        BinanceUmKlinesConfig(
            input_path=csv_path, output_path=jsonl_path,
            symbol="X", overwrite=True, bar_path="ohlc",
        )
    )
    return jsonl_path


def _cell(result) -> tuple[int, int, int]:
    report = render_graph_report(result)
    base_grid = report.split("base_grid")[1]
    match = re.search(r"- horizon=120 [^\n]*?0\.05: (\d+)/(\d+)/(\d+)", base_grid)
    assert match is not None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def test_session_isolation_pools_markets_without_boundary_bleed(tmp_path):
    definition = load_verifier_definition(_probe_path(tmp_path))
    # Market A is flat at 100; market B is flat at 110 (+10%). A is 570
    # bars, so its last gate-opened entry sits ~10 bars from A's end --
    # its 30-bar outcome window reaches past the boundary. Filenames are
    # shaped so prefix mode puts each market in its own session.
    market_a = _flat_market_jsonl(tmp_path, "mktA-1d-2020-01", n=570, level=100.0, seed=1)
    market_b = _flat_market_jsonl(tmp_path, "mktB-1d-2020-01", n=600, level=110.0, seed=2)

    solo_a = _cell(run_graph(definition, input_paths=[market_a], input_session_mode="single", calibrate_only=False))
    solo_b = _cell(run_graph(definition, input_paths=[market_b], input_session_mode="single", calibrate_only=False))
    pooled = _cell(run_graph_grouped(definition, input_paths=[market_a, market_b], input_session_mode="prefix", calibrate_only=False))
    continuous = _cell(run_graph(definition, input_paths=[market_a, market_b], input_session_mode="single", calibrate_only=False))

    # Isolation invariant: pooling per market equals summing independent
    # per-market runs, cell by cell -- no outcome crossed a boundary.
    assert pooled == tuple(solo_a[i] + solo_b[i] for i in range(3))
    # Both flat markets on their own resolve nothing (all untouched).
    assert solo_a[0] == 0 and solo_b[0] == 0

    # The old continuous feed leaks: A's boundary entry sees B's +10% and
    # is scored favorable. The session-isolated path does not. So the
    # session flag is no longer inert -- it changes the measurement.
    assert continuous[0] > pooled[0]
    assert pooled[0] == 0


def test_verify_run_integrity_gate_excludes_dirty_rows(tmp_path):
    definition_path = _probe_path(tmp_path)
    clean = _flat_market_jsonl(tmp_path, "clean-1d-2020-01", n=200, level=100.0, seed=7)
    # A dirty copy: duplicated rows and one row pulled far out of order.
    lines = clean.read_text().splitlines()
    dirty_lines = lines[:60] + [lines[59], lines[59]] + lines[60:120] + [lines[10]] + lines[120:]
    dirty = tmp_path / "dirty-1d-2020-01.jsonl"
    dirty.write_text("\n".join(dirty_lines) + "\n")

    def _run(args) -> tuple[int, str]:
        out, err = StringIO(), StringIO()
        code = cli.main(args, stdout=out, stderr=err)
        return code, out.getvalue()

    # Fail closed: dirty capture, require-safe, without gating -> exit 1.
    code, _ = _run([
        "verify-run", "--definition", str(definition_path),
        "--input", str(dirty), "--input-session-mode", "single",
        "--require-integrity-safe",
    ])
    assert code == 1

    # Gated: the dirty capture is cleaned and the run succeeds.
    code_gated, out_gated = _run([
        "verify-run", "--definition", str(definition_path),
        "--input", str(dirty), "--input-session-mode", "single",
        "--integrity-gated",
    ])
    assert code_gated == 0

    # And the gate actually changed what the engine saw: a gated run on
    # the dirty file matches a raw run on the clean file, bar for bar.
    code_clean, out_clean = _run([
        "verify-run", "--definition", str(definition_path),
        "--input", str(clean), "--input-session-mode", "single",
    ])
    assert code_clean == 0

    def _bars(text: str) -> str:
        match = re.search(r"bars: total=(\d+) directional=(\d+)", text)
        assert match is not None
        return match.group(0)

    assert _bars(out_gated) == _bars(out_clean)
