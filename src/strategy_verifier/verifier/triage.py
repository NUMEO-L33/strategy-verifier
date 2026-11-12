"""Verifier claim triage (the refusal engine).

The product's front door: a user describes a technique as a claim
file, and the triage either admits it toward registration or refuses
with named reasons and teaching guidance. Refusal is a first-class
output, not an error: the three refusal grounds from the interpreter
spec are checked mechanically — a missing or discretionary direction
rule, references to data outside the pipeline, and discretion hidden
inside condition prose. The discretion screen is a lexical heuristic
and says so in the report; the registration stage remains the binding
contract.

Research diagnostics only. No orders, no advice, no trading paths.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from strategy_verifier.domain.errors import DomainValidationError

AVAILABLE_DATA = (
    "trades",
    "klines",
    "daily_klines",
    "funding",
    "open_interest",
)

_DATA_GUIDANCE = (
    "available sources: trades (tick prices, quantities, sides), "
    "klines (any-interval bars with volume), daily_klines, funding, "
    "open_interest; order books, on-chain flows, news, and proprietary "
    "indicators are outside the current pipeline"
)

_DISCRETION_MARKERS = (
    "느낌",
    "감으로",
    "직감",
    "눈치",
    "재량",
    "적절히",
    "알아서",
    "상황에 따라",
    "케이스 바이",
    "보고 판단",
    "예쁘",
    "좋아 보이",
    "괜찮아 보이",
    "애매하면",
    "내공",
    "discretion",
    "gut feel",
    "as needed",
    "appropriately",
    "depends on the situation",
    "when it looks",
)

_CLAIM_KEYS = {"family", "summary", "direction", "data", "conditions"}


@dataclass(frozen=True, slots=True)
class TriageReason:
    code: str
    field: str
    detail: str
    guidance: str


@dataclass(frozen=True, slots=True)
class ClaimTriageResult:
    claim_path: Path
    family: str
    summary: str
    data: tuple[str, ...]
    condition_count: int
    admissible: bool
    reasons: tuple[TriageReason, ...]


def _find_marker(text: str) -> str | None:
    lowered = text.lower()
    for marker in _DISCRETION_MARKERS:
        if marker in lowered:
            return marker
    return None


def run_claim_triage(path: Path) -> ClaimTriageResult:
    if not isinstance(path, Path):
        raise DomainValidationError("path must be a Path")
    if not path.exists():
        raise DomainValidationError(f"claim does not exist: {path}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise DomainValidationError(f"invalid claim toml: {exc}")
    if set(raw) != {"claim"}:
        raise DomainValidationError(
            "a claim file must contain exactly one [claim] table"
        )
    claim = raw["claim"]
    if not isinstance(claim, dict):
        raise DomainValidationError("[claim] must be a table")
    unknown = set(claim) - _CLAIM_KEYS
    if unknown:
        raise DomainValidationError(
            f"unknown claim keys: {sorted(unknown)}"
        )
    return triage_claim_table(claim, path)


def triage_claim_table(claim: dict, path: Path) -> ClaimTriageResult:
    """Triage an already-loaded [claim] table; shared with the
    registration wizard, which embeds a claim beside a registration."""

    if not isinstance(claim, dict):
        raise DomainValidationError("[claim] must be a table")

    reasons: list[TriageReason] = []

    family = claim.get("family")
    if not isinstance(family, str) or not family.strip():
        reasons.append(
            TriageReason(
                code="MISSING_FAMILY",
                field="family",
                detail="the claim carries no family name",
                guidance="name the technique so its verdict can be pinned",
            )
        )
        family = ""
    family = family.strip()

    summary = claim.get("summary", "")
    if not isinstance(summary, str):
        raise DomainValidationError("summary must be a string")

    direction = claim.get("direction")
    if not isinstance(direction, str) or not direction.strip():
        reasons.append(
            TriageReason(
                code="MISSING_DIRECTION",
                field="direction",
                detail="no rule states which way the trade points",
                guidance=(
                    "state a mechanical direction rule, for example: "
                    "long when the close is above both averages and "
                    "the slow average exceeds its value twelve bars "
                    "ago, short on the mirror"
                ),
            )
        )
    else:
        marker = _find_marker(direction)
        if marker is not None:
            reasons.append(
                TriageReason(
                    code="HIDDEN_DISCRETION",
                    field="direction",
                    detail=f"discretion marker found: {marker}",
                    guidance=(
                        "replace the judgement call with a number or a "
                        "comparison a machine can evaluate"
                    ),
                )
            )

    data = claim.get("data")
    if not isinstance(data, list) or not data or not all(
        isinstance(item, str) and item.strip() for item in data
    ):
        reasons.append(
            TriageReason(
                code="MISSING_DATA",
                field="data",
                detail="the claim names no data sources",
                guidance=_DATA_GUIDANCE,
            )
        )
        data_tuple: tuple[str, ...] = ()
    else:
        data_tuple = tuple(item.strip() for item in data)
        for item in data_tuple:
            if item not in AVAILABLE_DATA:
                reasons.append(
                    TriageReason(
                        code="UNAVAILABLE_DATA",
                        field="data",
                        detail=f"outside the pipeline: {item}",
                        guidance=_DATA_GUIDANCE,
                    )
                )

    conditions = claim.get("conditions")
    if not isinstance(conditions, list) or not conditions or not all(
        isinstance(item, str) and item.strip() for item in conditions
    ):
        reasons.append(
            TriageReason(
                code="MISSING_CONDITIONS",
                field="conditions",
                detail="the claim states no entry conditions",
                guidance=(
                    "list each condition as its own sentence; every "
                    "sentence must be answerable yes or no from the "
                    "named data"
                ),
            )
        )
        condition_count = 0
    else:
        condition_count = len(conditions)
        for index, condition in enumerate(conditions):
            marker = _find_marker(condition)
            if marker is not None:
                reasons.append(
                    TriageReason(
                        code="HIDDEN_DISCRETION",
                        field=f"conditions[{index}]",
                        detail=f"discretion marker found: {marker}",
                        guidance=(
                            "replace the judgement call with a number "
                            "or a comparison a machine can evaluate, "
                            "for example: 'pulled back appropriately' "
                            "becomes 'distance to the 20 average at or "
                            "below 0.5 percent'"
                        ),
                    )
                )

    return ClaimTriageResult(
        claim_path=path,
        family=family,
        summary=summary.strip(),
        data=data_tuple,
        condition_count=condition_count,
        admissible=not reasons,
        reasons=tuple(reasons),
    )


def render_claim_triage_report(result: ClaimTriageResult) -> str:
    if not isinstance(result, ClaimTriageResult):
        raise DomainValidationError("result must be ClaimTriageResult")
    lines = [
        "Verifier Claim Triage Report",
        "Research diagnostics only. Refusal is a first-class output.",
        "No API keys, private API, orders, account access, or trading.",
        "",
        f"claim: {result.claim_path}",
        f"family: {result.family if result.family else '(missing)'}",
        f"data: {', '.join(result.data) if result.data else '(missing)'}",
        f"conditions: {result.condition_count}",
        f"admissible: {'yes' if result.admissible else 'no'}",
        "reasons:",
    ]
    if not result.reasons:
        lines.append("- none")
    for reason in result.reasons:
        lines.append(
            f"- {reason.code} field={reason.field} detail={reason.detail}"
        )
        lines.append(f"  guidance: {reason.guidance}")
    lines.extend(
        [
            "screen_note: the discretion screen is a lexical heuristic; "
            "passing it does not prove the claim is fully mechanical, "
            "and the registration stage remains the binding contract",
            (
                "next_steps: encode the claim as a graph_v1 definition "
                "file (see definitions/ma_touch_trend.toml), then seal it "
                "with verify-register before looking at any data"
                if result.admissible
                else "next_steps: repair every reason above, then "
                "resubmit; each guidance line states the smallest fix"
            ),
            "",
        ]
    )
    return "\n".join(lines)
