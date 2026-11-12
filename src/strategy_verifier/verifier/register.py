"""Verifier registration wizard.

A registration file carries a [claim] and a [registration]. The claim
is triaged first; refusal stops everything with the triage report. An
admitted claim is then pinned: the wizard writes the definition file
and the registration document together, before any data, enforcing
the research chapter's discipline as code — the exploration and
confirmation windows must be disjoint calendar ranges, the primary
cell must be a member of the registered grid, and registrations are
immutable, so an existing family name refuses to be overwritten and a
new look needs a new name. Both artifacts are reported with content
hashes.

Research diagnostics only. No orders, no advice, no trading paths.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.verifier.lifecycle import initialize_family_state
from strategy_verifier.verifier.triage import (
    ClaimTriageResult,
    render_claim_triage_report,
    triage_claim_table,
)

_REGISTRATION_KEYS = {
    "engine", "explore", "confirm", "markets", "bars_note",
    "params", "outcomes",
}
_DEFAULT_MARKETS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


@dataclass(frozen=True, slots=True)
class WindowRange:
    start: date
    end: date

    def text(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    family: str
    engine: str
    definition_path: Path
    document_path: Path
    definition_sha256: str
    document_sha256: str
    explore: WindowRange
    confirm: WindowRange
    verdict_name: str


def _parse_window(value, name: str) -> WindowRange:
    if not isinstance(value, str) or ".." not in value:
        raise DomainValidationError(
            f"{name} must look like 2026-06-01..2026-07-23"
        )
    start_text, end_text = value.split("..", 1)
    try:
        start = date.fromisoformat(start_text.strip())
        end = date.fromisoformat(end_text.strip())
    except ValueError:
        raise DomainValidationError(f"{name} carries an invalid date")
    if start >= end:
        raise DomainValidationError(f"{name} must start before it ends")
    return WindowRange(start=start, end=end)


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return '"' + value.replace('"', '\\"') + '"'
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise DomainValidationError(
        f"unsupported registration value type: {type(value).__name__}"
    )


def run_registration(
    path: Path,
    definitions_dir: Path,
    docs_dir: Path,
    state_dir: Path | None = None,
) -> tuple[ClaimTriageResult, RegistrationResult | None]:
    if not isinstance(path, Path):
        raise DomainValidationError("path must be a Path")
    if not path.exists():
        raise DomainValidationError(f"registration does not exist: {path}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise DomainValidationError(f"invalid registration toml: {exc}")
    if set(raw) != {"claim", "registration"}:
        raise DomainValidationError(
            "a registration file must contain exactly [claim] and "
            "[registration]"
        )

    triage = triage_claim_table(raw["claim"], path)
    if not triage.admissible:
        return triage, None

    registration = raw["registration"]
    if not isinstance(registration, dict):
        raise DomainValidationError("[registration] must be a table")
    unknown = set(registration) - _REGISTRATION_KEYS - {"definition"}
    if unknown:
        raise DomainValidationError(
            f"unknown registration keys: {sorted(unknown)}"
        )
    engine = registration.get("engine")
    if engine == "graph_v1":
        return _register_graph(
            raw, path, registration, triage,
            definitions_dir=definitions_dir,
            docs_dir=docs_dir,
            state_dir=state_dir,
        )
    raise DomainValidationError("engine must be graph_v1")


def _register_graph(
    raw: dict,
    path: Path,
    registration: dict,
    triage: ClaimTriageResult,
    *,
    definitions_dir: Path,
    docs_dir: Path,
    state_dir: Path | None,
) -> tuple[ClaimTriageResult, RegistrationResult]:
    """Register a graph family: the definition file already exists and
    is referenced, not generated; the wizard pins the claim, windows,
    the definition digest, the document, and the state birth."""

    allowed = {"engine", "explore", "confirm", "markets", "bars_note",
               "definition"}
    unknown = set(registration) - allowed
    if unknown:
        raise DomainValidationError(
            f"graph registration allows {sorted(allowed)}; unknown: "
            f"{sorted(unknown)}"
        )
    definition_ref = registration.get("definition")
    if not isinstance(definition_ref, str) or not definition_ref.strip():
        raise DomainValidationError(
            "graph registration requires definition = "
            '"definitions/<family>.toml"'
        )
    definition_path = Path(definition_ref)
    if not definition_path.exists():
        raise DomainValidationError(
            f"referenced definition does not exist: {definition_path}"
        )
    from strategy_verifier.verifier.definition import load_verifier_definition
    from strategy_verifier.verifier.graph import GraphDefinition

    graph = load_verifier_definition(definition_path)
    if not isinstance(graph, GraphDefinition):
        raise DomainValidationError(
            "the referenced file is not a graph_v1 definition"
        )
    if graph.family != triage.family:
        raise DomainValidationError(
            f"claim family {triage.family} does not match definition "
            f"family {graph.family}"
        )
    explore = _parse_window(registration.get("explore"), "explore")
    confirm = _parse_window(registration.get("confirm"), "confirm")
    if explore.start <= confirm.end and confirm.start <= explore.end:
        raise DomainValidationError(
            "explore and confirm windows overlap; the two-stage split "
            "requires disjoint calendar ranges"
        )
    markets = registration.get("markets", list(_DEFAULT_MARKETS))
    if not isinstance(markets, list) or not markets or not all(
        isinstance(market, str) and market.strip() for market in markets
    ):
        raise DomainValidationError("markets must be non-empty strings")
    bars_note = registration.get("bars_note", "")
    if not isinstance(bars_note, str):
        raise DomainValidationError("bars_note must be a string")

    family = triage.family
    document_path = docs_dir / f"{family.upper()}_REGISTRATION.md"
    state_path = (
        (state_dir / f"{family}.toml") if state_dir is not None else None
    )
    existing_paths = [document_path]
    if state_path is not None:
        existing_paths.append(state_path)
    for existing in existing_paths:
        if existing.exists():
            raise DomainValidationError(
                f"registration exists and is immutable: {existing}; "
                "a new look needs a new family name"
            )

    definition_text = definition_path.read_text(encoding="utf-8")
    import hashlib as _hashlib

    definition_sha_full = _hashlib.sha256(
        definition_text.encode("utf-8")
    ).hexdigest()
    verdict_name = f"{family.upper()}_NOT_CONFIRMED"
    primary_horizon = graph.horizons[len(graph.horizons) // 2]
    primary_threshold = graph.threshold_pcts[
        len(graph.threshold_pcts) // 2
    ]
    condition_lines = "\n".join(
        f"{index + 1}. {condition}"
        for index, condition in enumerate(
            raw["claim"].get("conditions", [])
        )
    )
    document_text = f"""# {family} Registration (Two-Stage, Pre-Registered)

Generated by verify-register from `{path.name}` before any
measurement. This document is immutable; a new look needs a new
family name. The definition file is referenced, not generated, and
its digest below is the binding technique.

## Claim (Admitted By Triage)

Direction: {raw['claim'].get('direction', '').strip()}

Data: {', '.join(triage.data)}

Conditions:
{condition_lines}

## Engine And Frozen Technique

Engine `graph_v1` on markets {', '.join(markets)}.

Definition file: `{definition_path}`

Definition file sha256: {definition_sha_full}

Outcomes: horizons {list(graph.horizons)}, thresholds
{[str(t) for t in graph.threshold_pcts]}, primary cell
{primary_horizon} at {primary_threshold} (the middle cell by
convention; state a different primary in bars_note before any
measurement if the claim demands one).

## Windows

Exploration {explore.text()}; confirmation {confirm.text()},
disjoint by construction and untouched until the freeze.

## Bar And Verdict

At the primary cell, a frozen arm passes only if its pooled favorable
share's 95 percent Wilson interval excludes one half on the favorable
side (or separates above its registered control arm where the engine
defines one) and its per-market share exceeds one half on at least
two of three markets. At most two arms may be frozen for confirmation,
each with at least 100 resolved exploration subjects. Anything short
closes the family as `{verdict_name}` with no redefinitions.
{('' if not bars_note else chr(10) + bars_note + chr(10))}
## Boundaries

Research diagnostics only. No strategy code changes, no engine wiring
from any output, no exchange clients, API keys, orders, live trading,
or DCA automation.
"""
    docs_dir.mkdir(parents=True, exist_ok=True)
    document_path.write_text(document_text, encoding="utf-8")
    if state_dir is not None:
        initialize_family_state(state_dir, family)

    return triage, RegistrationResult(
        family=family,
        engine="graph_v1",
        definition_path=definition_path,
        document_path=document_path,
        definition_sha256=definition_sha_full[:16],
        document_sha256=_hashlib.sha256(
            document_text.encode("utf-8")
        ).hexdigest()[:16],
        explore=explore,
        confirm=confirm,
        verdict_name=verdict_name,
    )


def render_registration_report(
    triage: ClaimTriageResult,
    registration: RegistrationResult | None,
) -> str:
    if registration is None:
        return render_claim_triage_report(triage)
    lines = [
        "Verifier Registration Report",
        "Research diagnostics only. Registrations are immutable.",
        "No API keys, private API, orders, account access, or trading.",
        "",
        f"family: {registration.family}",
        f"engine: {registration.engine}",
        f"explore: {registration.explore.text()}",
        f"confirm: {registration.confirm.text()}",
        f"verdict_on_failure: {registration.verdict_name}",
        (
            f"definition: {registration.definition_path} "
            f"sha256[:16]={registration.definition_sha256}"
        ),
        (
            f"document: {registration.document_path} "
            f"sha256[:16]={registration.document_sha256}"
        ),
        "state: initialized at registered when a state directory is "
        "supplied",
        "next_steps: run verify-run with this definition on the "
        "exploration window only; the confirmation window stays "
        "untouched until the freeze",
        "",
    ]
    return "\n".join(lines)
