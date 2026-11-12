"""Tests for the condition-graph composer.

The demo family runs on an engineered two-sided trend tape and must
match an independent oracle that recomputes averages, direction,
touch events, gating, stride controls, and every race cell from the
same converted stream; run-node before and after semantics are pinned
directly; schema violations are rejected with names; the calibrate
ladder follows the nearest-rank rule; and the CLI rides verify-run.
"""

import random
from decimal import Decimal
from pathlib import Path

import pytest

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.public_market_data import (
    BinanceUmAggTradeHistoryConfig,
    run_binance_um_aggtrade_history_conversion,
)
from strategy_verifier.public_market_data.public_trade_capture_reader import (
    PublicTradeCaptureReadConfig,
    PublicTradeCaptureStreamCounters,
    iter_public_trade_capture_events,
)
from strategy_verifier.verifier import (
    load_graph_definition,
    load_verifier_definition,
    run_graph,
)

_DEMO = Path("definitions/ma_touch_trend.toml")


def _small_raw(**overrides) -> dict:
    raw = {
        "family": "small",
        "engine": "graph_v1",
        "frame": {"bar_seconds": 60},
        "params": {"delta": "0.0006"},
        "series": [
            {"name": "fast", "primitive": "sma", "input": "close",
             "period": 3},
            {"name": "slow", "primitive": "sma", "input": "close",
             "period": 10},
            {"name": "slow_prior", "primitive": "lag", "input": "slow",
             "k": 3},
            {"name": "touch_gap", "primitive": "distance", "a": "close",
             "b": "fast", "denominator": "close"},
        ],
        "conditions": [
            {"name": "near_fast", "type": "compare", "left": "touch_gap",
             "op": "<=", "right": "delta"},
        ],
        "direction": {
            "long_when": ["close > slow", "slow > slow_prior"],
            "short_when": ["close < slow", "slow < slow_prior"],
        },
        "event": {"when": ["near_fast"], "gate_bars": 8},
        "controls": {"stride": 5, "offset": 2, "min_distance_bars": 2},
        "outcomes": {
            "horizons": [300, 1500],
            "threshold_pcts": ["0.004", "0.01"],
        },
        "calibrate": {"series": "touch_gap"},
    }
    raw.update(overrides)
    return raw


@pytest.fixture(scope="module")
def synthetic_capture(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("graph")
    random.seed(71)
    lines = [
        "agg_trade_id,price,quantity,first_trade_id,last_trade_id,"
        "transact_time,is_buyer_maker"
    ]
    price = 50000.0
    index = 0
    phases = [(120, 3.0), (40, -0.2), (120, -3.0), (40, 0.2)] * 3
    for bars, drift in phases:
        for _ in range(bars):
            for _ in range(20):
                index += 1
                price += random.uniform(-2.0, 2.0) + drift / 20
                lines.append(
                    f"{index},{price:.1f},0.5,{index * 2},{index * 2 + 1},"
                    f"{1750000000000 + index * 3000},false"
                )
    csv_path = base / "graph.csv"
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    capture = base / "graph-100-1.jsonl"
    run_binance_um_aggtrade_history_conversion(
        BinanceUmAggTradeHistoryConfig(
            input_path=csv_path, output_path=capture,
            symbol="BTCUSDT", overwrite=True,
        )
    )
    return capture


def _oracle(path: Path, raw: dict):
    counters = PublicTradeCaptureStreamCounters()
    ticks = [
        stream_event.mapped_trade_tick
        for stream_event in iter_public_trade_capture_events(
            PublicTradeCaptureReadConfig(input_path=path), counters
        )
        if stream_event.mapped_trade_tick is not None
    ]
    bar_ms = raw["frame"]["bar_seconds"] * 1000
    delta = Decimal(raw["params"]["delta"])
    fast_p, slow_p, lag_k = 3, 10, 3
    gate_bars = raw["event"]["gate_bars"]
    stride = raw["controls"]["stride"]
    offset = raw["controls"]["offset"]
    min_distance = raw["controls"]["min_distance_bars"]

    closes: list[Decimal] = []
    slow_history: list[Decimal] = []
    bar_count = 0
    directional = 0
    since_event = 10**9
    last_event_bar = -(10**9)
    events = []
    controls = []
    current_bar = None
    bar_close = None

    def finalize(raw_at):
        nonlocal bar_count, directional, since_event, last_event_bar
        if bar_close is None:
            return
        bar_count += 1
        closes.append(bar_close)
        fast = (
            sum(closes[-fast_p:]) / Decimal(fast_p)
            if len(closes) >= fast_p else None
        )
        slow = (
            sum(closes[-slow_p:]) / Decimal(slow_p)
            if len(closes) >= slow_p else None
        )
        if slow is not None:
            slow_history.append(slow)
        prior = (
            slow_history[-(lag_k + 1)]
            if len(slow_history) >= lag_k + 1 else None
        )
        touch = (
            abs(bar_close - fast) / bar_close if fast is not None else None
        )
        if slow is not None and prior is not None:
            if bar_close > slow and slow > prior:
                direction = Decimal("1")
            elif bar_close < slow and slow < prior:
                direction = Decimal("-1")
            else:
                direction = Decimal("0")
        else:
            direction = Decimal("0")
        since_event += 1
        if direction == 0:
            return
        directional += 1
        near = touch is not None and touch <= delta
        if near and since_event >= gate_bars:
            events.append((raw_at, bar_close, direction))
            since_event = 0
            last_event_bar = bar_count
        elif directional % stride == offset:
            if bar_count - last_event_bar >= min_distance:
                controls.append((raw_at, bar_close, direction))

    for raw_index, tick in enumerate(ticks, start=1):
        bar_id = int(tick.timestamp.timestamp() * 1000) // bar_ms
        if bar_id != current_bar:
            if current_bar is not None:
                finalize(raw_index)
            current_bar = bar_id
            bar_close = None
        bar_close = tick.price
    finalize(len(ticks) + 1)

    def resolve(items):
        grid = {
            (h, Decimal(t)): [0, 0, 0]
            for h in raw["outcomes"]["horizons"]
            for t in raw["outcomes"]["threshold_pcts"]
        }
        for start_raw, entry, direction in items:
            for h in raw["outcomes"]["horizons"]:
                for t in raw["outcomes"]["threshold_pcts"]:
                    threshold = Decimal(t)
                    outcome = 2
                    for later in range(start_raw - 1, len(ticks)):
                        raw_at = later + 1
                        if raw_at < start_raw:
                            continue
                        move = (
                            ticks[later].price / entry - Decimal("1")
                        ) * direction
                        if move >= threshold:
                            outcome = 0
                            break
                        if move <= -threshold:
                            outcome = 1
                            break
                        if raw_at - start_raw >= h:
                            break
                    grid[(h, threshold)][outcome] += 1
        return grid

    return events, controls, resolve(events), resolve(controls)


def test_engine_matches_independent_oracle(synthetic_capture):
    raw = _small_raw()
    definition = load_graph_definition(raw, Path("inline"))
    result = run_graph(
        definition, [synthetic_capture], "single", calibrate_only=False
    )
    events, controls, event_grid, control_grid = _oracle(
        synthetic_capture, raw
    )
    assert result.event_count == len(events) > 0
    assert result.control_count == len(controls) > 0
    assert result.long_event_count == sum(
        1 for e in events if e[2] > 0
    )
    for cell, tally in result.base_grid.items():
        assert [tally.favorable, tally.adverse, tally.untouched] == (
            event_grid[cell]
        ), cell
    for cell, tally in result.control_grid.items():
        assert [tally.favorable, tally.adverse, tally.untouched] == (
            control_grid[cell]
        ), cell
    for cell, base in result.base_grid.items():
        merged = [
            result.side_grids["long"][cell].favorable
            + result.side_grids["short"][cell].favorable,
            result.side_grids["long"][cell].adverse
            + result.side_grids["short"][cell].adverse,
            result.side_grids["long"][cell].untouched
            + result.side_grids["short"][cell].untouched,
        ]
        assert merged == [base.favorable, base.adverse, base.untouched]


def test_run_node_before_and_after_semantics():
    base = _small_raw()
    base["conditions"].append(
        {"name": "standing", "type": "run", "on": "near_fast",
         "minimum": 2, "evaluate": "after"}
    )
    load_graph_definition(base, Path("inline"))  # 스키마 통과
    from strategy_verifier.verifier.conditions import create_condition

    run = create_condition("true_run", minimum=2)
    after = []
    for flag in (True, True, False, True, True):
        run.update(flag)
        after.append(run.met())
    assert after == [False, True, False, False, True]
    run2 = create_condition("true_run", minimum=2)
    before = []
    for flag in (True, True, False, True, True):
        before.append(run2.met())
        run2.update(flag)
    assert before == [False, False, True, False, False]


def test_schema_rejections():
    with pytest.raises(DomainValidationError):
        load_graph_definition(
            _small_raw(series=[{"name": "x", "primitive": "sma",
                                "input": "ghost", "period": 3}]),
            Path("inline"),
        )
    bad = _small_raw()
    bad["conditions"][0]["op"] = "=="
    with pytest.raises(DomainValidationError):
        load_graph_definition(bad, Path("inline"))
    bad2 = _small_raw()
    bad2["controls"]["offset"] = 9
    with pytest.raises(DomainValidationError):
        load_graph_definition(bad2, Path("inline"))
    bad3 = _small_raw()
    bad3["event"]["when"] = ["ghost"]
    with pytest.raises(DomainValidationError):
        load_graph_definition(bad3, Path("inline"))
    bad4 = _small_raw()
    bad4["direction"]["long_when"] = ["close >> slow"]
    with pytest.raises(DomainValidationError):
        load_graph_definition(bad4, Path("inline"))


def test_calibrate_ladder(synthetic_capture):
    definition = load_graph_definition(_small_raw(), Path("inline"))
    result = run_graph(
        definition, [synthetic_capture], "single", calibrate_only=True
    )
    assert result.event_count == 0
    assert result.calibrate_value_count == result.directional_bar_count
    values = [value for _, value in result.calibrate_percentiles]
    assert len(values) == 19
    assert all(a <= b for a, b in zip(values, values[1:]))


def test_demo_definition_and_cli(synthetic_capture, capsys):
    from strategy_verifier import cli

    definition = load_verifier_definition(_DEMO)
    assert definition.family == "ma_touch_trend"

    exit_code = cli.main([
        "verify-run", "--definition", str(_DEMO),
        "--input", str(synthetic_capture),
        "--input-session-mode", "single",
        "--calibrate-only",
    ])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Capture Graph Report" in captured.out
    assert "calibrate_percentiles:" in captured.out


def _ohlc_capture(tmp_path: Path, rows, name="ohlc.jsonl") -> Path:
    from strategy_verifier.public_market_data import (
        BinanceUmKlinesConfig,
        run_binance_um_klines_conversion,
    )

    lines = []
    for index, (o, h, l, c, v) in enumerate(rows):
        open_ms = (96 * 20500 + index) * 900000
        lines.append(
            f"{open_ms},{o},{h},{l},{c},{v},{open_ms + 899999},1,1,1,1,0"
        )
    csv_path = tmp_path / (name + ".csv")
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    capture = tmp_path / name
    run_binance_um_klines_conversion(
        BinanceUmKlinesConfig(
            input_path=csv_path, output_path=capture,
            symbol="BTCUSDT", bar_path="ohlc",
        )
    )
    return capture


def _anatomy_raw(calibrate_series: str) -> dict:
    return {
        "family": "anatomy",
        "engine": "graph_v1",
        "frame": {"bar_seconds": 900},
        "series": [],
        "conditions": [],
        "direction": {
            "long_when": ["close > low"],
            "short_when": ["low > high"],
        },
        "event": {"when": ["always"], "gate_bars": 1},
        "controls": {"stride": 5, "offset": 0, "min_distance_bars": 1},
        "outcomes": {"horizons": [10], "threshold_pcts": ["0.01"]},
        "calibrate": {"series": calibrate_series},
    }


def test_ohlc_anatomy_roundtrip(tmp_path):
    # 변환기 ohlc 모드 -> 프레임 재구성 = 손계산 해부값
    from strategy_verifier.verifier import load_graph_definition

    rows = [
        ("100", "110", "90", "105", "7"),
        ("200", "220", "180", "210", "3"),
    ]
    capture = _ohlc_capture(tmp_path, rows)
    expected = {
        "open": [100, 200], "high": [110, 220], "low": [90, 180],
        "body": [5, 10], "body_abs": [5, 10],
        "upper_wick": [5, 10], "lower_wick": [10, 20],
        "range": [20, 40], "volume": [7, 3],
    }
    for series, values in expected.items():
        raw = _anatomy_raw(series)
        raw["conditions"] = [
            {"name": "always", "type": "compare", "left": "high",
             "op": ">=", "right": "low"},
        ]
        definition = load_graph_definition(raw, Path("inline"))
        result = run_graph(
            definition, [capture], "single", calibrate_only=True
        )
        assert result.bar_count == 2, series
        assert result.calibrate_value_count == 2, series
        ladder = dict(result.calibrate_percentiles)
        low, high = sorted(values)
        assert float(ladder[5]) == low, series
        assert float(ladder[95]) == high, series


def _reversal_raw(event_when, extra_conditions=(), params=None):
    base_params = {"k": "1"}
    if params:
        base_params.update(params)
    return {
        "family": "mini_wick",
        "engine": "graph_v1",
        "frame": {"bar_seconds": 900},
        "params": base_params,
        "series": [
            {"name": "slow", "primitive": "sma", "input": "close",
             "period": 3},
            {"name": "slow_prior", "primitive": "lag", "input": "slow",
             "k": 1},
            {"name": "fast", "primitive": "sma", "input": "close",
             "period": 2},
        ],
        "conditions": [
            {"name": "wick_long", "type": "ratio_compare",
             "a": "lower_wick", "b": "body_abs", "scale": "body_abs",
             "op": ">=", "right": "k"},
            {"name": "wick_short", "type": "ratio_compare",
             "a": "upper_wick", "b": "body_abs", "scale": "body_abs",
             "op": ">=", "right": "k"},
            {"name": "gate", "type": "side_gate",
             "long_when": ["wick_long"], "short_when": ["wick_short"]},
            *extra_conditions,
        ],
        "direction": {
            "long_when": ["close < slow", "slow < slow_prior"],
            "short_when": ["close > slow", "slow > slow_prior"],
        },
        "event": {"when": list(event_when), "gate_bars": 2},
        "controls": {"stride": 50, "offset": 1, "min_distance_bars": 2},
        "outcomes": {"horizons": [8], "threshold_pcts": ["0.004"]},
        "calibrate": {"series": "close"},
    }


_REVERSAL_ROWS = [
    # 하락 추세 형성
    ("100", "100.2", "99.5", "100", "1"),
    ("99", "99.2", "98.5", "98.8", "1"),
    ("97.5", "97.8", "97", "97.2", "1"),
    ("96", "96.3", "95.6", "95.9", "1"),
    # 망치형: 아랫꼬리 5 vs 몸통 0.2
    ("93", "93.5", "88", "93.2", "1"),
    # 상승 추세로 반전 형성
    ("101", "101.4", "100.8", "101.2", "1"),
    ("103", "103.4", "102.8", "103.2", "1"),
    ("105", "105.4", "104.8", "105.2", "1"),
    # 역망치형: 윗꼬리 5 vs 몸통 0.2
    ("107", "112", "106.8", "107.2", "1"),
    ("107.1", "107.3", "106.9", "107.0", "1"),
]


def test_side_gate_selects_by_direction(tmp_path):
    from strategy_verifier.verifier import load_graph_definition

    capture = _ohlc_capture(tmp_path, _REVERSAL_ROWS, "rev.jsonl")
    definition = load_graph_definition(
        _reversal_raw(["gate"]), Path("inline")
    )
    result = run_graph(
        definition, [capture], "single", calibrate_only=False
    )
    assert result.long_event_count == 1
    assert result.short_event_count == 1


def test_signed_compare_gates_and_flips(tmp_path):
    from strategy_verifier.verifier import load_graph_definition

    capture = _ohlc_capture(tmp_path, _REVERSAL_ROWS, "sg.jsonl")
    strict = _reversal_raw(
        ["gate", "thrust"],
        extra_conditions=(
            {"name": "thrust", "type": "signed_compare",
             "value": "close", "reference": "fast", "scale": "close",
             "minimum": "tm"},
        ),
        params={"tm": "0.001"},
    )
    definition = load_graph_definition(strict, Path("inline"))
    result = run_graph(
        definition, [capture], "single", calibrate_only=False
    )
    assert result.event_count == 0  # 반전 직후 close는 fast 반대편

    loose = _reversal_raw(
        ["gate", "thrust"],
        extra_conditions=(
            {"name": "thrust", "type": "signed_compare",
             "value": "close", "long_reference": "fast",
             "short_reference": "fast", "scale": "close",
             "minimum": "tm"},
        ),
        params={"tm": "-0.1"},
    )
    definition = load_graph_definition(loose, Path("inline"))
    result = run_graph(
        definition, [capture], "single", calibrate_only=False
    )
    assert result.long_event_count == 1
    assert result.short_event_count == 1


def test_new_vocab_validations(tmp_path):
    from strategy_verifier.verifier import load_graph_definition

    bad = _reversal_raw(["gate"])
    bad["direction"]["long_when"] = ["gate"]
    with pytest.raises(DomainValidationError):
        load_graph_definition(bad, Path("inline"))

    bad2 = _reversal_raw(["gate"])
    bad2["conditions"].append(
        {"name": "r", "type": "run", "on": "gate", "minimum": 2}
    )
    with pytest.raises(DomainValidationError):
        load_graph_definition(bad2, Path("inline"))

    bad3 = _reversal_raw(["gate"])
    bad3["conditions"][2] = {
        "name": "gate", "type": "side_gate",
        "long_when": ["ghost"], "short_when": ["wick_short"],
    }
    with pytest.raises(DomainValidationError):
        load_graph_definition(bad3, Path("inline"))

    bad4 = _reversal_raw(["gate"])
    bad4["conditions"].append(
        {"name": "t", "type": "signed_compare", "value": "close",
         "reference": "fast", "long_reference": "fast",
         "short_reference": "fast", "scale": "close", "minimum": "k"}
    )
    with pytest.raises(DomainValidationError):
        load_graph_definition(bad4, Path("inline"))


def test_demo_wick_reversal_loads():
    from strategy_verifier.verifier import (
        GraphDefinition,
        load_verifier_definition,
    )

    definition = load_verifier_definition(
        Path("definitions/wick_reversal.toml")
    )
    assert isinstance(definition, GraphDefinition)
    assert definition.family == "wick_reversal"
    assert definition.calibrate_series == "range_pct"


def test_cmf_engine_integration(tmp_path):
    """A cmf series loads, runs, and its calibrated values equal the
    money-flow ratio recomputed by hand over each 2-bar window."""
    from decimal import Decimal

    from strategy_verifier.verifier import load_graph_definition

    rows = [
        ("100", "110", "90", "105", "10"),   # MFV=+5,  V=10
        ("105", "120", "100", "110", "20"),  # MFV= 0,  V=20
        ("110", "130", "110", "130", "30"),  # MFV=+30, V=30
    ]
    capture = _ohlc_capture(tmp_path, rows)
    raw = _anatomy_raw("cmf")
    raw["series"] = [{"name": "cmf", "primitive": "cmf", "period": 2}]
    raw["conditions"] = [
        {"name": "always", "type": "compare", "left": "high",
         "op": ">=", "right": "low"},
    ]
    definition = load_graph_definition(raw, Path("inline"))
    result = run_graph(
        definition, [capture], "single", calibrate_only=True
    )
    assert result.bar_count == 3
    assert result.calibrate_value_count == 2  # first bar is warmup
    ladder = dict(result.calibrate_percentiles)
    assert ladder[5] == Decimal(5) / Decimal(30)   # window bars 1,2
    assert ladder[95] == Decimal("0.6")            # window bars 2,3


def test_cmf_schema_rejects_reference_keys():
    from strategy_verifier.verifier import load_graph_definition

    raw = _anatomy_raw("cmf")
    raw["series"] = [
        {"name": "cmf", "primitive": "cmf", "period": 2, "input": "close"},
    ]
    raw["conditions"] = [
        {"name": "always", "type": "compare", "left": "high",
         "op": ">=", "right": "low"},
    ]
    with pytest.raises(DomainValidationError):
        load_graph_definition(raw, Path("inline"))


def test_discount_zone_composition(tmp_path):
    """The SMC premium/discount axis composes from existing
    primitives — no new primitive needed. position =
    (close - min low) / (max high - min low) over a lookback, built
    as ratio(distance(close,lo,/close), distance(hi,lo,/close)) with
    the close normalization cancelling. Values match hand math;
    below 0.5 is discount, above is premium."""
    from strategy_verifier.verifier import load_graph_definition

    rows = [
        ("100", "110", "90", "100", "1"),
        ("100", "120", "100", "115", "1"),   # pos (115-90)/(120-90)=0.8333 premium
        ("120", "130", "110", "112", "1"),   # pos (112-100)/(130-100)=0.4   discount
        ("115", "125", "105", "120", "1"),   # pos (120-105)/(130-105)=0.6   premium
    ]
    capture = _ohlc_capture(tmp_path, rows)
    raw = _anatomy_raw("position")
    raw["series"] = [
        {"name": "lo", "primitive": "rolling_min", "input": "low",
         "period": 2},
        {"name": "hi", "primitive": "rolling_max", "input": "high",
         "period": 2},
        {"name": "num", "primitive": "distance", "a": "close",
         "b": "lo", "denominator": "close"},
        {"name": "den", "primitive": "distance", "a": "hi",
         "b": "lo", "denominator": "close"},
        {"name": "position", "primitive": "ratio", "a": "num",
         "b": "den"},
    ]
    raw["conditions"] = [
        {"name": "always", "type": "compare", "left": "high",
         "op": ">=", "right": "low"},
    ]
    definition = load_graph_definition(raw, Path("inline"))
    result = run_graph(
        definition, [capture], "single", calibrate_only=True
    )
    assert result.bar_count == 4
    assert result.calibrate_value_count == 3  # first bar warms up
    ladder = dict(result.calibrate_percentiles)
    # p5=min, p50=median, p95=max over {0.4, 0.6, 0.8333}
    assert abs(float(ladder[5]) - 0.4) < 1e-9
    assert abs(float(ladder[50]) - 0.6) < 1e-9
    assert abs(float(ladder[95]) - (25 / 30)) < 1e-9
    # exactly one bar in the discount half
    assert float(ladder[5]) < 0.5 <= float(ladder[50])


def test_order_block_event_fires_on_reentry(tmp_path):
    """A graph_v1 definition using the order_block condition fires one
    long event on the bar where price re-enters a confirmed bullish
    order block, and nowhere else."""
    from strategy_verifier.verifier import load_graph_definition

    rows = [
        # (open, high, low, close)
        ("110", "112", "99", "100", "1"),   # 0 down -> candidate [99,112]
        ("101", "126", "101", "125", "1"),  # 1 displacement confirms
        ("125", "130", "124", "128", "1"),  # 2 above band
        ("127", "127", "108", "110", "1"),  # 3 re-entry -> long event
        ("110", "111", "109", "111", "1"),  # 4 still inside
        ("111", "113", "110", "112", "1"),  # 5 future for outcome
        ("112", "114", "111", "113", "1"),  # 6 future for outcome
    ]
    capture = _ohlc_capture(tmp_path, rows)
    raw = {
        "family": "ob_test",
        "engine": "graph_v1",
        "frame": {"bar_seconds": 900},
        "params": {},
        "series": [],
        "conditions": [
            {"name": "ob", "type": "order_block",
             "push_window": 3, "displacement": "1.0"},
        ],
        "direction": {"long_when": ["ob"], "short_when": ["low > high"]},
        "event": {"when": ["ob"], "gate_bars": 1},
        "controls": {"stride": 5, "offset": 0, "min_distance_bars": 1},
        "outcomes": {"horizons": [1], "threshold_pcts": ["0.001"]},
    }
    definition = load_graph_definition(raw, Path("inline"))
    result = run_graph(definition, [capture], "single", calibrate_only=False)
    assert result.event_count == 1
    assert result.long_event_count == 1
    assert result.short_event_count == 0


def test_order_block_schema_rejects_bad_keys(tmp_path):
    from strategy_verifier.verifier import load_graph_definition

    raw = {
        "family": "ob_bad", "engine": "graph_v1",
        "frame": {"bar_seconds": 900}, "params": {}, "series": [],
        "conditions": [
            {"name": "ob", "type": "order_block", "push_window": 3,
             "displacement": "1.0", "extra": 1},
        ],
        "direction": {"long_when": ["ob"], "short_when": ["low > high"]},
        "event": {"when": ["ob"], "gate_bars": 1},
        "controls": {"stride": 5, "offset": 0, "min_distance_bars": 1},
        "outcomes": {"horizons": [1], "threshold_pcts": ["0.001"]},
    }
    with pytest.raises(DomainValidationError):
        load_graph_definition(raw, Path("inline"))
