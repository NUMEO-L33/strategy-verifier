"""Tests for the claim triage refusal engine.

Every refusal ground gets a fixture: missing family, missing or
discretionary direction, missing or unavailable data, missing or
discretionary conditions; reasons accumulate rather than stopping at
the first; schema breakage raises; the CLI signals admissible, refused,
and error through its exit codes.
"""

from pathlib import Path

import pytest

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.verifier import (
    render_claim_triage_report,
    run_claim_triage,
)

_EXAMPLE = Path("definitions/claims/example_squeeze_claim.toml")


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "claim.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_example_claim_is_admissible():
    result = run_claim_triage(_EXAMPLE)
    assert result.admissible is True
    assert result.reasons == ()
    assert result.condition_count == 3
    rendered = render_claim_triage_report(result)
    assert "admissible: yes" in rendered
    assert "- none" in rendered


def test_each_refusal_ground_fires(tmp_path):
    cases = {
        "MISSING_FAMILY": '''
[claim]
direction = "long above the average"
data = ["trades"]
conditions = ["close above the average"]
''',
        "MISSING_DIRECTION": '''
[claim]
family = "x"
data = ["trades"]
conditions = ["close above the average"]
''',
        "MISSING_DATA": '''
[claim]
family = "x"
direction = "long above the average"
conditions = ["close above the average"]
''',
        "UNAVAILABLE_DATA": '''
[claim]
family = "x"
direction = "long above the average"
data = ["orderbook"]
conditions = ["close above the average"]
''',
        "MISSING_CONDITIONS": '''
[claim]
family = "x"
direction = "long above the average"
data = ["trades"]
conditions = []
''',
    }
    for code, body in cases.items():
        result = run_claim_triage(_write(tmp_path, body))
        assert result.admissible is False, code
        assert any(reason.code == code for reason in result.reasons), code

    discretionary = run_claim_triage(_write(tmp_path, '''
[claim]
family = "x"
direction = "느낌이 좋을 때 롱"
data = ["trades"]
conditions = ["눌림이 예쁘게 나오면 진입", "close above the average"]
'''))
    assert discretionary.admissible is False
    hidden = [
        reason for reason in discretionary.reasons
        if reason.code == "HIDDEN_DISCRETION"
    ]
    assert len(hidden) == 2
    assert {reason.field for reason in hidden} == {
        "direction", "conditions[0]"
    }
    rendered = render_claim_triage_report(discretionary)
    assert "guidance:" in rendered
    assert "admissible: no" in rendered


def test_reasons_accumulate(tmp_path):
    result = run_claim_triage(_write(tmp_path, '''
[claim]
family = ""
data = ["orderbook", "onchain"]
conditions = ["재량껏 판단"]
'''))
    codes = sorted(reason.code for reason in result.reasons)
    assert codes == [
        "HIDDEN_DISCRETION",
        "MISSING_DIRECTION",
        "MISSING_FAMILY",
        "UNAVAILABLE_DATA",
        "UNAVAILABLE_DATA",
    ]


def test_schema_breakage_raises(tmp_path):
    with pytest.raises(DomainValidationError):
        run_claim_triage(_write(tmp_path, 'family = "x"\n'))
    with pytest.raises(DomainValidationError):
        run_claim_triage(_write(tmp_path, '''
[claim]
family = "x"
rogue = 1
'''))
    with pytest.raises(DomainValidationError):
        run_claim_triage(tmp_path / "missing.toml")


def test_cli_exit_codes(tmp_path, capsys):
    from strategy_verifier import cli

    assert cli.main(["verify-triage", "--claim", str(_EXAMPLE)]) == 0
    assert "admissible: yes" in capsys.readouterr().out

    refused = _write(tmp_path, '''
[claim]
family = "x"
direction = "감으로 판단"
data = ["trades"]
conditions = ["close above the average"]
''')
    assert cli.main(["verify-triage", "--claim", str(refused)]) == 2
    assert "HIDDEN_DISCRETION" in capsys.readouterr().out

    assert cli.main(
        ["verify-triage", "--claim", str(tmp_path / "nope.toml")]
    ) == 1
    assert "error:" in capsys.readouterr().err
