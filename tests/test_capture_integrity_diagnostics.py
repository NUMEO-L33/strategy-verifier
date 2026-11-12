import json
from pathlib import Path

from strategy_verifier.public_market_data import (
    CaptureIntegrityDiagnosticsConfig,
    PublicTradeCaptureReadConfig,
    run_capture_integrity_diagnostics,
)


def test_clean_capture_is_safe_for_strategy_analysis(tmp_path: Path) -> None:
    first = tmp_path / "krw-btc-clean-1000-001.jsonl"
    second = tmp_path / "krw-btc-clean-1000-002.jsonl"
    _write_capture(first, [_record(0, "2026-01-01T00:00:01+00:00", "101")])
    _write_capture(second, [_record(0, "2026-01-01T00:00:02+00:00", "102")])

    result = run_capture_integrity_diagnostics(_config(first, second))

    assert result.safe_for_strategy_analysis is True
    assert result.issue_count == 0
    assert result.clean_event_count == 2
    assert result.completed_bars_by_tick_size_if_clean[100] == 0


def test_duplicate_across_chunks_is_detected_without_modifying_capture(
    tmp_path: Path,
) -> None:
    first = tmp_path / "krw-btc-clean-1000-001.jsonl"
    second = tmp_path / "krw-btc-clean-1000-002.jsonl"
    duplicate = _record(0, "2026-01-01T00:00:01+00:00", "101")
    _write_capture(first, [duplicate])
    _write_capture(second, [duplicate])

    result = run_capture_integrity_diagnostics(_config(first, second))

    assert result.safe_for_strategy_analysis is False
    assert result.detected_duplicate_payload_count == 1
    assert result.issue_event_count == 1
    assert result.stored_duplicate_count == 0
    assert result.clean_event_count == 1
    assert "capture contains duplicate public trade payloads" in result.block_reasons
    assert first.read_text(encoding="utf-8").count("\n") == 1
    assert second.read_text(encoding="utf-8").count("\n") == 1


def test_out_of_order_timestamp_across_chunks_is_detected(tmp_path: Path) -> None:
    first = tmp_path / "krw-btc-clean-1000-001.jsonl"
    second = tmp_path / "krw-btc-clean-1000-002.jsonl"
    _write_capture(first, [_record(0, "2026-01-01T00:00:03+00:00", "103")])
    _write_capture(second, [_record(0, "2026-01-01T00:00:02+00:00", "102")])

    result = run_capture_integrity_diagnostics(_config(first, second))

    assert result.safe_for_strategy_analysis is False
    assert result.detected_out_of_order_timestamp_count == 1
    assert result.issue_event_count == 1
    assert result.stored_out_of_order_count == 0
    assert "capture contains out-of-order public trade timestamps" in result.block_reasons


def _config(*paths: Path) -> CaptureIntegrityDiagnosticsConfig:
    return CaptureIntegrityDiagnosticsConfig(
        read_configs=tuple(
            PublicTradeCaptureReadConfig(input_path=path) for path in paths
        )
    )


def _write_capture(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def _record(sequence: int, timestamp: str, price: str) -> dict:
    return {
        "sequence": sequence,
        "received_at": timestamp,
        "source": "upbit_public_trade",
        "raw_payload": {
            "market": "KRW-BTC",
            "sequential_id": sequence,
            "trade_timestamp": timestamp,
            "trade_price": price,
            "trade_volume": "0.1",
            "ask_bid": "BID",
        },
        "mapped_trade_tick": {
            "symbol": "KRW-BTC",
            "timestamp": timestamp,
            "price": price,
            "quantity": "0.1",
            "side": "BUY",
        },
        "mapping_error": None,
        "is_duplicate": False,
        "is_out_of_order": False,
    }
