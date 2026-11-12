"""Tests for state reading: markers match the engine's event set with
resolved annotations, the adjudication block is mandatory in both the
unadjudicated and closed forms, snapshots carry condition states, the
output is deterministic and free of instruction vocabulary, and
registered-engine definitions are refused."""

from pathlib import Path

import pytest

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.verifier import (
    advance_family_state,
    initialize_family_state,
    load_graph_definition,
    run_graph,
    run_reading,
    render_reading_report,
)

import tests.test_verifier_graph as graph_tests


@pytest.fixture(scope="module")
def reading_setup(tmp_path_factory):
    base = tmp_path_factory.mktemp("reading")
    capture = graph_tests._ohlc_capture(
        base, graph_tests._REVERSAL_ROWS, "read.jsonl"
    )
    raw = graph_tests._reversal_raw(["gate"])
    raw["family"] = "mini_read"
    definition = load_graph_definition(raw, Path("inline"))
    return capture, definition


def test_markers_match_engine_and_annotations(reading_setup, tmp_path):
    capture, definition = reading_setup
    engine = run_graph(
        definition, [capture], "single", calibrate_only=False
    )
    reading = run_reading(
        definition, [capture], "single", last_bars=2,
        state_dir=tmp_path / "state",
    )
    assert len(reading.run.reading_markers) == engine.event_count == 2
    outcomes = {
        marker["outcome"] for marker in reading.run.reading_markers
    }
    assert outcomes <= {"favorable", "adverse", "untouched"}
    sides = [marker["side"] for marker in reading.run.reading_markers]
    assert sides.count("long") == engine.long_event_count
    rendered = render_reading_report(reading)
    assert "occurrence_record: total=2" in rendered
    assert "adjudication: UNADJUDICATED" in rendered
    assert "latest_bars:" in rendered
    assert "conditions:" in rendered
    assert "direction=" in rendered


def test_adjudication_attachment_closed(reading_setup, tmp_path):
    capture, definition = reading_setup
    state_dir = tmp_path / "state"
    initialize_family_state(state_dir, "mini_read")
    advance_family_state(state_dir, "mini_read", "explored", "")
    advance_family_state(
        state_dir, "mini_read", "closed",
        "MINI_READ_NOT_CONFIRMED: every cell at the coin",
    )
    reading = run_reading(
        definition, [capture], "single", last_bars=1,
        state_dir=state_dir,
    )
    rendered = render_reading_report(reading)
    assert "adjudication: stage=closed" in rendered
    assert "MINI_READ_NOT_CONFIRMED" in rendered
    assert "permanently closed" in rendered


def test_deterministic_and_instruction_free(reading_setup, tmp_path):
    capture, definition = reading_setup
    kwargs = dict(
        input_paths=[capture], input_session_mode="single",
        last_bars=3, state_dir=tmp_path / "state",
    )
    first = render_reading_report(run_reading(definition, **kwargs))
    second = render_reading_report(run_reading(definition, **kwargs))
    assert first == second
    lowered = first.lower()
    for forbidden in (
        "entry", "target", "stop-loss", "stoploss", "recommend",
        "signal", "buy", "sell",
    ):
        assert forbidden not in lowered, forbidden
    assert "boundary: no instruction to act" in first


def test_registered_engine_refused_and_cli(reading_setup, tmp_path, capsys):
    from strategy_verifier import cli

    capture, definition = reading_setup
    squeeze = tmp_path / "legacy.toml"
    squeeze.write_text('family = "x"\nengine = "squeeze_play_trend_v1"\n', encoding="utf-8")
    with pytest.raises(DomainValidationError):
        run_reading(
            __import__(
                "strategy_verifier.verifier", fromlist=["x"]
            ).load_verifier_definition(squeeze),
            [capture], "single", 1, tmp_path / "state",
        )

    raw = graph_tests._reversal_raw(["gate"])
    raw["family"] = "mini_read"
    import tomllib  # noqa: F401  (문서화용)
    definition_path = tmp_path / "mini_read.toml"
    lines = ["family = \"mini_read\"", "engine = \"graph_v1\"", "",
             "[frame]", "bar_seconds = 900", "", "[params]",
             'k = "1"', "",
             ]
    for spec in raw["series"]:
        lines += ["[[series]]"] + [
            (
                f'{key} = "{value}"' if isinstance(value, str)
                else f"{key} = {value}"
            )
            for key, value in spec.items()
        ] + [""]
    for spec in raw["conditions"]:
        lines += ["[[conditions]]"]
        for key, value in spec.items():
            if isinstance(value, str):
                lines.append(f'{key} = "{value}"')
            elif isinstance(value, list):
                inner = ", ".join(f'"{item}"' for item in value)
                lines.append(f"{key} = [{inner}]")
            else:
                lines.append(f"{key} = {value}")
        lines.append("")
    lines += [
        "[direction]",
        'long_when = ["close < slow", "slow < slow_prior"]',
        'short_when = ["close > slow", "slow > slow_prior"]',
        "",
        "[event]",
        'when = ["gate"]',
        "gate_bars = 2",
        "",
        "[outcomes]",
        "horizons = [8]",
        'threshold_pcts = ["0.004"]',
    ]
    definition_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    exit_code = cli.main([
        "verify-read", "--definition", str(definition_path),
        "--input", str(capture),
        "--input-session-mode", "single",
        "--state-dir", str(tmp_path / "state"),
    ])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "State Reading Report" in captured.out
    assert "adjudication:" in captured.out
    assert "equation:" in captured.out
