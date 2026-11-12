"""Verifier family lifecycle.

Every registered family carries a state file that records its life:
registered, optionally calibrated, explored, then frozen toward
confirmation or closed at exploration, ending confirmed or closed.
The machine refuses order violations with teaching messages, demands
a memo wherever the research chapter demanded one (freezes, closures,
confirmations), and treats terminal stages as permanent: a closed
family cannot advance, and a new look needs a new family name. Status
always names the allowed next moves and the suggested next command.

Research diagnostics only. No orders, no advice, no trading paths.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from strategy_verifier.domain.errors import DomainValidationError

STAGES = (
    "registered",
    "calibrated",
    "explored",
    "frozen",
    "confirmed",
    "closed",
)

TRANSITIONS: dict[str, tuple[str, ...]] = {
    "registered": ("calibrated", "explored"),
    "calibrated": ("explored",),
    "explored": ("frozen", "closed"),
    "frozen": ("confirmed", "closed"),
    "confirmed": (),
    "closed": (),
}

TERMINAL = ("confirmed", "closed")
_NOTE_REQUIRED = ("frozen", "confirmed", "closed")

_GUIDANCE = {
    "registered": (
        "run the covariate calibration on the exploration window if "
        "the engine has calibrated thresholds, otherwise advance "
        "straight to explored after the exploration run"
    ),
    "calibrated": (
        "run verify-run with the definition on the exploration window "
        "only, then advance to explored"
    ),
    "explored": (
        "freeze at most two arms with at least 100 resolved subjects "
        "each and advance to frozen, or close at exploration with the "
        "reason in the note"
    ),
    "frozen": (
        "download the confirmation window now and never earlier, run "
        "verify-run against the frozen definition, then advance to "
        "confirmed or closed by the registered bar"
    ),
    "confirmed": (
        "terminal: economics and robustness study proceed outside the "
        "verifier; this record stays"
    ),
    "closed": (
        "terminal and permanent: a new look needs a new family name "
        "and a new registration on disjoint windows"
    ),
}


@dataclass(frozen=True, slots=True)
class FamilyState:
    family: str
    stage: str
    transitions: tuple[str, ...]
    state_path: Path


def _state_path(state_dir: Path, family: str) -> Path:
    return state_dir / f"{family}.toml"


def _write_state(state: FamilyState) -> None:
    lines = [
        f'family = "{state.family}"',
        f'stage = "{state.stage}"',
        "transitions = [",
    ]
    for entry in state.transitions:
        lines.append('  "' + entry.replace('"', "'") + '",')
    lines.append("]")
    state.state_path.parent.mkdir(parents=True, exist_ok=True)
    state.state_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def initialize_family_state(state_dir: Path, family: str) -> FamilyState:
    if not isinstance(family, str) or not family.strip():
        raise DomainValidationError("family must be a non-empty string")
    path = _state_path(state_dir, family.strip())
    if path.exists():
        raise DomainValidationError(
            f"state exists and is immutable at birth: {path}; a new "
            "look needs a new family name"
        )
    state = FamilyState(
        family=family.strip(),
        stage="registered",
        transitions=(
            f"registered {date.today().isoformat()} created by "
            "verify-register",
        ),
        state_path=path,
    )
    _write_state(state)
    return state


def load_family_state(state_dir: Path, family: str) -> FamilyState:
    path = _state_path(state_dir, family)
    if not path.exists():
        raise DomainValidationError(f"no state for family: {family}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise DomainValidationError(f"invalid state toml: {exc}")
    stage = raw.get("stage")
    transitions = raw.get("transitions")
    if raw.get("family") != family or stage not in STAGES or not isinstance(
        transitions, list
    ):
        raise DomainValidationError(f"corrupt state file: {path}")
    return FamilyState(
        family=family,
        stage=stage,
        transitions=tuple(str(item) for item in transitions),
        state_path=path,
    )


def advance_family_state(
    state_dir: Path, family: str, to: str, note: str
) -> FamilyState:
    if to not in STAGES:
        raise DomainValidationError(
            f"unknown stage: {to}; stages are {', '.join(STAGES)}"
        )
    state = load_family_state(state_dir, family)
    if state.stage in TERMINAL:
        raise DomainValidationError(
            f"{state.stage} is terminal and permanent; a new look "
            "needs a new family name"
        )
    allowed = TRANSITIONS[state.stage]
    if to not in allowed:
        raise DomainValidationError(
            f"cannot advance {state.stage} to {to}; allowed next: "
            f"{', '.join(allowed)}"
        )
    if to in _NOTE_REQUIRED and (not isinstance(note, str) or not note.strip()):
        raise DomainValidationError(
            f"advancing to {to} requires a note: record what was "
            "frozen, or the closure or confirmation reason, exactly "
            "as the checkpoints always did"
        )
    entry = f"{to} {date.today().isoformat()} {note.strip()}".rstrip()
    advanced = FamilyState(
        family=state.family,
        stage=to,
        transitions=state.transitions + (entry,),
        state_path=state.state_path,
    )
    _write_state(advanced)
    return advanced


def list_family_states(state_dir: Path) -> tuple[FamilyState, ...]:
    if not state_dir.exists():
        return ()
    states = []
    for path in sorted(state_dir.glob("*.toml")):
        states.append(load_family_state(state_dir, path.stem))
    return tuple(states)


def render_family_status(state: FamilyState) -> str:
    if not isinstance(state, FamilyState):
        raise DomainValidationError("state must be FamilyState")
    allowed = TRANSITIONS[state.stage]
    lines = [
        "Verifier Family Status",
        "Research diagnostics only. Closed is permanent.",
        "",
        f"family: {state.family}",
        f"stage: {state.stage}",
        (
            "allowed_next: "
            + (", ".join(allowed) if allowed else "none (terminal)")
        ),
        f"guidance: {_GUIDANCE[state.stage]}",
        "history:",
    ]
    for entry in state.transitions:
        lines.append(f"- {entry}")
    lines.append("")
    return "\n".join(lines)
