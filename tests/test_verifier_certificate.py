"""Tests for the certificate: it folds real verdict and diagnosis
outputs into one HTML sheet carrying the verdict, criteria, markets,
grades, equation hashes, and the boundary text; it stays free of
instruction vocabulary; missing inputs and missing VERDICT lines are
refused."""


import pytest

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.verifier import render_certificate


def _report(base_f, base_a) -> str:
    return (
        "base_grid (favorable/adverse/untouched share):\n"
        f"- horizon=480 0.01: {base_f}/{base_a}/0 (0.5)\n"
    )


def _make_outputs(tmp_path, capsys):
    from strategy_verifier import cli

    reports = []
    for index, (f, a) in enumerate([(70, 30), (66, 34), (61, 39)]):
        path = tmp_path / f"m{index}.txt"
        path.write_text(_report(f, a), encoding="utf-8")
        reports.append(str(path))

    assert cli.main([
        "verify-verdict", "--family", "cert_fam",
        "--report", *reports,
        "--primary-horizon", "480", "--primary-threshold", "0.01",
    ]) == 0
    verdict_path = tmp_path / "verdict.txt"
    verdict_path.write_text(capsys.readouterr().out, encoding="utf-8")

    assert cli.main([
        "verify-diagnose", "--family", "cert_fam",
        "--report", *reports,
        "--primary-horizon", "480", "--primary-threshold", "0.01",
    ]) == 0
    diagnosis_path = tmp_path / "diagnosis.txt"
    diagnosis_path.write_text(capsys.readouterr().out, encoding="utf-8")
    return verdict_path, diagnosis_path


def test_certificate_folds_reports(tmp_path, capsys):
    from strategy_verifier import cli

    verdict_path, diagnosis_path = _make_outputs(tmp_path, capsys)
    output = tmp_path / "cert.html"
    assert cli.main([
        "verify-certificate", "--family", "cert_fam",
        "--verdict-report", str(verdict_path),
        "--diagnosis-report", str(diagnosis_path),
        "--output", str(output),
    ]) == 0
    assert "Certificate written" in capsys.readouterr().out

    document = output.read_text(encoding="utf-8")
    assert "검증 감정서" in document
    assert "CERT_FAM_CONFIRMED" in document
    assert "판정 기준" in document
    assert "market_combined:" in document
    assert "verdict result:" in document
    assert "docs/DISCLAIMER.md" in document
    lowered = document.lower()
    for forbidden in ("entry", "recommend", "buy", "sell", "signal"):
        assert forbidden not in lowered, forbidden


def test_refusals(tmp_path, capsys):
    from strategy_verifier import cli

    verdict_path, diagnosis_path = _make_outputs(tmp_path, capsys)
    no_verdict = tmp_path / "empty.txt"
    no_verdict.write_text("nothing here\n", encoding="utf-8")

    with pytest.raises(DomainValidationError):
        render_certificate(
            "x", "no verdict line", "diag", tmp_path / "x.html"
        )
    assert cli.main([
        "verify-certificate", "--family", "x",
        "--verdict-report", str(no_verdict),
        "--diagnosis-report", str(diagnosis_path),
        "--output", str(tmp_path / "y.html"),
    ]) == 1
    assert cli.main([
        "verify-certificate", "--family", "x",
        "--verdict-report", str(tmp_path / "ghost.txt"),
        "--diagnosis-report", str(diagnosis_path),
        "--output", str(tmp_path / "z.html"),
    ]) == 1
