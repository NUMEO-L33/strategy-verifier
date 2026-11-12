"""Verifier definition files.

A family is described in a TOML file with ``engine = "graph_v1"``: a
composable graph of series primitives, conditions, a direction rule, an
episode gate, and outcome bands (see docs/INTERPRETER_V1_SPEC.md). Any
technique expressible as when-plus-direction rides the same engine.

Research diagnostics only. No orders, no trading paths.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from strategy_verifier.domain.errors import DomainValidationError

SUPPORTED_ENGINES = ("graph_v1",)


def load_verifier_definition(path: Path):
    if not isinstance(path, Path):
        raise DomainValidationError("path must be a Path")
    if not path.exists():
        raise DomainValidationError(f"definition does not exist: {path}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise DomainValidationError(f"invalid definition toml: {exc}")
    if raw.get("engine") not in SUPPORTED_ENGINES:
        raise DomainValidationError(
            f"engine must be one of {SUPPORTED_ENGINES}"
        )
    from strategy_verifier.verifier.graph import load_graph_definition

    return load_graph_definition(raw, path)


def run_verifier_definition(
    definition,
    input_paths: list[Path],
    input_session_mode: str,
    calibrate_only: bool = False,
) -> str:
    from strategy_verifier.verifier.graph import (
        GraphDefinition,
        render_graph_report,
        run_graph_grouped,
    )

    if not isinstance(definition, GraphDefinition):
        raise DomainValidationError("definition must be a graph_v1 definition")
    return render_graph_report(
        run_graph_grouped(
            definition,
            input_paths=input_paths,
            input_session_mode=input_session_mode,
            calibrate_only=calibrate_only,
        )
    )
