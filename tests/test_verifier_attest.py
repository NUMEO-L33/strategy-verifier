"""Tests for the attestation equation: roundtrip identity, each term's
tamper is named exactly, measurement code imports no randomness, and
the graph engine is byte-deterministic across runs."""

from pathlib import Path

import pytest

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.verifier import (
    build_attestation,
    check_attestation,
    render_attestation,
)


def _setup(tmp_path: Path):
    definition = tmp_path / "def.toml"
    definition.write_text("family = 'x'\n", encoding="utf-8")
    market_a = tmp_path / "a.jsonl"
    market_a.write_text("row1\n", encoding="utf-8")
    market_b = tmp_path / "b.jsonl"
    market_b.write_text("row2\n", encoding="utf-8")
    report = tmp_path / "report.txt"
    report.write_text("VERDICT: X\n", encoding="utf-8")
    attestation = tmp_path / "attest.txt"
    attestation.write_text(
        render_attestation(
            build_attestation(
                definition, [market_a, market_b], "measure explore",
                report,
            )
        ),
        encoding="utf-8",
    )
    return definition, market_a, market_b, report, attestation


def test_roundtrip_and_each_term_named(tmp_path):
    definition, market_a, market_b, report, attestation = _setup(tmp_path)
    args = (definition, [market_a, market_b], "measure explore", report)
    assert check_attestation(attestation, *args) == (True, ())

    report.write_text("VERDICT: TAMPERED\n", encoding="utf-8")
    assert check_attestation(attestation, *args)[1] == ("report",)
    report.write_text("VERDICT: X\n", encoding="utf-8")

    definition.write_text("family = 'y'\n", encoding="utf-8")
    assert check_attestation(attestation, *args)[1] == ("technique",)
    definition.write_text("family = 'x'\n", encoding="utf-8")

    market_b.write_text("row2 changed\n", encoding="utf-8")
    assert check_attestation(attestation, *args)[1] == ("market",)
    market_b.write_text("row2\n", encoding="utf-8")

    changed_order = (
        definition, [market_a, market_b], "measure confirm", report
    )
    assert check_attestation(attestation, *changed_order)[1] == ("order",)

    rendered = attestation.read_text(encoding="utf-8")
    assert "result = market + technique + order" in rendered
    with pytest.raises(DomainValidationError):
        build_attestation(definition, [], "x", report)
    with pytest.raises(DomainValidationError):
        check_attestation(tmp_path / "ghost.txt", *args)


def test_measurement_code_imports_no_randomness():
    roots = [
        Path("src/strategy_verifier/verifier"),
        Path("src/strategy_verifier/public_market_data"),
    ]
    offenders = []
    for root in roots:
        for path in sorted(root.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "import random" in text or "from random" in text:
                offenders.append(str(path))
    assert offenders == []


def test_graph_engine_is_byte_deterministic(tmp_path):
    from decimal import Decimal
    from strategy_verifier.public_market_data import (
        BinanceUmKlinesConfig,
        run_binance_um_klines_conversion,
    )
    from strategy_verifier.verifier import load_graph_definition
    from strategy_verifier.verifier.graph import render_graph_report, run_graph

    rows = []
    price = Decimal("50000")
    for index in range(400):
        price += Decimal((index * 37) % 11 - 5)
        open_ms = (96 * 20400 + index) * 900000
        rows.append(
            f"{open_ms},{price},{price},{price},{price},1,"
            f"{open_ms + 900000 - 1},1,1,1,1,0"
        )
    csv_path = tmp_path / "d.csv"
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    capture = tmp_path / "d.jsonl"
    run_binance_um_klines_conversion(
        BinanceUmKlinesConfig(
            input_path=csv_path, output_path=capture, symbol="BTCUSDT"
        )
    )
    raw = {
        "family": "det",
        "engine": "graph_v1",
        "frame": {"bar_seconds": 0},
        "params": {"delta": "0.0005"},
        "series": [
            {"name": "fast", "primitive": "sma", "input": "close",
             "period": 3},
            {"name": "slow", "primitive": "sma", "input": "close",
             "period": 10},
            {"name": "slow_prior", "primitive": "lag", "input": "slow",
             "k": 3},
            {"name": "gap", "primitive": "distance", "a": "close",
             "b": "fast", "denominator": "close"},
        ],
        "conditions": [
            {"name": "near", "type": "compare", "left": "gap",
             "op": "<=", "right": "delta"},
        ],
        "direction": {
            "long_when": ["close > slow", "slow > slow_prior"],
            "short_when": ["close < slow", "slow < slow_prior"],
        },
        "event": {"when": ["near"], "gate_bars": 5},
        "controls": {"stride": 7, "offset": 3, "min_distance_bars": 3},
        "outcomes": {
            "horizons": [50, 200],
            "threshold_pcts": ["0.001", "0.004"],
        },
        "calibrate": {"series": "gap"},
    }
    definition = load_graph_definition(raw, Path("inline"))
    first = render_graph_report(
        run_graph(definition, [capture], "single", calibrate_only=False)
    )
    second = render_graph_report(
        run_graph(definition, [capture], "single", calibrate_only=False)
    )
    assert first == second
    assert "events: rated=" in first
