"""Tests for the family lifecycle state machine: the full happy path,
skip-calibration path, every refusal (order violations, missing
memos, terminal permanence), status rendering, and CLI exit codes."""


import pytest

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.verifier import (
    advance_family_state,
    initialize_family_state,
    list_family_states,
    load_family_state,
    render_family_status,
)


def test_full_happy_path_and_history(tmp_path):
    initialize_family_state(tmp_path, "pilot")
    advance_family_state(tmp_path, "pilot", "calibrated", "")
    advance_family_state(tmp_path, "pilot", "explored", "")
    advance_family_state(
        tmp_path, "pilot", "frozen", "H-a and H-b, 120 and 140 resolved"
    )
    state = advance_family_state(
        tmp_path, "pilot", "confirmed", "bar cleared at the primary cell"
    )
    assert state.stage == "confirmed"
    assert len(state.transitions) == 5
    reloaded = load_family_state(tmp_path, "pilot")
    assert reloaded.stage == "confirmed"
    rendered = render_family_status(reloaded)
    assert "allowed_next: none (terminal)" in rendered
    assert "H-a and H-b" in rendered


def test_skip_calibration_and_exploration_closure(tmp_path):
    initialize_family_state(tmp_path, "nocov")
    advance_family_state(tmp_path, "nocov", "explored", "")
    state = advance_family_state(
        tmp_path, "nocov", "closed", "every arm below the coin"
    )
    assert state.stage == "closed"
    rendered = render_family_status(state)
    assert "permanent" in rendered


def test_refusals(tmp_path):
    initialize_family_state(tmp_path, "x")
    with pytest.raises(DomainValidationError):
        advance_family_state(tmp_path, "x", "frozen", "memo")  # 건너뛰기
    with pytest.raises(DomainValidationError):
        advance_family_state(tmp_path, "x", "sideways", "")  # 미지 단계
    advance_family_state(tmp_path, "x", "explored", "")
    with pytest.raises(DomainValidationError):
        advance_family_state(tmp_path, "x", "confirmed", "memo")  # 동결 생략
    with pytest.raises(DomainValidationError):
        advance_family_state(tmp_path, "x", "frozen", "")  # 메모 누락
    advance_family_state(tmp_path, "x", "closed", "reason")
    with pytest.raises(DomainValidationError):
        advance_family_state(tmp_path, "x", "explored", "")  # 종결 영구
    with pytest.raises(DomainValidationError):
        initialize_family_state(tmp_path, "x")  # 재탄생 거부
    with pytest.raises(DomainValidationError):
        load_family_state(tmp_path, "ghost")


def test_list_and_cli(tmp_path, capsys):
    from strategy_verifier import cli

    initialize_family_state(tmp_path, "a")
    initialize_family_state(tmp_path, "b")
    assert len(list_family_states(tmp_path)) == 2

    assert cli.main([
        "verify-advance", "--family", "a", "--to", "explored",
        "--state-dir", str(tmp_path),
    ]) == 0
    assert "stage: explored" in capsys.readouterr().out

    assert cli.main([
        "verify-advance", "--family", "a", "--to", "confirmed",
        "--note", "x", "--state-dir", str(tmp_path),
    ]) == 1
    assert "allowed next" in capsys.readouterr().err

    assert cli.main([
        "verify-status", "--all", "--state-dir", str(tmp_path),
    ]) == 0
    out = capsys.readouterr().out
    assert "family: a" in out and "family: b" in out

    assert cli.main(["verify-status", "--state-dir", str(tmp_path)]) == 1
