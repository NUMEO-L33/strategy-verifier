"""Tests for verdict rendering: the Wilson oracle is pinned against
the squeeze final look's archived arithmetic, both bar modes pass and
fail on hand-built reports, and the CLI carries the outcome in its
exit code."""

from pathlib import Path

import pytest

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.verifier import (
    render_verdict_report,
    run_verdict,
    wilson,
)


def _report(base_f, base_a, control=None) -> str:
    lines = [
        "Capture Synthetic Report",
        "",
        "base_grid (favorable/adverse/untouched share):",
        f"- horizon=480 0.01: {base_f}/{base_a}/0 (0.5)",
    ]
    if control is not None:
        lines.extend(
            [
                "control_grid:",
                f"- horizon=480 0.01: {control[0]}/{control[1]}/0 (0.5)",
            ]
        )
    return "\n".join(lines) + "\n"


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_wilson_matches_archived_squeeze_arithmetic():
    share, low, high = wilson(82, 143)
    assert round(share, 4) == 0.5734
    assert round(low, 4) == 0.4915
    assert round(high, 4) == 0.6515
    share, low, high = wilson(226, 443)
    assert round(low, 4) == 0.4637
    assert round(high, 4) == 0.5564


def test_coin_mode_pass_and_fail(tmp_path):
    reports = [
        _write(tmp_path, f"{name}.txt", _report(70, 30))
        for name in ("btc", "eth", "sol")
    ]
    result = run_verdict(
        "pilot", reports, 480, "0.01", "base_grid", None
    )
    assert result.passed is True
    assert result.verdict_name == "PILOT_CONFIRMED"
    rendered = render_verdict_report(result)
    assert "VERDICT: PILOT_CONFIRMED" in rendered
    assert "[pass]" in rendered

    weak = [
        _write(tmp_path, f"w{index}.txt", _report(51, 49))
        for index in range(3)
    ]
    result = run_verdict("pilot", weak, 480, "0.01", "base_grid", None)
    assert result.passed is False
    assert result.verdict_name == "PILOT_NOT_CONFIRMED"
    assert "closure is permanent" in render_verdict_report(result)


def test_control_mode_separation(tmp_path):
    separated = [
        _write(
            tmp_path, f"s{index}.txt", _report(75, 25, control=(45, 55))
        )
        for index in range(3)
    ]
    result = run_verdict(
        "ctl", separated, 480, "0.01", "base_grid", "control_grid"
    )
    assert result.passed is True
    assert result.control_share is not None

    overlapping = [
        _write(
            tmp_path, f"o{index}.txt", _report(55, 45, control=(50, 50))
        )
        for index in range(3)
    ]
    result = run_verdict(
        "ctl", overlapping, 480, "0.01", "base_grid", "control_grid"
    )
    assert result.passed is False
    assert result.criteria[0].passed is False


def test_errors_and_cli(tmp_path, capsys):
    from strategy_verifier import cli

    good = _write(tmp_path, "g.txt", _report(70, 30))
    with pytest.raises(DomainValidationError):
        run_verdict("x", [good], 999, "0.01", "base_grid", None)
    with pytest.raises(DomainValidationError):
        run_verdict("x", [good], 480, "0.01", "missing_grid", None)
    with pytest.raises(DomainValidationError):
        run_verdict("x", [tmp_path / "nope.txt"], 480, "0.01", "base_grid", None)

    assert cli.main([
        "verify-verdict", "--family", "pilot",
        "--report", str(good), str(good), str(good),
        "--primary-horizon", "480", "--primary-threshold", "0.01",
    ]) == 0
    assert "VERDICT: PILOT_CONFIRMED" in capsys.readouterr().out

    weak = _write(tmp_path, "wk.txt", _report(50, 50))
    assert cli.main([
        "verify-verdict", "--family", "pilot",
        "--report", str(weak), str(weak), str(weak),
        "--primary-horizon", "480", "--primary-threshold", "0.01",
    ]) == 2

    assert cli.main([
        "verify-verdict", "--family", "pilot",
        "--report", str(tmp_path / "nope.txt"),
        "--primary-horizon", "480", "--primary-threshold", "0.01",
    ]) == 1
