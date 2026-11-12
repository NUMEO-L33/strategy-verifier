"""J-2 acceptance: the live reader over the recorder's session files
reproduces the batch runner cell for cell and marker for marker on
the same tape, and bar state survives across drains while the files
grow — one engine, two feeds, structurally."""

import json
import random
from pathlib import Path

from strategy_verifier.verifier import LiveReader, load_graph_definition, run_graph


def _cells(grid):
    return {
        cell: (tally.favorable, tally.adverse, tally.untouched)
        for cell, tally in grid.items()
    }


def _messages():
    random.seed(31)
    price = 50000.0
    index = 0
    out = []
    for bars, drift in [(150, 2.0), (60, 0.1), (40, 120.0),
                        (150, -2.0), (60, 0.1), (40, -120.0)]:
        for _ in range(bars):
            for _ in range(20):
                index += 1
                price += random.uniform(-2.5, 2.5) + drift / 20
                out.append({
                    "e": "aggTrade", "s": "BTCUSDT", "a": index,
                    "p": f"{price:.1f}", "q": "0.5",
                    "f": index * 2, "l": index * 2 + 1,
                    "T": 1750000000000 + index * 3000, "m": False,
                })
    return out


_RAW = {
    "family": "live_parity", "engine": "graph_v1",
    "frame": {"bar_seconds": 60}, "params": {"sigma": "0.002"},
    "series": [
        {"name": "fast", "primitive": "sma", "input": "close",
         "period": 3},
        {"name": "slow", "primitive": "sma", "input": "close",
         "period": 10},
        {"name": "slow_prior", "primitive": "lag", "input": "slow",
         "k": 3},
        {"name": "gap", "primitive": "distance", "a": "fast",
         "b": "slow", "denominator": "close"},
        {"name": "upper", "primitive": "pair_max", "a": "fast",
         "b": "slow"},
        {"name": "lower", "primitive": "pair_min", "a": "fast",
         "b": "slow"},
    ],
    "conditions": [
        {"name": "in_squeeze", "type": "compare", "left": "gap",
         "op": "<=", "right": "sigma"},
        {"name": "standing", "type": "run", "on": "in_squeeze",
         "minimum": 2, "evaluate": "before"},
        {"name": "squeeze_now", "type": "run", "on": "in_squeeze",
         "minimum": 2, "evaluate": "after"},
        {"name": "breakout", "type": "signed_compare",
         "value": "close", "long_reference": "upper",
         "short_reference": "lower", "scale": "close",
         "minimum": "sigma"},
    ],
    "direction": {
        "long_when": ["close > slow", "slow > slow_prior"],
        "short_when": ["close < slow", "slow < slow_prior"],
    },
    "event": {"when": ["standing", "breakout"], "gate_bars": 3},
    "controls": {"stride": 5, "offset": 2, "min_distance_bars": 3,
                 "blocked_by": ["squeeze_now"]},
    "outcomes": {"horizons": [300, 1500],
                 "threshold_pcts": ["0.004", "0.01"]},
}


def test_live_matches_batch_across_growth_and_rotation(tmp_path, capsys):
    from strategy_verifier import cli

    messages = _messages()
    half = len(messages) // 2
    gap_at = half + 137

    # 1) 전반부만 리플레이 → base 파일 생성
    replay_one = tmp_path / "m1.jsonl"
    replay_one.write_text(
        "\n".join(json.dumps(m) for m in messages[:half]) + "\n",
        encoding="utf-8",
    )
    base = tmp_path / "live.jsonl"
    assert cli.main([
        "record", "--symbol", "BTCUSDT", "--output", str(base),
        "--replay-file", str(replay_one),
    ]) == 0
    capsys.readouterr()

    definition = load_graph_definition(_RAW, Path("inline"))
    reader = LiveReader(definition, base)
    first_fed = reader.drain()
    assert first_fed == half

    # 2) 성장: 후반부를 (갭 1회 포함) 이어서 녹화 — s2 회전 발생
    replay_two = tmp_path / "m2.jsonl"
    tail_lines = [json.dumps(m) for m in messages[half:gap_at]]
    tail_lines.append("__gap__")
    tail_lines += [json.dumps(m) for m in messages[gap_at:]]
    replay_two.write_text("\n".join(tail_lines) + "\n",
                          encoding="utf-8")
    # 후반 녹화는 base 에 이어쓰기 불가(덮어쓰기 거부) → 별도 임시로
    # 녹화한 뒤 실제 성장 시뮬레이션: base 에 append + s2 신설
    scratch = tmp_path / "scratch.jsonl"
    assert cli.main([
        "record", "--symbol", "BTCUSDT", "--output", str(scratch),
        "--replay-file", str(replay_two),
    ]) == 0
    capsys.readouterr()
    with base.open("a", encoding="utf-8") as handle:
        handle.write(scratch.read_text(encoding="utf-8"))
    rotated = tmp_path / "live-s2.jsonl"
    rotated.write_text(
        (tmp_path / "scratch-s2.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    second_fed = reader.drain()
    assert first_fed + second_fed == len(messages)
    live = reader.session.finish()
    reader.close()

    batch = run_graph(
        definition, [base, rotated], "single",
        calibrate_only=False, collect_reading=True,
    )
    assert live.event_count == batch.event_count > 0
    assert live.control_count == batch.control_count > 0
    assert _cells(live.base_grid) == _cells(batch.base_grid)
    assert _cells(live.control_grid) == _cells(batch.control_grid)
    for side in ("long", "short"):
        assert _cells(live.side_grids[side]) == _cells(
            batch.side_grids[side]
        )
    assert live.reading_markers == batch.reading_markers
    assert live.bar_count == batch.bar_count
