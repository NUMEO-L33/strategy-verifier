from pathlib import Path

from strategy_verifier.public_market_data.capture_session_continuity import (
    capture_session_group_id,
)


def test_prefix_parser_handles_common_chunk_names():
    assert (
        capture_session_group_id(
            Path("krw-btc-v5-oos3-5000-001.jsonl"),
            "prefix",
        )
        == "krw-btc-v5-oos3"
    )
    assert (
        capture_session_group_id(
            Path("krw-btc-v5-oos3-50000-001.jsonl"),
            "prefix",
        )
        == "krw-btc-v5-oos3"
    )
    assert (
        capture_session_group_id(
            Path("krw-btc-v5-oos-cont1-20000-001.jsonl"),
            "prefix",
        )
        == "krw-btc-v5-oos-cont1"
    )


def test_file_mode_uses_each_file_as_session_group():
    assert (
        capture_session_group_id(
            Path("krw-btc-v5-oos3-5000-001.jsonl"),
            "file",
        )
        == "krw-btc-v5-oos3-5000-001.jsonl"
    )
