import hashlib
import io
import json
from pathlib import Path

import pytest

from strategy_verifier import cli
from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.public_market_data.capture_integrity_preparation import (
    prepare_capture_inputs,
)


def test_gated_preparation_excludes_duplicate_and_out_of_order_once(
    tmp_path: Path,
) -> None:
    first = tmp_path / "krw-btc-clean-100-001.jsonl"
    second = tmp_path / "krw-btc-clean-100-002.jsonl"
    original = _record(0, "2026-01-01T00:00:01+00:00", "101")
    later = _record(1, "2026-01-01T00:00:03+00:00", "103")
    _write_capture(first, [original, later])
    _write_capture(second, [original])
    first_before = first.read_bytes()
    second_before = second.read_bytes()

    with prepare_capture_inputs(
        input_paths=(first, second),
        integrity_gated=True,
        require_integrity_safe=False,
        input_session_mode="prefix",
    ) as prepared:
        assert prepared.manifest.integrity_mode == "gated"
        assert prepared.manifest.raw_valid_event_count == 3
        assert prepared.manifest.duplicate_issue_count == 1
        assert prepared.manifest.out_of_order_issue_count == 1
        assert prepared.manifest.unique_excluded_issue_event_count == 1
        assert prepared.manifest.gated_clean_event_count == 2
        assert len(prepared.input_paths) == 1
        clean_text = prepared.input_paths[0].read_text(encoding="utf-8")
        assert clean_text.count("\n") == 2
        expected_sha = hashlib.sha256(
            (
                json.dumps(original, sort_keys=True)
                + "\n"
                + json.dumps(later, sort_keys=True)
                + "\n"
            ).encode("utf-8")
        ).hexdigest()
        assert prepared.manifest.gated_event_stream_sha256 == expected_sha

    assert first.read_bytes() == first_before
    assert second.read_bytes() == second_before


def test_gated_preparation_sha_is_independent_of_input_argument_order(
    tmp_path: Path,
) -> None:
    first = tmp_path / "krw-btc-clean-100-001.jsonl"
    second = tmp_path / "krw-btc-clean-100-002.jsonl"
    _write_capture(first, [_record(0, "2026-01-01T00:00:01+00:00", "101")])
    _write_capture(second, [_record(0, "2026-01-01T00:00:02+00:00", "102")])

    shas: list[str] = []
    for paths in ((first, second), (second, first), (first, second)):
        with prepare_capture_inputs(
            input_paths=paths,
            integrity_gated=True,
            require_integrity_safe=False,
            input_session_mode="prefix",
        ) as prepared:
            shas.append(prepared.manifest.gated_event_stream_sha256 or "")

    assert shas[0] == shas[1] == shas[2]


def test_gated_preparation_preserves_session_mode_grouping(tmp_path: Path) -> None:
    """Clean inputs pass through unmaterialized; dirty inputs merge.

    Downstream engines re-apply the session mode to the file names they
    receive, so passthrough of the original paths preserves session
    semantics exactly; the merged-file layout is exercised on the dirty
    path, where materialization still happens.
    """
    first = tmp_path / "krw-btc-clean-100-001.jsonl"
    second = tmp_path / "krw-btc-clean-100-002.jsonl"
    _write_capture(first, [_record(0, "2026-01-01T00:00:01+00:00", "101")])
    _write_capture(second, [_record(0, "2026-01-01T00:00:02+00:00", "102")])

    with prepare_capture_inputs(
        input_paths=(first, second),
        integrity_gated=True,
        require_integrity_safe=False,
        input_session_mode="prefix",
    ) as clean_mode:
        assert clean_mode.input_paths == (first, second)

    dirty_first = tmp_path / "krw-eth-dirty-100-001.jsonl"
    dirty_second = tmp_path / "krw-eth-dirty-100-002.jsonl"
    duplicate = _record(0, "2026-01-01T00:00:01+00:00", "101")
    _write_capture(dirty_first, [duplicate])
    _write_capture(dirty_second, [duplicate])

    with prepare_capture_inputs(
        input_paths=(dirty_first, dirty_second),
        integrity_gated=True,
        require_integrity_safe=False,
        input_session_mode="file",
    ) as dirty_file_mode:
        assert len(dirty_file_mode.input_paths) == 2
        assert dirty_file_mode.input_paths != (dirty_first, dirty_second)

    with prepare_capture_inputs(
        input_paths=(dirty_first, dirty_second),
        integrity_gated=True,
        require_integrity_safe=False,
        input_session_mode="prefix",
    ) as dirty_prefix_mode:
        assert len(dirty_prefix_mode.input_paths) == 1


def test_require_integrity_safe_fails_closed_when_raw_capture_has_issues(
    tmp_path: Path,
) -> None:
    first = tmp_path / "krw-btc-clean-100-001.jsonl"
    second = tmp_path / "krw-btc-clean-100-002.jsonl"
    duplicate = _record(0, "2026-01-01T00:00:01+00:00", "101")
    _write_capture(first, [duplicate])
    _write_capture(second, [duplicate])

    with pytest.raises(DomainValidationError):
        with prepare_capture_inputs(
            input_paths=(first, second),
            integrity_gated=False,
            require_integrity_safe=True,
            input_session_mode="prefix",
        ):
            raise AssertionError("unreachable")


def test_verify_run_cli_help_exposes_integrity_options(capsys) -> None:
    exit_code = cli.main(
        [
            "verify-run",
            "--definition",
            "definitions/ma_touch_trend.toml",
            "--help",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "--integrity-gated" in output
    assert "--require-integrity-safe" in output


def test_verify_run_cli_renders_raw_warning_for_unsafe_capture(
    tmp_path: Path,
) -> None:
    first = tmp_path / "krw-btc-clean-100-001.jsonl"
    second = tmp_path / "krw-btc-clean-100-002.jsonl"
    duplicate = _record(0, "2026-01-01T00:00:01+00:00", "101")
    _write_capture(first, [duplicate])
    _write_capture(second, [duplicate])
    stdout = io.StringIO()
    exit_code = cli.main(
        [
            "verify-run",
            "--definition",
            "definitions/ma_touch_trend.toml",
            "--input",
            str(first),
            str(second),
            "--input-session-mode",
            "prefix",
        ],
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert "integrity_mode: raw" in output
    assert "duplicate_issue_count: 1" in output
    assert "integrity_warning: raw capture contains integrity issues" in output


def test_verify_run_cli_renders_gated_manifest_for_capture(
    tmp_path: Path,
) -> None:
    first = tmp_path / "krw-btc-clean-100-001.jsonl"
    second = tmp_path / "krw-btc-clean-100-002.jsonl"
    duplicate = _record(0, "2026-01-01T00:00:01+00:00", "101")
    _write_capture(first, [duplicate])
    _write_capture(second, [duplicate])
    stdout = io.StringIO()
    exit_code = cli.main(
        [
            "verify-run",
            "--definition",
            "definitions/ma_touch_trend.toml",
            "--input",
            str(second),
            str(first),
            "--input-session-mode",
            "prefix",
            "--integrity-gated",
        ],
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert "integrity_mode: gated" in output
    assert "raw_valid_event_count: 2" in output
    assert "unique_excluded_issue_event_count: 1" in output
    assert "gated_clean_event_count: 1" in output


def test_integrity_gated_with_require_integrity_safe_analyzes_clean_stream(
    tmp_path: Path,
) -> None:
    first = tmp_path / "krw-btc-clean-100-001.jsonl"
    second = tmp_path / "krw-btc-clean-100-002.jsonl"
    duplicate = _record(0, "2026-01-01T00:00:01+00:00", "101")
    _write_capture(first, [duplicate])
    _write_capture(second, [duplicate])
    stdout = io.StringIO()
    exit_code = cli.main(
        [
            "verify-run",
            "--definition",
            "definitions/ma_touch_trend.toml",
            "--input",
            str(first),
            str(second),
            "--input-session-mode",
            "prefix",
            "--integrity-gated",
            "--require-integrity-safe",
        ],
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert "integrity_mode: gated" in output
    assert "duplicate_issue_count: 1" in output
    assert "gated_clean_event_count: 1" in output


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
