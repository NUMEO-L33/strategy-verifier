"""Tests for the clinical diagnosis layer: every segment receives a
grade, each detector fires on its engineered signature with factual
implications, EDGE segments surface as facts, and the report carries
no prescriptions."""

from pathlib import Path

import pytest

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.verifier import run_diagnosis, render_diagnosis_report

_H = (100, 480, 1440)


def _section(name: str, cells: dict) -> list[str]:
    lines = [f"{name}:"]
    for horizon in _H:
        favorable, adverse = cells.get(horizon, (0, 0))
        lines.append(
            f"- horizon={horizon} 0.01: {favorable}/{adverse}/0 (0.5)"
        )
    return lines


def _write(tmp_path: Path, name: str, sections: dict) -> Path:
    lines = ["Capture Synthetic Report", ""]
    for section, cells in sections.items():
        lines.extend(_section(section, cells))
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _flat(f, a):
    return {h: (f, a) for h in _H}


def _codes(result):
    return {finding.code for finding in result.findings}


def test_grades_decay_single_market_and_drift(tmp_path):
    reports = []
    shares = [(62, 38), (47, 53), (46, 54)]
    for index, (f, a) in enumerate(shares):
        sections = {
            "base_grid": {100: (60, 40), 480: (f, a), 1440: (40, 60)},
            "side=long": _flat(34, 16),
            "side=short": _flat(20, 30),
        }
        reports.append(_write(tmp_path, f"m{index}.txt", sections))
    result = run_diagnosis(
        "fam", reports, 480, "0.01", "base_grid", None,
        ("side=long", "side=short"), (),
    )
    codes = _codes(result)
    assert "HORIZON_DECAY" in codes
    assert "SINGLE_MARKET" in codes
    assert "DRIFT_ASYMMETRY" in codes
    rendered = render_diagnosis_report(result)
    # 구간 판정: 최단 셀 EDGE(180/120), 최장 셀 INVERSE(120/180)
    assert "horizon=100 t=0.01: EDGE" in rendered
    assert "horizon=1440 t=0.01: INVERSE" in rendered
    assert any("cell horizon=100" in e for e in result.edges_present)
    # 처방·서사 부재
    assert "next:" not in rendered
    assert "mirage" not in rendered
    assert "implication:" in rendered


def test_control_cohabitation_and_starvation(tmp_path):
    reports = [
        _write(
            tmp_path, f"c{index}.txt",
            {"base_grid": _flat(50, 50), "control_grid": _flat(50, 50)},
        )
        for index in range(3)
    ]
    result = run_diagnosis(
        "fam", reports, 480, "0.01", "base_grid", "control_grid",
        None, (),
    )
    assert "CONTROL_COHABITATION" in _codes(result)
    assert any(
        "control_grid@primary" in line
        for line in result.section_grade_lines
    )

    thin = [
        _write(tmp_path, f"t{index}.txt", {"base_grid": _flat(7, 6)})
        for index in range(3)
    ]
    result = run_diagnosis(
        "fam", thin, 480, "0.01", "base_grid", None, None, (),
    )
    assert "SAMPLE_STARVATION" in _codes(result)
    assert "THIN" in render_diagnosis_report(result)


def test_split_grades_noise_and_edge(tmp_path):
    reports = [
        _write(
            tmp_path, f"s{index}.txt",
            {
                "base_grid": _flat(100, 100),
                "regime=above": _flat(58, 42),
                "regime=below": _flat(42, 58),
                "vol=high": _flat(51, 49),
                "vol=low": _flat(49, 51),
                "align=aligned": _flat(15, 9),
                "align=anti": _flat(9, 15),
            },
        )
        for index in range(3)
    ]
    result = run_diagnosis(
        "fam", reports, 480, "0.01", "base_grid", None, None,
        (
            ("regime=above", "regime=below"),
            ("vol=high", "vol=low"),
            ("align=aligned", "align=anti"),
        ),
    )
    codes = _codes(result)
    assert "SPREAD_INSIDE_NOISE" in codes           # vol 쌍
    assert any("regime=above@primary" in e for e in result.edges_present)
    rendered = render_diagnosis_report(result)
    assert "align=aligned@primary: THIN" in rendered
    assert "regime=below@primary: INVERSE" in rendered


def test_flat_anatomy_and_errors(tmp_path):
    flat = [
        _write(tmp_path, f"f{index}.txt", {"base_grid": _flat(101, 99)})
        for index in range(3)
    ]
    result = run_diagnosis(
        "fam", flat, 480, "0.01", "base_grid", None, None, (),
    )
    assert "FLAT_ANATOMY" in _codes(result)
    assert result.edges_present == ()

    with pytest.raises(DomainValidationError):
        run_diagnosis(
            "fam", flat, 480, "0.01", "ghost_grid", None, None, (),
        )
    with pytest.raises(DomainValidationError):
        run_diagnosis(
            "fam", flat, 999, "0.01", "base_grid", None, None, (),
        )


def test_cli(tmp_path, capsys):
    from strategy_verifier import cli

    report = _write(
        tmp_path, "r.txt",
        {
            "base_grid": _flat(120, 80),
            "regime=above": _flat(70, 30),
            "regime=below": _flat(50, 50),
        },
    )
    exit_code = cli.main([
        "verify-diagnose", "--family", "fam",
        "--report", str(report), str(report), str(report),
        "--primary-horizon", "480", "--primary-threshold", "0.01",
        "--split", "regime=above:regime=below",
    ])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "cell_grades:" in captured.out
    assert "edges_present:" in captured.out
    assert "grade_key:" in captured.out

    assert cli.main([
        "verify-diagnose", "--family", "fam",
        "--report", str(report),
        "--primary-horizon", "480", "--primary-threshold", "0.01",
        "--side-arms", "onlyone",
    ]) == 1
