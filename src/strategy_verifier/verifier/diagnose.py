"""Verifier diagnosis (clinical anatomy).

Grades every segment of a measurement and lists problems with
evidence. Grade taxonomy per pooled cell: THIN (resolved under the
floor), EDGE (Wilson lower bound above one half), INVERSE (upper
bound below one half), LEAN+ or LEAN- (share off one half without
significance), FLAT (exact tie). Detectors state what is wrong and
where; implications are one factual sentence. Diagnosis only:
prescriptions, tuning, and freeze selection are out of scope.

Research diagnostics only. No orders, no advice, no trading paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.verifier.verdict import parse_report_grids, wilson

_FLOOR = 100


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    severity: str
    title: str
    evidence: str
    implication: str


@dataclass(frozen=True, slots=True)
class DiagnosisResult:
    family: str
    arm: str
    primary_horizon: int
    primary_threshold: str
    cell_grade_lines: tuple[str, ...]
    market_grade_lines: tuple[str, ...]
    section_grade_lines: tuple[str, ...]
    findings: tuple[Finding, ...]
    edges_present: tuple[str, ...]


def _share(favorable: int, adverse: int) -> float | None:
    resolved = favorable + adverse
    if resolved == 0:
        return None
    return favorable / resolved


def _grade(favorable: int, adverse: int) -> tuple[str, str]:
    resolved = favorable + adverse
    share = _share(favorable, adverse)
    if resolved < _FLOOR:
        share_text = f"{share:.4f}" if share is not None else "-"
        return (
            "THIN",
            f"{favorable}/{adverse} share={share_text} n={resolved}",
        )
    share, low, high = wilson(favorable, resolved)
    detail = (
        f"{favorable}/{adverse} share={share:.4f} [{low:.4f},{high:.4f}]"
    )
    if low > 0.5:
        return "EDGE", detail
    if high < 0.5:
        return "INVERSE", detail
    if share > 0.5:
        return "LEAN+", detail
    if share < 0.5:
        return "LEAN-", detail
    return "FLAT", detail


def _pooled(per_market: dict, section: str, cell) -> tuple[int, int]:
    favorable = adverse = 0
    for grids in per_market.values():
        value = grids.get(section, {}).get(cell)
        if value is not None:
            favorable += value[0]
            adverse += value[1]
    return favorable, adverse


def run_diagnosis(
    family: str,
    report_paths: list[Path],
    primary_horizon: int,
    primary_threshold: str,
    arm: str,
    control_arm: str | None,
    side_arms: tuple[str, str] | None,
    split_pairs: tuple[tuple[str, str], ...],
) -> DiagnosisResult:
    if not isinstance(family, str) or not family.strip():
        raise DomainValidationError("family must be a non-empty string")
    if not report_paths:
        raise DomainValidationError("at least one report is required")

    per_market: dict[Path, dict] = {}
    for path in report_paths:
        if not path.exists():
            raise DomainValidationError(f"report does not exist: {path}")
        per_market[path] = parse_report_grids(
            path.read_text(encoding="utf-8")
        )

    sections_needed = [arm]
    if control_arm:
        sections_needed.append(control_arm)
    if side_arms:
        sections_needed.extend(side_arms)
    for pair in split_pairs:
        sections_needed.extend(pair)
    horizons: set[int] = set()
    thresholds: set[str] = set()
    for path, grids in per_market.items():
        for section in sections_needed:
            if section not in grids:
                raise DomainValidationError(
                    f"section {section} not found in {path}"
                )
        for cell in grids[arm]:
            horizons.add(cell[0])
            thresholds.add(cell[1])
    horizons_sorted = tuple(sorted(horizons))
    thresholds_sorted = tuple(
        sorted(thresholds, key=lambda text: float(text))
    )
    primary = (primary_horizon, primary_threshold)
    if primary not in per_market[report_paths[0]][arm]:
        raise DomainValidationError(
            "primary cell not present in the arm's grid"
        )

    findings: list[Finding] = []
    edges: list[str] = []

    # 전 셀 등급
    cell_grade_lines: list[str] = []
    shape_shares: list[float] = []
    for horizon in horizons_sorted:
        for threshold in thresholds_sorted:
            favorable, adverse = _pooled(
                per_market, arm, (horizon, threshold)
            )
            grade, detail = _grade(favorable, adverse)
            cell_grade_lines.append(
                f"horizon={horizon} t={threshold}: {grade} {detail}"
            )
            if grade == "EDGE":
                edges.append(
                    f"cell horizon={horizon} t={threshold}: {detail}"
                )
            if threshold == primary_threshold:
                share = _share(favorable, adverse)
                if share is not None:
                    shape_shares.append(share)

    # 마켓 등급 (주 셀)
    market_grade_lines: list[str] = []
    market_shares: list[float] = []
    for path in report_paths:
        value = per_market[path][arm][primary]
        grade, detail = _grade(value[0], value[1])
        market_grade_lines.append(f"{path.name}: {grade} {detail}")
        if grade == "EDGE":
            edges.append(f"market {path.name} at primary: {detail}")
        share = _share(value[0], value[1])
        market_shares.append(share if share is not None else 0.0)

    pooled_favorable, pooled_adverse = _pooled(per_market, arm, primary)
    pooled_share, pooled_low, pooled_high = wilson(
        pooled_favorable, pooled_favorable + pooled_adverse
    )
    half_width = (pooled_high - pooled_low) / 2

    # 섹션 등급: 대조군, 사이드(주 셀+최장), 분할 양쪽(주 셀)
    section_grade_lines: list[str] = []

    def _grade_section(section: str, cell, label: str) -> tuple[str, float | None]:
        favorable, adverse = _pooled(per_market, section, cell)
        grade, detail = _grade(favorable, adverse)
        section_grade_lines.append(f"{label}: {grade} {detail}")
        if grade == "EDGE":
            edges.append(f"{label}: {detail}")
        return grade, _share(favorable, adverse)

    control_share = None
    if control_arm:
        _, control_share = _grade_section(
            control_arm, primary, f"{control_arm}@primary"
        )
    side_longest: dict[str, float | None] = {}
    if side_arms:
        longest = horizons_sorted[-1]
        for section in side_arms:
            _grade_section(section, primary, f"{section}@primary")
            _, share = _grade_section(
                section,
                (longest, primary_threshold),
                f"{section}@horizon={longest}",
            )
            side_longest[section] = share
    split_shares: dict[str, float | None] = {}
    for pair in split_pairs:
        for section in pair:
            _, share = _grade_section(
                section, primary, f"{section}@primary"
            )
            split_shares[section] = share

    # 문제 목록
    if len(shape_shares) >= 3:
        drop = shape_shares[0] - shape_shares[-1]
        chain = " -> ".join(f"{s:.3f}" for s in shape_shares)
        if all(
            a >= b for a, b in zip(shape_shares, shape_shares[1:])
        ) and drop >= 0.03:
            findings.append(Finding(
                code="HORIZON_DECAY",
                severity="warning",
                title="share falls monotonically with horizon",
                evidence=f"primary-threshold shares: {chain}",
                implication=(
                    "the conditioned move reverses beyond the shorter "
                    "horizons"
                ),
            ))
        elif all(
            a <= b for a, b in zip(shape_shares, shape_shares[1:])
        ) and -drop >= 0.03:
            findings.append(Finding(
                code="HORIZON_GROWTH",
                severity="info",
                title="share rises monotonically with horizon",
                evidence=f"primary-threshold shares: {chain}",
                implication=(
                    "the conditioned move needs the longer horizons to "
                    "express"
                ),
            ))

    if market_shares:
        above = sum(1 for share in market_shares if share > 0.5)
        best = max(market_shares)
        rest = sorted(market_shares)[:-1]
        if above <= 1 and rest and best - sum(rest) / len(rest) >= 0.08:
            findings.append(Finding(
                code="SINGLE_MARKET",
                severity="critical",
                title="the pooled lean exists in one market only",
                evidence=(
                    "per-market shares at primary: "
                    + ", ".join(f"{s:.3f}" for s in market_shares)
                ),
                implication=(
                    "a one-market lean has no cross-market support and "
                    "carries the highest collapse risk in confirmation"
                ),
            ))

    if pooled_favorable + pooled_adverse < _FLOOR:
        findings.append(Finding(
            code="SAMPLE_STARVATION",
            severity="critical",
            title="the arm is below the measurement floor",
            evidence=(
                f"resolved={pooled_favorable + pooled_adverse} "
                f"floor={_FLOOR}"
            ),
            implication=(
                "no interval at this size supports any statement "
                "beyond THIN"
            ),
        ))

    if control_arm and control_share is not None:
        gap = pooled_share - control_share
        if abs(gap) < half_width:
            findings.append(Finding(
                code="CONTROL_COHABITATION",
                severity="critical",
                title=(
                    "the arm and its control are statistically "
                    "indistinguishable at the primary cell"
                ),
                evidence=(
                    f"arm={pooled_share:.4f} control="
                    f"{control_share:.4f} gap={gap:+.4f} "
                    f"half_width={half_width:.4f}"
                ),
                implication="the condition adds no information",
            ))

    if side_arms and len(side_longest) == 2:
        long_share = side_longest[side_arms[0]]
        short_share = side_longest[side_arms[1]]
        if (
            long_share is not None
            and short_share is not None
            and abs(long_share - short_share) >= 0.15
        ):
            findings.append(Finding(
                code="DRIFT_ASYMMETRY",
                severity="warning",
                title="sides diverge at the longest horizon",
                evidence=(
                    f"{side_arms[0]}={long_share:.3f} "
                    f"{side_arms[1]}={short_share:.3f}"
                ),
                implication=(
                    "consistent with directional drift exposure rather "
                    "than a symmetric edge"
                ),
            ))

    for left, right in split_pairs:
        left_share = split_shares.get(left)
        right_share = split_shares.get(right)
        if left_share is None or right_share is None:
            continue
        spread = left_share - right_share
        if abs(spread) < half_width:
            findings.append(Finding(
                code="SPREAD_INSIDE_NOISE",
                severity="info",
                title=f"split {left} vs {right} separates nothing",
                evidence=(
                    f"{left}={left_share:.4f} {right}="
                    f"{right_share:.4f} spread={spread:+.4f} "
                    f"half_width={half_width:.4f}"
                ),
                implication=(
                    "the axis does not sort winners from losers at "
                    "the primary cell"
                ),
            ))

    if not findings and not edges:
        findings.append(Finding(
            code="FLAT_ANATOMY",
            severity="info",
            title="no segment departs from the base rate",
            evidence=(
                f"pooled {pooled_share:.4f} "
                f"[{pooled_low:.4f},{pooled_high:.4f}]; no EDGE or "
                "INVERSE anywhere"
            ),
            implication="the measurement contains no structure",
        ))

    return DiagnosisResult(
        family=family.strip(),
        arm=arm,
        primary_horizon=primary_horizon,
        primary_threshold=primary_threshold,
        cell_grade_lines=tuple(cell_grade_lines),
        market_grade_lines=tuple(market_grade_lines),
        section_grade_lines=tuple(section_grade_lines),
        findings=tuple(findings),
        edges_present=tuple(edges),
    )


def render_diagnosis_report(result: DiagnosisResult) -> str:
    if not isinstance(result, DiagnosisResult):
        raise DomainValidationError("result must be DiagnosisResult")
    lines = [
        "Verifier Diagnosis Report",
        "Diagnosis only; prescriptions are out of scope.",
        "Research diagnostics only. No orders, no advice, no trading.",
        "",
        f"family: {result.family}",
        (
            f"arm: {result.arm} primary: horizon="
            f"{result.primary_horizon} threshold="
            f"{result.primary_threshold}"
        ),
        "grade_key: EDGE lower>0.5 | INVERSE upper<0.5 | LEAN+/- off "
        "one half without significance | FLAT tie | THIN n<100",
        "cell_grades:",
    ]
    for line in result.cell_grade_lines:
        lines.append(f"- {line}")
    lines.append("market_grades_at_primary:")
    for line in result.market_grade_lines:
        lines.append(f"- {line}")
    if result.section_grade_lines:
        lines.append("section_grades:")
        for line in result.section_grade_lines:
            lines.append(f"- {line}")
    lines.append("problems:")
    if not result.findings:
        lines.append("- none")
    for finding in result.findings:
        lines.append(
            f"- [{finding.severity}] {finding.code}: {finding.title}"
        )
        lines.append(f"  evidence: {finding.evidence}")
        lines.append(f"  implication: {finding.implication}")
    lines.append("edges_present:")
    if result.edges_present:
        for edge in result.edges_present:
            lines.append(f"- {edge}")
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)
