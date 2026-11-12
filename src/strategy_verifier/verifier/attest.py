"""Verifier attestation (the equation, machine-checkable).

result = market + technique + order, and nothing else. An attestation
binds the four terms by content hash: every input capture file (the
market), the definition file (the technique), the verbatim order
string (the user's ask), and the produced report (the result). Check
mode re-derives all four and names exactly which term changed, so
determinism is verified rather than claimed, and any overfitting
would have to announce itself as a changed technique or order hash.

Research diagnostics only. No orders, no advice, no trading paths.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from strategy_verifier.domain.errors import DomainValidationError


def _file_sha(path: Path) -> str:
    if not path.exists():
        raise DomainValidationError(f"file does not exist: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Attestation:
    market_files: tuple[tuple[str, str], ...]
    market_combined: str
    technique_file: str
    technique_sha: str
    order_text: str
    order_sha: str
    report_file: str
    report_sha: str


def build_attestation(
    definition_path: Path,
    input_paths: list[Path],
    order_text: str,
    report_path: Path,
) -> Attestation:
    if not input_paths:
        raise DomainValidationError("at least one input is required")
    if not isinstance(order_text, str) or not order_text.strip():
        raise DomainValidationError("order must be a non-empty string")
    market_files = tuple(
        (path.name, _file_sha(path)) for path in sorted(input_paths)
    )
    combined = _text_sha(
        "\n".join(f"{name} {sha}" for name, sha in market_files)
    )
    return Attestation(
        market_files=market_files,
        market_combined=combined,
        technique_file=definition_path.name,
        technique_sha=_file_sha(definition_path),
        order_text=order_text.strip(),
        order_sha=_text_sha(order_text.strip()),
        report_file=report_path.name,
        report_sha=_file_sha(report_path),
    )


def render_attestation(attestation: Attestation) -> str:
    lines = [
        "Verifier Attestation",
        "result = market + technique + order, and nothing else.",
        "Identical terms force an identical report hash; any deviation "
        "identifies the changed term.",
        "Research diagnostics only. No orders, no advice, no trading.",
        "",
        "market:",
    ]
    for name, sha in attestation.market_files:
        lines.append(f"- {name} sha256[:16]={sha}")
    lines.extend(
        [
            f"market_combined: {attestation.market_combined}",
            (
                f"technique: {attestation.technique_file} "
                f"sha256[:16]={attestation.technique_sha}"
            ),
            f"order: {attestation.order_text}",
            f"order_sha: {attestation.order_sha}",
            (
                f"report: {attestation.report_file} "
                f"sha256[:16]={attestation.report_sha}"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def check_attestation(
    attestation_path: Path,
    definition_path: Path,
    input_paths: list[Path],
    order_text: str,
    report_path: Path,
) -> tuple[bool, tuple[str, ...]]:
    if not attestation_path.exists():
        raise DomainValidationError(
            f"attestation does not exist: {attestation_path}"
        )
    recorded: dict[str, str] = {}
    for line in attestation_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("market_combined: "):
            recorded["market"] = line.split(": ", 1)[1].strip()
        elif line.startswith("technique: "):
            recorded["technique"] = line.rsplit("=", 1)[1].strip()
        elif line.startswith("order_sha: "):
            recorded["order"] = line.split(": ", 1)[1].strip()
        elif line.startswith("report: "):
            recorded["report"] = line.rsplit("=", 1)[1].strip()
    missing = {"market", "technique", "order", "report"} - set(recorded)
    if missing:
        raise DomainValidationError(
            f"attestation is missing terms: {sorted(missing)}"
        )
    derived = build_attestation(
        definition_path, input_paths, order_text, report_path
    )
    current = {
        "market": derived.market_combined,
        "technique": derived.technique_sha,
        "order": derived.order_sha,
        "report": derived.report_sha,
    }
    mismatched = tuple(
        term for term in ("market", "technique", "order", "report")
        if recorded[term] != current[term]
    )
    return (not mismatched, mismatched)
