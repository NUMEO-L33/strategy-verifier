"""Equivalence tests for the streaming integrity gate.

Injects duplicates, out-of-order timestamps, invalid lines, and blank
lines into synthetic captures, then requires the streaming diagnostics
and gated writer to match the retained in-memory references exactly:
identical results, identical manifests, and byte-identical gated files.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from strategy_verifier.public_market_data import (
    BinanceUmAggTradeHistoryConfig,
    run_binance_um_aggtrade_history_conversion,
)
from strategy_verifier.public_market_data.capture_integrity_diagnostics import (
    CaptureIntegrityDiagnosticsConfig,
    _run_in_memory_reference_diagnostics,
    run_capture_integrity_diagnostics,
)
from strategy_verifier.public_market_data.capture_integrity_preparation import (
    _first_last_timestamps,
    _raw_manifest_sha,
    _write_gated_files,
    _write_gated_files_in_memory_reference,
    prepare_capture_inputs,
    render_integrity_manifest,
)
from strategy_verifier.public_market_data.public_trade_capture_reader import (
    PublicTradeCaptureReadConfig,
)


HEADER = (
    "agg_trade_id,price,quantity,first_trade_id,last_trade_id,"
    "transact_time,is_buyer_maker"
)


def _write_converted(path: Path, start_id: int, count: int) -> None:
    lines = [HEADER]
    for offset in range(count):
        trade_id = start_id + offset
        lines.append(
            f"{trade_id},{60000 + trade_id},0.5,{trade_id * 2},"
            f"{trade_id * 2 + 1},{1750000000000 + trade_id * 250},false"
        )
    csv_path = path.with_suffix(".csv")
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run_binance_um_aggtrade_history_conversion(
        BinanceUmAggTradeHistoryConfig(
            input_path=csv_path,
            output_path=path,
            symbol="BTCUSDT",
            overwrite=True,
        )
    )


@pytest.fixture(scope="module")
def dirty_capture_paths(tmp_path_factory) -> list[Path]:
    """Three files with injected duplicate, out-of-order, and invalid lines."""
    base = tmp_path_factory.mktemp("integrity-equivalence")
    paths = [
        base / "alpha-2026-06-01-100-1.jsonl",
        base / "alpha-2026-06-02-100-1.jsonl",
        base / "beta-2026-06-01-100-1.jsonl",
    ]
    _write_converted(paths[0], start_id=1, count=40)
    _write_converted(paths[1], start_id=101, count=40)
    _write_converted(paths[2], start_id=201, count=40)

    # Cross-file duplicate payload: copy a line from file 0 into file 1.
    file0_lines = paths[0].read_text(encoding="utf-8").splitlines()
    file1_lines = paths[1].read_text(encoding="utf-8").splitlines()
    file1_lines.insert(5, file0_lines[3])
    # Out-of-order timestamp inside file 1: swap two later lines.
    file1_lines[20], file1_lines[21] = file1_lines[21], file1_lines[20]
    # Invalid and blank lines inside file 2.
    file2_lines = paths[2].read_text(encoding="utf-8").splitlines()
    file2_lines.insert(10, "this is not json")
    file2_lines.insert(3, "")
    paths[1].write_text("\n".join(file1_lines) + "\n", encoding="utf-8")
    paths[2].write_text("\n".join(file2_lines) + "\n", encoding="utf-8")
    return paths


def test_streaming_diagnostics_matches_reference(dirty_capture_paths):
    config = CaptureIntegrityDiagnosticsConfig(
        read_configs=tuple(
            PublicTradeCaptureReadConfig(input_path=path)
            for path in dirty_capture_paths
        )
    )

    streaming = run_capture_integrity_diagnostics(config)
    reference = _run_in_memory_reference_diagnostics(config)

    assert streaming.valid_event_count == reference.valid_event_count
    assert streaming.invalid_line_count == reference.invalid_line_count == 1
    assert (
        streaming.detected_duplicate_payload_count
        == reference.detected_duplicate_payload_count
        == 1
    )
    assert (
        streaming.detected_out_of_order_timestamp_count
        == reference.detected_out_of_order_timestamp_count
    )
    assert streaming.issue_event_count == reference.issue_event_count
    assert streaming.clean_event_count == reference.clean_event_count
    assert streaming.block_reasons == reference.block_reasons
    assert len(streaming.issues) == len(reference.issues)
    for mine, theirs in zip(streaming.issues, reference.issues):
        assert mine == theirs


@pytest.mark.parametrize("input_session_mode", ["file", "prefix", "single"])
def test_streaming_gated_files_match_reference_bytes(
    dirty_capture_paths,
    input_session_mode,
):
    config = CaptureIntegrityDiagnosticsConfig(
        read_configs=tuple(
            PublicTradeCaptureReadConfig(input_path=path)
            for path in dirty_capture_paths
        )
    )
    diagnostics = run_capture_integrity_diagnostics(config)

    with TemporaryDirectory() as streaming_dir, TemporaryDirectory() as reference_dir:
        streaming_paths, streaming_sha = _write_gated_files(
            raw_paths=tuple(dirty_capture_paths),
            diagnostics=diagnostics,
            output_dir=Path(streaming_dir),
            input_session_mode=input_session_mode,
        )
        reference_paths, reference_sha = _write_gated_files_in_memory_reference(
            raw_paths=tuple(dirty_capture_paths),
            diagnostics=diagnostics,
            output_dir=Path(reference_dir),
            input_session_mode=input_session_mode,
        )

        assert streaming_sha == reference_sha
        assert [path.name for path in streaming_paths] == [
            path.name for path in reference_paths
        ]
        for streaming_path, reference_path in zip(
            streaming_paths, reference_paths
        ):
            assert streaming_path.read_bytes() == reference_path.read_bytes()


def test_prepared_manifest_is_stable_end_to_end(dirty_capture_paths):
    with prepare_capture_inputs(
        input_paths=tuple(dirty_capture_paths),
        integrity_gated=True,
        require_integrity_safe=False,
        input_session_mode="single",
    ) as prepared:
        manifest = prepared.manifest
        rendered = render_integrity_manifest(manifest)
        gated_total = sum(
            sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line)
            for path in prepared.input_paths
        )

        assert manifest.integrity_mode == "gated"
        assert manifest.duplicate_issue_count == 1
        assert manifest.invalid_line_count == 1
        assert gated_total == manifest.gated_clean_event_count
        assert "gated_event_stream_sha256" in rendered
        assert manifest.first_timestamp is not None
        assert manifest.last_timestamp is not None


def test_streaming_helper_functions_match_expected(dirty_capture_paths):
    first, last = _first_last_timestamps(tuple(dirty_capture_paths))
    sha_one = _raw_manifest_sha(tuple(dirty_capture_paths))
    sha_two = _raw_manifest_sha(tuple(dirty_capture_paths))

    assert first is not None and last is not None
    assert first < last
    assert sha_one == sha_two
    assert len(sha_one) == 64


@pytest.fixture(scope="module")
def clean_capture_paths(tmp_path_factory) -> list[Path]:
    base = tmp_path_factory.mktemp("integrity-clean")
    paths = [
        base / "gamma-2026-06-01-100-1.jsonl",
        base / "gamma-2026-06-02-100-1.jsonl",
    ]
    _write_converted(paths[0], start_id=1, count=30)
    _write_converted(paths[1], start_id=101, count=30)
    return paths


def test_clean_inputs_pass_through_without_materialization(
    clean_capture_paths,
):
    from strategy_verifier.public_market_data.capture_integrity_preparation import (
        _clean_stream_sha,
    )

    with prepare_capture_inputs(
        input_paths=tuple(clean_capture_paths),
        integrity_gated=True,
        require_integrity_safe=True,
        input_session_mode="single",
    ) as prepared:
        assert prepared.input_paths == tuple(clean_capture_paths)
        assert prepared.manifest.integrity_mode == "gated"
        assert prepared.manifest.duplicate_issue_count == 0
        assert prepared.manifest.gated_clean_event_count == 60

        config = CaptureIntegrityDiagnosticsConfig(
            read_configs=tuple(
                PublicTradeCaptureReadConfig(input_path=path)
                for path in clean_capture_paths
            )
        )
        diagnostics = run_capture_integrity_diagnostics(config)
        with TemporaryDirectory() as reference_dir:
            _, reference_sha = _write_gated_files_in_memory_reference(
                raw_paths=tuple(clean_capture_paths),
                diagnostics=diagnostics,
                output_dir=Path(reference_dir),
                input_session_mode="single",
            )
        assert prepared.manifest.gated_event_stream_sha256 == reference_sha
        assert _clean_stream_sha(tuple(clean_capture_paths)) == reference_sha


def test_dirty_inputs_still_materialize_gated_files(dirty_capture_paths):
    with prepare_capture_inputs(
        input_paths=tuple(dirty_capture_paths),
        integrity_gated=True,
        require_integrity_safe=False,
        input_session_mode="single",
    ) as prepared:
        assert prepared.input_paths != tuple(dirty_capture_paths)
        for path in prepared.input_paths:
            assert "verifier_integrity_gated_" in str(path)
            assert path.exists()
