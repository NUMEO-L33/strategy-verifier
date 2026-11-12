"""Verifier verdict rendering.

Reads the measurement reports, extracts the registered arm at the
primary cell per market, computes pooled Wilson intervals, applies
the registered bar — against the coin, or against a named control arm
by interval separation — and renders the final verdict: confirmed, or
the family's registered not-confirmed name. The criteria mirror the
registration document's contract exactly, every number in the verdict
is recomputable from the cited reports, and the exit code carries the
outcome so pipelines can branch on it.

Research diagnostics only. No orders, no advice, no trading paths.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from strategy_verifier.domain.errors import DomainValidationError

_Z = 1.96


def parse_report_grids(text: str) -> dict:
    grids: dict = {}
    current = None
    for line in text.splitlines():
        header = re.match(r"^([a-z0-9_]+(?:=[a-z]+)?)(?: \(.*\))?:$", line)
        if header and ("grid" in header.group(1) or "=" in header.group(1)):
            current = header.group(1)
            grids[current] = {}
            continue
        if current and line.startswith("- horizon="):
            horizon = int(re.search(r"horizon=(\d+)", line).group(1))
            for cell in re.finditer(r"([\d.]+): (\d+)/(\d+)/(\d+)", line):
                grids[current][(horizon, cell.group(1))] = (
                    int(cell.group(2)),
                    int(cell.group(3)),
                    int(cell.group(4)),
                )
        elif current and not line.startswith("-"):
            current = None
    return grids


def wilson(favorable: int, resolved: int) -> tuple[float, float, float]:
    if resolved <= 0:
        return (0.0, 0.0, 0.0)
    share = favorable / resolved
    denominator = 1 + _Z * _Z / resolved
    center = (share + _Z * _Z / (2 * resolved)) / denominator
    half_width = (
        _Z
        * math.sqrt(
            share * (1 - share) / resolved
            + _Z * _Z / (4 * resolved * resolved)
        )
        / denominator
    )
    return (share, center - half_width, center + half_width)


@dataclass(frozen=True, slots=True)
class MarketReading:
    report_path: Path
    favorable: int
    adverse: int
    share: float
    control_share: float | None


@dataclass(frozen=True, slots=True)
class Criterion:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class VerdictResult:
    family: str
    arm: str
    control_arm: str | None
    horizon: int
    threshold: str
    markets: tuple[MarketReading, ...]
    pooled_favorable: int
    pooled_adverse: int
    pooled_share: float
    pooled_low: float
    pooled_high: float
    control_share: float | None
    control_low: float | None
    control_high: float | None
    criteria: tuple[Criterion, ...]
    passed: bool
    verdict_name: str


def _extract_cell(
    grids: dict, section: str, horizon: int, threshold: str, path: Path
) -> tuple[int, int]:
    if section not in grids:
        raise DomainValidationError(
            f"section {section} not found in {path}"
        )
    cell = grids[section].get((horizon, threshold))
    if cell is None:
        raise DomainValidationError(
            f"cell horizon={horizon} threshold={threshold} not found "
            f"under {section} in {path}"
        )
    return cell[0], cell[1]


def run_verdict(
    family: str,
    report_paths: list[Path],
    horizon: int,
    threshold: str,
    arm: str,
    control_arm: str | None,
) -> VerdictResult:
    if not isinstance(family, str) or not family.strip():
        raise DomainValidationError("family must be a non-empty string")
    if not report_paths:
        raise DomainValidationError("at least one report is required")
    family = family.strip()

    markets: list[MarketReading] = []
    pooled_favorable = pooled_adverse = 0
    control_favorable = control_adverse = 0
    for path in report_paths:
        if not path.exists():
            raise DomainValidationError(f"report does not exist: {path}")
        grids = parse_report_grids(path.read_text(encoding="utf-8"))
        favorable, adverse = _extract_cell(
            grids, arm, horizon, threshold, path
        )
        pooled_favorable += favorable
        pooled_adverse += adverse
        share = (
            favorable / (favorable + adverse)
            if favorable + adverse else 0.0
        )
        market_control_share = None
        if control_arm is not None:
            c_favorable, c_adverse = _extract_cell(
                grids, control_arm, horizon, threshold, path
            )
            control_favorable += c_favorable
            control_adverse += c_adverse
            market_control_share = (
                c_favorable / (c_favorable + c_adverse)
                if c_favorable + c_adverse else 0.0
            )
        markets.append(
            MarketReading(
                report_path=path,
                favorable=favorable,
                adverse=adverse,
                share=share,
                control_share=market_control_share,
            )
        )

    pooled_share, pooled_low, pooled_high = wilson(
        pooled_favorable, pooled_favorable + pooled_adverse
    )
    market_count = len(markets)
    needed = 2 if market_count == 3 else market_count // 2 + 1

    criteria: list[Criterion] = []
    if control_arm is None:
        criteria.append(
            Criterion(
                name="pooled Wilson lower bound above one half",
                passed=pooled_low > 0.5,
                detail=f"lower={pooled_low:.4f}",
            )
        )
        above = sum(1 for market in markets if market.share > 0.5)
        criteria.append(
            Criterion(
                name=(
                    f"per-market share above one half on at least "
                    f"{needed} of {market_count}"
                ),
                passed=above >= needed,
                detail=f"above={above}/{market_count}",
            )
        )
        control_share = control_low = control_high = None
    else:
        control_share, control_low, control_high = wilson(
            control_favorable, control_favorable + control_adverse
        )
        criteria.append(
            Criterion(
                name="pooled interval separation above the control",
                passed=pooled_low > control_high,
                detail=(
                    f"arm_lower={pooled_low:.4f} "
                    f"control_upper={control_high:.4f}"
                ),
            )
        )
        above = sum(
            1
            for market in markets
            if market.control_share is not None
            and market.share > market.control_share
        )
        criteria.append(
            Criterion(
                name=(
                    f"per-market share above own control on at least "
                    f"{needed} of {market_count}"
                ),
                passed=above >= needed,
                detail=f"above={above}/{market_count}",
            )
        )

    passed = all(criterion.passed for criterion in criteria)
    verdict_name = (
        f"{family.upper()}_CONFIRMED"
        if passed
        else f"{family.upper()}_NOT_CONFIRMED"
    )
    return VerdictResult(
        family=family,
        arm=arm,
        control_arm=control_arm,
        horizon=horizon,
        threshold=threshold,
        markets=tuple(markets),
        pooled_favorable=pooled_favorable,
        pooled_adverse=pooled_adverse,
        pooled_share=pooled_share,
        pooled_low=pooled_low,
        pooled_high=pooled_high,
        control_share=control_share,
        control_low=control_low,
        control_high=control_high,
        criteria=tuple(criteria),
        passed=passed,
        verdict_name=verdict_name,
    )


def render_verdict_report(result: VerdictResult) -> str:
    if not isinstance(result, VerdictResult):
        raise DomainValidationError("result must be VerdictResult")
    lines = [
        "Verifier Verdict Report",
        "Research diagnostics only. Every number is recomputable from "
        "the cited reports.",
        "No API keys, private API, orders, account access, or trading.",
        "",
        f"family: {result.family}",
        (
            f"cell: horizon={result.horizon} "
            f"threshold={result.threshold} arm={result.arm}"
            + (
                f" control={result.control_arm}"
                if result.control_arm
                else " (against the coin)"
            )
        ),
        "markets:",
    ]
    for market in result.markets:
        control_text = (
            f" control={market.control_share:.4f}"
            if market.control_share is not None
            else ""
        )
        lines.append(
            f"- {market.report_path.name}: {market.favorable}/"
            f"{market.adverse} share={market.share:.4f}{control_text}"
        )
    lines.append(
        f"pooled: {result.pooled_favorable}/{result.pooled_adverse} "
        f"share={result.pooled_share:.4f} "
        f"wilson=[{result.pooled_low:.4f}, {result.pooled_high:.4f}]"
    )
    if result.control_arm is not None:
        lines.append(
            f"control_pooled: share={result.control_share:.4f} "
            f"wilson=[{result.control_low:.4f}, {result.control_high:.4f}]"
        )
    lines.append("criteria:")
    for criterion in result.criteria:
        lines.append(
            f"- [{'pass' if criterion.passed else 'fail'}] "
            f"{criterion.name} ({criterion.detail})"
        )
    lines.extend(
        [
            f"VERDICT: {result.verdict_name}",
            "anatomy: run verify-diagnose on the same reports for "
            "where it broke and why",
            (
                "next_steps: advance the family to confirmed with this "
                "verdict in the note"
                if result.passed
                else "next_steps: advance the family to closed with this "
                "verdict in the note; closure is permanent"
            ),
            "",
        ]
    )
    return "\n".join(lines)
