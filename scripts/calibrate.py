"""Reproduce the calibration numbers quoted in README and docs.

Feeds synthetic markets with known answers through the real conversion
and measurement path, and prints the favorable share with a Wilson 95%
interval for each:

  positive control  planted upward drift          -> expect well above 50%
  negative control  driftless random walk, 5 seeds -> expect ~50%
  SMC on noise      the pooled SMC definition on random walks -> expect ~50%

Usage (from the repository root, after `pip install -e .`):

    python scripts/calibrate.py

Research diagnostics only.
"""

from __future__ import annotations

import csv
import math
import random
import re
import tempfile
from pathlib import Path

from strategy_verifier.public_market_data.binance_um_daily_klines import (
    BinanceUmKlinesConfig,
    run_binance_um_klines_conversion,
)
from strategy_verifier.verifier.definition import (
    load_verifier_definition,
    run_verifier_definition,
)

DAY_MS = 86_400_000
START_MS = 1_600_000_000_000

PROBE = """
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


def write_klines(path: Path, n: int, mu: float, seed: int, vary_volume: bool) -> None:
    rng = random.Random(seed)
    price, open_ms, rows = 100.0, START_MS, []
    for _ in range(n):
        o = price
        c = o * math.exp(mu + rng.gauss(0.0, 0.02))
        hi = max(o, c) * (1 + abs(rng.gauss(0.0, 0.003)))
        lo = min(o, c) * (1 - abs(rng.gauss(0.0, 0.003)))
        vol = f"{rng.uniform(500, 1500):.2f}" if vary_volume else "1000"
        rows.append([open_ms, f"{o:.4f}", f"{hi:.4f}", f"{lo:.4f}", f"{c:.4f}",
                     vol, open_ms + DAY_MS - 1, "0", 0, "0", "0", "0"])
        price, open_ms = c, open_ms + DAY_MS
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["open_time", "open", "high", "low", "close", "volume",
                         "close_time", "quote_volume", "count", "taker_buy_volume",
                         "taker_buy_quote_volume", "ignore"])
        writer.writerows(rows)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    if n == 0:
        return 0.0, 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, c - h, c + h


def measure(definition_path: Path, workdir: Path, name: str, n: int, mu: float,
            seed: int, vary_volume: bool = False) -> str:
    csv_path, jsonl_path = workdir / f"{name}.csv", workdir / f"{name}.jsonl"
    write_klines(csv_path, n, mu, seed, vary_volume)
    run_binance_um_klines_conversion(BinanceUmKlinesConfig(
        input_path=csv_path, output_path=jsonl_path, symbol="BTCUSDT",
        overwrite=True, bar_path="ohlc"))
    definition = load_verifier_definition(definition_path)
    report = run_verifier_definition(definition, input_paths=[jsonl_path],
                                     input_session_mode="single")
    horizon, threshold = definition.horizons[0], str(definition.threshold_pcts[0])
    m = re.search(rf"- horizon={horizon} [^\n]*?{re.escape(threshold)}: (\d+)/(\d+)/(\d+)",
                  report.split("base_grid")[1])
    fav, adv = int(m.group(1)), int(m.group(2))
    p, lo, hi = wilson(fav, fav + adv)
    covers = "covers 50%" if lo <= 0.5 <= hi else "excludes 50%"
    return f"{fav}/{fav + adv} = {p * 100:5.1f}%  Wilson95 [{lo * 100:4.1f}, {hi * 100:4.1f}]  {covers}"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        probe = work / "calib_probe.toml"
        probe.write_text(PROBE, encoding="utf-8")
        print("positive control (planted drift):")
        print("  seed 12345:", measure(probe, work, "pos", 6000, 0.010, 12345))
        print("negative control (driftless random walk):")
        for seed in (11, 22, 33, 44, 55):
            print(f"  seed {seed}:", measure(probe, work, f"neg{seed}", 6000, 0.0, seed))
        smc = root / "definitions" / "smc_cmf_discount_ob_1d_pool.toml"
        print("SMC pooled definition on random walks:")
        for seed in (101, 202, 303):
            print(f"  seed {seed}:", measure(smc, work, f"smc{seed}", 8000, 0.0, seed, True))


if __name__ == "__main__":
    main()
