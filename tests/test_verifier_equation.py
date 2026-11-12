"""Tests for the equation stamp: input hashing, result-hash
correctness, tamper sensitivity, double-run determinism through the
graph engine, and stamp presence on all three product commands."""

import hashlib
from pathlib import Path

import pytest

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.verifier import (
    file_sha256,
    render_equation_block,
    run_graph,
    render_graph_report,
)


def test_hashing_and_tamper_sensitivity(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("market data", encoding="utf-8")
    digest = file_sha256(a)
    assert digest == hashlib.sha256(b"market data").hexdigest()
    a.write_text("market data.", encoding="utf-8")
    assert file_sha256(a) != digest
    with pytest.raises(DomainValidationError):
        file_sha256(tmp_path / "ghost.txt")


def test_equation_block_result_hash(tmp_path):
    a = tmp_path / "m.jsonl"
    a.write_text("x", encoding="utf-8")
    body = "Report body\n"
    block = render_equation_block([a], None, "order text", body)
    expected = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert f"result: sha256={expected}" in block
    assert "market_combined: sha256=" in block
    assert "determinism:" in block
    # 동일 입력 -> 동일 블록
    assert block == render_equation_block([a], None, "order text", body)


def test_double_run_determinism_graph(tmp_path):
    # 그래프 측정 2회 = 바이트 동일 (자립형 테이프)
    from strategy_verifier.public_market_data import (
        BinanceUmKlinesConfig,
        run_binance_um_klines_conversion,
    )
    from strategy_verifier.verifier import load_graph_definition
    import tests.test_verifier_graph as graph_tests

    rows = []
    price = 50000.0
    for index in range(400):
        price *= 1.001 if (index // 100) % 2 == 0 else 0.999
        open_ms = (96 * 20400 + index) * 900000
        rows.append(
            f"{open_ms},{price:.1f},{price:.1f},{price:.1f},"
            f"{price:.1f},1,{open_ms + 899999},1,1,1,1,0"
        )
    csv_path = tmp_path / "det.csv"
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    capture = tmp_path / "det.jsonl"
    run_binance_um_klines_conversion(
        BinanceUmKlinesConfig(
            input_path=csv_path, output_path=capture, symbol="BTCUSDT"
        )
    )
    raw = graph_tests._small_raw()
    raw["frame"] = {"bar_seconds": 0}
    definition = load_graph_definition(raw, Path("inline"))
    first = render_graph_report(
        run_graph(definition, [capture], "single", calibrate_only=False)
    )
    second = render_graph_report(
        run_graph(definition, [capture], "single", calibrate_only=False)
    )
    assert first == second
    assert "events: rated=" in first


def test_stamp_on_cli_commands(tmp_path, capsys):
    from strategy_verifier import cli

    fixture = Path("tests/fixtures/public_trade_capture_sample.jsonl")
    assert cli.main([
        "verify-run",
        "--definition", "definitions/ma_touch_trend.toml",
        "--input", str(fixture),
        "--input-session-mode", "single",
    ]) == 0
    out = capsys.readouterr().out
    assert "equation:" in out
    assert "technique: ma_touch_trend.toml: sha256=" in out
    assert "order: verify-run session=single calibrate=False inputs=1" in out

    report = tmp_path / "r.txt"
    report.write_text(
        "base_grid:\n- horizon=480 0.01: 70/30/0 (0.7)\n",
        encoding="utf-8",
    )
    assert cli.main([
        "verify-verdict", "--family", "fam",
        "--report", str(report), str(report), str(report),
        "--primary-horizon", "480", "--primary-threshold", "0.01",
    ]) == 0
    assert "equation:" in capsys.readouterr().out

    assert cli.main([
        "verify-diagnose", "--family", "fam",
        "--report", str(report),
        "--primary-horizon", "480", "--primary-threshold", "0.01",
    ]) == 0
    assert "equation:" in capsys.readouterr().out
