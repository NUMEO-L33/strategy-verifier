"""Verifier state reading (charter articles two and three).

Reads the market with the verdict attached: the occurrence points of
the user's own registered rule, each annotated with its resolved
race outcome at the annotation cell; the latest bars' condition
states; and, always and unconditionally, the family's adjudication —
its lifecycle stage and closing note, or an UNADJUDICATED banner when
the family has never entered the pipeline. Description only: the
output contains no instruction to act, no target, no stop, and no
recommendation, and the words for those things do not appear in it.
Batch and deterministic: the same market, technique, and order
reproduce the same reading, and the equation stamp rides every
report. Realtime streams are version two, gated on the recorded
attorney review.

Research diagnostics only. No orders, no advice, no trading paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.verifier.graph import (
    GraphDefinition,
    GraphRunResult,
    run_graph,
)
from strategy_verifier.verifier.lifecycle import load_family_state


@dataclass(frozen=True, slots=True)
class ReadingResult:
    definition: GraphDefinition
    run: GraphRunResult
    annotate_horizon: int
    annotate_threshold: str
    adjudication_lines: tuple[str, ...]


def _adjudication(state_dir: Path, family: str) -> tuple[str, ...]:
    try:
        state = load_family_state(state_dir, family)
    except DomainValidationError:
        return (
            "adjudication: UNADJUDICATED",
            "- this family has never entered the pipeline; its "
            "occurrence points carry no record at any bar",
            "- run verify-triage and verify-register before trusting "
            "any reading of this rule",
        )
    lines = [f"adjudication: stage={state.stage}"]
    if state.transitions:
        lines.append(f"- last: {state.transitions[-1]}")
    if state.stage == "closed":
        lines.append(
            "- this family is permanently closed; the occurrences "
            "below belong to a rule that did not clear its "
            "registered bar"
        )
    elif state.stage == "confirmed":
        lines.append(
            "- confirmed means the registered bar was cleared on a "
            "past window; it is not a statement about any future bar"
        )
    else:
        lines.append(
            "- no verdict exists yet; occurrences below are "
            "unjudged observations"
        )
    return tuple(lines)


def run_reading(
    definition,
    input_paths: list[Path],
    input_session_mode: str,
    last_bars: int,
    state_dir: Path,
) -> ReadingResult:
    if not isinstance(definition, GraphDefinition):
        raise DomainValidationError(
            "state reading supports graph_v1 definitions in version "
            "one; registered-engine families are measured with "
            "verify-run and judged with verify-verdict"
        )
    if not isinstance(last_bars, int) or last_bars <= 0:
        raise DomainValidationError("last-bars must be a positive integer")
    run = run_graph(
        definition,
        input_paths=input_paths,
        input_session_mode=input_session_mode,
        calibrate_only=False,
        collect_reading=True,
        reading_last_bars=last_bars,
    )
    annotate_horizon = definition.horizons[len(definition.horizons) // 2]
    annotate_threshold = definition.threshold_pcts[
        len(definition.threshold_pcts) // 2
    ]
    return ReadingResult(
        definition=definition,
        run=run,
        annotate_horizon=annotate_horizon,
        annotate_threshold=str(annotate_threshold),
        adjudication_lines=_adjudication(state_dir, definition.family),
    )


def render_reading_report(result: ReadingResult) -> str:
    if not isinstance(result, ReadingResult):
        raise DomainValidationError("result must be ReadingResult")
    run = result.run
    lines = [
        "State Reading Report",
        "Description only. This output states what a user-defined "
        "rule did and does on the given data; it instructs nothing.",
        "The adjudication block below is mandatory and cannot be "
        "removed. Research diagnostics only. No orders, no advice, "
        "no trading paths.",
        "",
        f"family: {result.definition.family}",
    ]
    lines.extend(result.adjudication_lines)
    lines.append(
        "occurrences (outcome at the annotation cell horizon="
        f"{result.annotate_horizon} threshold="
        f"{result.annotate_threshold}):"
    )
    if not run.reading_markers:
        lines.append("- none on this data")
    favorable = adverse = untouched = 0
    for marker in run.reading_markers:
        outcome = marker["outcome"]
        if outcome == "favorable":
            favorable += 1
        elif outcome == "adverse":
            adverse += 1
        else:
            untouched += 1
        lines.append(
            f"- {marker['timestamp']} {marker['side']} "
            f"price={marker['price']} outcome={outcome}"
        )
    resolved = favorable + adverse
    share_text = (
        f"{favorable / resolved:.4f}" if resolved else "none"
    )
    lines.append(
        f"occurrence_record: total={len(run.reading_markers)} "
        f"favorable={favorable} adverse={adverse} "
        f"untouched={untouched} share={share_text}"
    )
    lines.append("latest_bars:")
    if not run.reading_last_bars:
        lines.append("- none")
    for snapshot in run.reading_last_bars:
        condition_text = " ".join(
            f"{name}={'yes' if held else 'no'}"
            for name, held in snapshot["conditions"].items()
        ) or "none"
        series_text = " ".join(
            f"{name}={value}"
            for name, value in snapshot["series"].items()
        ) or "none"
        lines.append(
            f"- {snapshot['timestamp']} close={snapshot['close']} "
            f"direction={snapshot['direction']} occurred="
            f"{'yes' if snapshot['occurred'] else 'no'}"
        )
        lines.append(f"  conditions: {condition_text}")
        lines.append(f"  series: {series_text}")
    lines.append(
        "boundary: no instruction to act exists in this report; "
        "whether to act on anything above is solely the reader's own "
        "judgement, and past outcomes state nothing about future bars"
    )
    lines.append("")
    return "\n".join(lines)
