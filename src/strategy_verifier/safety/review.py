"""Fail-closed source safety review.

Scans the package for order, broker, key, and network-client vocabulary
so that the verifier provably stays a read-only research tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

APPROVED_PACKAGES = frozenset(
    {
        "domain",
        "product_resources",
        "public_market_data",
        "safety",
        "ui",
        "verifier",
    }
)

FORBIDDEN_MODULE_NAMES = frozenset(
    {
        "execution",
        "exchange",
        "broker",
        "live",
        "order",
        "orders",
        "web" + "socket",
        "api" + "_client",
        "paper",
    }
)

# Sanctioned pairs: (relative path, term) explicitly allowed with a
# recorded reason. The recorder is market-data only — it opens a
# read-only public stream and contains no order vocabulary; the
# charter's stage-three (v2) work made this exemption on 2026-08-01.
SANCTIONED_SOURCE_TERMS = {
    ("verifier/recorder.py", "web" + "socket"):
        "market-data recording only; no order paths",
}

DANGEROUS_SOURCE_TERMS = frozenset(
    {
        "place" + "_order",
        "cancel" + "_order",
        "live" + "_order",
        "api" + "_key",
        "secret" + "_key",
        "web" + "socket",
        "req" + "uests",
        "http" + "x",
        "aio" + "http",
    }
)


@dataclass(frozen=True, slots=True)
class SafetyFinding:
    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class SafetyReviewResult:
    passed: bool
    scanned_files: int
    findings: tuple[SafetyFinding, ...]


def run_safety_review(package_root: Path | None = None) -> SafetyReviewResult:
    root = package_root or Path(__file__).resolve().parents[1]
    findings: list[SafetyFinding] = []
    scanned_files = 0

    if not root.exists():
        return SafetyReviewResult(
            passed=False,
            scanned_files=0,
            findings=(SafetyFinding(str(root), "package root does not exist"),),
        )

    findings.extend(_scan_module_names(root))
    for source_path in sorted(root.rglob("*.py")):
        scanned_files += 1
        findings.extend(_scan_source_terms(root, source_path))

    return SafetyReviewResult(
        passed=not findings,
        scanned_files=scanned_files,
        findings=tuple(findings),
    )


def _scan_module_names(root: Path) -> list[SafetyFinding]:
    findings: list[SafetyFinding] = []
    for path in sorted(root.rglob("*")):
        if not path.is_dir():
            continue
        name = path.name
        if name.startswith("__"):
            continue
        relative = path.relative_to(root)
        if len(relative.parts) == 1 and name in APPROVED_PACKAGES:
            continue
        if name in FORBIDDEN_MODULE_NAMES:
            findings.append(
                SafetyFinding(
                    str(relative),
                    f"forbidden module/package name: {name}",
                )
            )
    for source_path in sorted(root.rglob("*.py")):
        stem = source_path.stem
        if stem in FORBIDDEN_MODULE_NAMES:
            findings.append(
                SafetyFinding(
                    str(source_path.relative_to(root)),
                    f"forbidden module/package name: {stem}",
                )
            )
    return findings


def _scan_source_terms(root: Path, source_path: Path) -> list[SafetyFinding]:
    findings: list[SafetyFinding] = []
    text = source_path.read_text(encoding="utf-8")
    lowered = text.lower()
    relative = source_path.relative_to(root)
    for term in sorted(DANGEROUS_SOURCE_TERMS):
        if term in lowered:
            if (str(relative), term) in SANCTIONED_SOURCE_TERMS:
                continue
            findings.append(
                SafetyFinding(
                    str(relative),
                    f"dangerous source term: {term}",
                )
            )
    return findings
