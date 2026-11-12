"""Tests for verifier definition files (product v0).

The pilot definition must dispatch to the oracle-pinned squeeze
engine and reproduce its output byte for byte on the same inputs;
schema violations must be rejected."""

from pathlib import Path

import pytest

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.verifier import (
    load_verifier_definition,
    run_verifier_definition,
)

_PILOT = Path("definitions/ma_touch_trend.toml")
_FIXTURE = Path("tests/fixtures/public_trade_capture_sample.jsonl")


def test_schema_rejections(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text('family = "x"\nengine = "nope"\n', encoding="utf-8")
    with pytest.raises(DomainValidationError):
        load_verifier_definition(bad)
    # Only the general graph_v1 engine is supported; retired per-family
    # engines are refused by name.
    legacy = tmp_path / "legacy.toml"
    legacy.write_text(
        'family = "x"\nengine = "squeeze_play_trend_v1"\n', encoding="utf-8"
    )
    with pytest.raises(DomainValidationError):
        load_verifier_definition(legacy)
    with pytest.raises(DomainValidationError):
        run_verifier_definition(
            object(), input_paths=[_FIXTURE], input_session_mode="single"
        )


def test_cli_verify_run(capsys):
    from strategy_verifier import cli

    exit_code = cli.main(
        [
            "verify-run",
            "--definition", str(_PILOT),
            "--input", str(_FIXTURE),
            "--input-session-mode", "single",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "family: ma_touch_trend" in captured.out


