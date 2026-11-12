from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.domain import TradeSide, TradeTick
from strategy_verifier.public_market_data import (
    PublicTradeCaptureReadConfig,
    PublicTradeCaptureReadResult,
    read_public_trade_capture_jsonl,
)


FIXTURE_PATH = Path("tests/fixtures/public_trade_capture_sample.jsonl")


def test_reader_loads_deterministic_jsonl_fixture():
    result = read_public_trade_capture_jsonl(
        PublicTradeCaptureReadConfig(input_path=FIXTURE_PATH)
    )

    assert isinstance(result, PublicTradeCaptureReadResult)
    assert result.total_jsonl_lines == 3
    assert result.valid_event_count == 3
    assert result.invalid_line_count == 0
    assert result.stream_events[0].mapped_trade_tick is not None
    assert result.stream_events[0].mapped_trade_tick.symbol == "KRW-BTC"


def test_reader_preserves_event_order():
    result = read_public_trade_capture_jsonl(
        PublicTradeCaptureReadConfig(input_path=FIXTURE_PATH)
    )

    assert [event.sequence for event in result.stream_events] == [0, 1, 2]
    assert [
        event.raw_payload["trade_price"]
        for event in result.stream_events
    ] == ["101", "102", "103"]


def test_reader_counts_total_valid_and_invalid_lines(tmp_path):
    path = tmp_path / "mixed.jsonl"
    path.write_text(
        "\n".join(
            [
                _valid_record_line(0),
                "{not-json",
                _valid_record_line(2),
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = read_public_trade_capture_jsonl(
        PublicTradeCaptureReadConfig(input_path=path)
    )

    assert result.total_jsonl_lines == 3
    assert result.valid_event_count == 2
    assert result.invalid_line_count == 1
    assert result.error_messages == ["line 2: malformed JSON"]


def test_reader_rejects_missing_file_cleanly(tmp_path):
    with pytest.raises(DomainValidationError, match="does not exist"):
        read_public_trade_capture_jsonl(
            PublicTradeCaptureReadConfig(input_path=tmp_path / "missing.jsonl")
        )


def test_reader_rejects_non_jsonl_path_cleanly(tmp_path):
    with pytest.raises(DomainValidationError, match="local JSONL"):
        PublicTradeCaptureReadConfig(input_path=tmp_path / "capture.json")


def test_reader_can_fail_closed_on_malformed_json_line(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text("{not-json\n", encoding="utf-8")

    with pytest.raises(DomainValidationError, match="line 1: malformed JSON"):
        read_public_trade_capture_jsonl(
            PublicTradeCaptureReadConfig(
                input_path=path,
                skip_invalid_lines=False,
            )
        )


def test_reader_parses_numeric_payload_values_without_float(tmp_path):
    path = tmp_path / "numeric.jsonl"
    record = _valid_record(0)
    record["raw_payload"]["trade_price"] = 100.5
    record["mapped_trade_tick"] = None
    path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")

    result = read_public_trade_capture_jsonl(
        PublicTradeCaptureReadConfig(input_path=path)
    )

    raw_price = result.stream_events[0].raw_payload["trade_price"]
    assert isinstance(raw_price, Decimal)
    assert not isinstance(raw_price, float)
    assert result.stream_events[0].mapped_trade_tick is not None
    assert result.stream_events[0].mapped_trade_tick.price == Decimal("100.5")


def test_reader_reconstructs_mapped_trade_tick_from_capture_record(tmp_path):
    path = tmp_path / "mapped.jsonl"
    record = _valid_record(0)
    record["raw_payload"]["timestamp"] = "1780918022263"
    record["raw_payload"]["trade_timestamp"] = "1780918022213"
    path.write_text(_record_line(record), encoding="utf-8")

    result = read_public_trade_capture_jsonl(
        PublicTradeCaptureReadConfig(input_path=path)
    )

    tick = result.stream_events[0].mapped_trade_tick
    assert isinstance(tick, TradeTick)
    assert tick.symbol == "KRW-BTC"
    assert tick.price == Decimal("101")
    assert tick.quantity == Decimal("0.5")
    assert tick.side is TradeSide.BUY
    assert tick.timestamp.tzinfo is not None


def test_reader_missing_mapped_trade_tick_falls_back_to_raw_payload_rebuild(tmp_path):
    path = tmp_path / "raw-rebuild.jsonl"
    record = _valid_record(0)
    record["mapped_trade_tick"] = None
    path.write_text(_record_line(record), encoding="utf-8")

    result = read_public_trade_capture_jsonl(
        PublicTradeCaptureReadConfig(input_path=path)
    )

    assert result.valid_event_count == 1
    assert result.stream_events[0].mapped_trade_tick is not None
    assert result.stream_events[0].mapped_trade_tick.price == Decimal("101")


def test_reader_malformed_mapped_trade_tick_line_is_invalid(tmp_path):
    path = tmp_path / "bad-mapped.jsonl"
    record = _valid_record(0)
    record["raw_payload"]["timestamp"] = "1780918022263"
    record["raw_payload"]["trade_timestamp"] = "1780918022213"
    record["mapped_trade_tick"]["price"] = "-1"
    path.write_text(_record_line(record), encoding="utf-8")

    result = read_public_trade_capture_jsonl(
        PublicTradeCaptureReadConfig(input_path=path)
    )

    assert result.valid_event_count == 0
    assert result.invalid_line_count == 1
    assert result.error_messages == ["line 1: price must be greater than zero"]


def _valid_record_line(sequence: int) -> str:
    return _record_line(_valid_record(sequence))


def _record_line(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def _valid_record(sequence: int) -> dict:
    timestamp = f"2026-01-02T00:00:0{sequence + 1}+00:00"
    price = str(101 + sequence)
    return {
        "sequence": sequence,
        "received_at": timestamp,
        "source": "upbit_public_trade",
        "raw_payload": {
            "market": "KRW-BTC",
            "trade_timestamp": timestamp,
            "trade_price": price,
            "trade_volume": "0.5",
            "ask_bid": "BID",
        },
        "mapped_trade_tick": {
            "symbol": "KRW-BTC",
            "timestamp": timestamp,
            "price": price,
            "quantity": "0.5",
            "side": "BUY",
        },
        "mapping_error": None,
        "is_duplicate": False,
        "is_out_of_order": False,
    }
