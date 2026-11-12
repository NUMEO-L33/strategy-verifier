"""Tests for the registration wizard: dual artifact generation,
window disjointness, primary-cell membership, immutability, refusal
pass-through, and CLI exit codes."""

from pathlib import Path

import pytest

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.verifier import (
    load_verifier_definition,
    render_registration_report,
    run_registration,
)

_GOOD = '''
[claim]
family = "ma_touch_trend"
direction = "Long when the close exceeds the slow average with the slow average rising; short on the mirror."
data = ["klines"]
conditions = ["Close within delta of the fast average inside the trend."]

[registration]
engine = "graph_v1"
definition = "definitions/ma_touch_trend.toml"
explore = "2026-06-01..2026-07-23"
confirm = "2025-09-01..2025-11-30"
'''


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "reg.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_registration_writes_both_artifacts(tmp_path):
    triage, registration = run_registration(
        _write(tmp_path, _GOOD),
        definitions_dir=tmp_path / "defs",
        docs_dir=tmp_path / "docs",
        state_dir=tmp_path / "state",
    )
    assert triage.admissible is True
    from strategy_verifier.verifier import load_family_state

    assert load_family_state(
        tmp_path / "state", "ma_touch_trend"
    ).stage == "registered"
    assert registration is not None
    definition = load_verifier_definition(registration.definition_path)
    assert definition.family == "ma_touch_trend"
    document = registration.document_path.read_text(encoding="utf-8")
    assert "MA_TOUCH_TREND_NOT_CONFIRMED" in document
    assert "2025-09-01..2025-11-30" in document
    assert "disjoint by construction" in document
    report = render_registration_report(triage, registration)
    assert "sha256[:16]=" in report
    assert registration.verdict_name == "MA_TOUCH_TREND_NOT_CONFIRMED"


def test_refused_claim_writes_nothing(tmp_path):
    body = _GOOD.replace(
        'direction = "Long when the close exceeds the slow average with the slow average rising; short on the mirror."',
        'direction = "느낌 좋을 때 롱"',
    )
    triage, registration = run_registration(
        _write(tmp_path, body),
        definitions_dir=tmp_path / "defs",
        docs_dir=tmp_path / "docs",
    )
    assert registration is None
    assert triage.admissible is False
    assert not (tmp_path / "defs").exists()
    assert "HIDDEN_DISCRETION" in render_registration_report(triage, None)


def test_structural_rejections(tmp_path):
    overlap = _GOOD.replace(
        'confirm = "2025-09-01..2025-11-30"',
        'confirm = "2026-07-01..2026-09-30"',
    )
    with pytest.raises(DomainValidationError):
        run_registration(
            _write(tmp_path, overlap),
            definitions_dir=tmp_path / "d1",
            docs_dir=tmp_path / "o1",
        )
    bad_cell = _GOOD.replace(
        'definition = "definitions/ma_touch_trend.toml"',
        'definition = "definitions/does_not_exist.toml"',
    )
    with pytest.raises(DomainValidationError):
        run_registration(
            _write(tmp_path, bad_cell),
            definitions_dir=tmp_path / "d2",
            docs_dir=tmp_path / "o2",
        )
    bad_window = _GOOD.replace(
        'explore = "2026-06-01..2026-07-23"',
        'explore = "2026-07-23..2026-06-01"',
    )
    with pytest.raises(DomainValidationError):
        run_registration(
            _write(tmp_path, bad_window),
            definitions_dir=tmp_path / "d3",
            docs_dir=tmp_path / "o3",
        )


def test_registrations_are_immutable(tmp_path):
    path = _write(tmp_path, _GOOD)
    kwargs = dict(
        definitions_dir=tmp_path / "defs", docs_dir=tmp_path / "docs"
    )
    _, first = run_registration(path, **kwargs)
    before = first.definition_path.read_text(encoding="utf-8")
    with pytest.raises(DomainValidationError):
        run_registration(path, **kwargs)
    assert first.definition_path.read_text(encoding="utf-8") == before


def test_cli_exit_codes(tmp_path, capsys):
    from strategy_verifier import cli

    good = _write(tmp_path, _GOOD)
    assert cli.main([
        "verify-register", "--file", str(good),
        "--definitions-dir", str(tmp_path / "defs"),
        "--docs-dir", str(tmp_path / "docs"),
        "--state-dir", str(tmp_path / "state"),
    ]) == 0
    assert "Verifier Registration Report" in capsys.readouterr().out

    refused = tmp_path / "refused.toml"
    refused.write_text(
        _GOOD.replace('data = ["klines"]', 'data = ["orderbook"]'),
        encoding="utf-8",
    )
    assert cli.main([
        "verify-register", "--file", str(refused),
        "--definitions-dir", str(tmp_path / "d2"),
        "--docs-dir", str(tmp_path / "o2"),
        "--state-dir", str(tmp_path / "s2"),
    ]) == 2
    assert "UNAVAILABLE_DATA" in capsys.readouterr().out

    assert cli.main([
        "verify-register", "--file", str(tmp_path / "missing.toml"),
    ]) == 1
    assert "error:" in capsys.readouterr().err


def test_graph_registration_references_existing_definition(tmp_path):
    body = '''
[claim]
family = "ma_touch_trend"
direction = "Long when the close exceeds the slow average with the slow average rising; short on the mirror."
data = ["klines"]
conditions = ["Close within delta of the fast average inside the trend."]

[registration]
engine = "graph_v1"
definition = "definitions/ma_touch_trend.toml"
explore = "2024-09-01..2026-06-30"
confirm = "2022-07-01..2024-08-31"
'''
    triage, registration = run_registration(
        _write(tmp_path, body),
        definitions_dir=tmp_path / "defs",
        docs_dir=tmp_path / "docs",
        state_dir=tmp_path / "state",
    )
    assert registration is not None
    assert registration.engine == "graph_v1"
    document = registration.document_path.read_text(encoding="utf-8")
    assert "Definition file sha256:" in document
    assert "referenced, not generated" in document
    from strategy_verifier.verifier import load_family_state

    assert load_family_state(
        tmp_path / "state", "ma_touch_trend"
    ).stage == "registered"

    mismatch = body.replace(
        'family = "ma_touch_trend"', 'family = "other_name"'
    )
    with pytest.raises(DomainValidationError):
        run_registration(
            _write(tmp_path, mismatch),
            definitions_dir=tmp_path / "d2",
            docs_dir=tmp_path / "o2",
            state_dir=tmp_path / "s2",
        )
