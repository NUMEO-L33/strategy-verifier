"""Verifier certificate renderer (session F).

Folds a verdict report and a diagnosis report into a single A4
print-ready HTML certificate: the verdict line, the registered
criteria with pass or fail, the per-market readings, the clinical
grade excerpt, the equation hashes that make the document
recomputable, and the boundary text that makes it honest. No
external dependencies; any browser prints it to PDF. The certificate
adds nothing to the reports it cites — every number on it exists in
the referenced files, and the result hashes prove which files those
were.

Research diagnostics only. No orders, no advice, no trading paths.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from strategy_verifier.domain.errors import DomainValidationError

_BOUNDARY = (
    "이 문서는 등록된 기준에 대한 과거형·통계형 진술이다. 어떤 내용도 "
    "투자 자문·매매 권유가 아니며, 미래 수익이나 손실에 대한 예측·보장이 "
    "아니다. 모든 투자 판단과 그 결과는 전적으로 이용자 본인의 "
    "책임이다. 전문은 docs/DISCLAIMER.md 에 있다."
)


@dataclass(frozen=True, slots=True)
class CertificateResult:
    family: str
    verdict_name: str
    output_path: Path


def _grab(text: str, prefix: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def _block(text: str, header: str) -> list[str]:
    lines = []
    active = False
    for line in text.splitlines():
        if line.strip() == header:
            active = True
            continue
        if active:
            if line.startswith("- ") or line.startswith("  "):
                lines.append(line.strip())
            else:
                break
    return lines


def _rows(items: list[str]) -> str:
    return "\n".join(
        f"<tr><td>{html.escape(item)}</td></tr>" for item in items
    )


def render_certificate(
    family: str,
    verdict_text: str,
    diagnosis_text: str,
    output_path: Path,
) -> CertificateResult:
    if not isinstance(family, str) or not family.strip():
        raise DomainValidationError("family must be a non-empty string")
    verdict_line = _grab(verdict_text, "VERDICT: ")
    if verdict_line is None:
        raise DomainValidationError(
            "the verdict report carries no VERDICT line"
        )
    cell_line = _grab(verdict_text, "cell: ") or ""
    pooled_line = _grab(verdict_text, "pooled: ") or ""
    market_lines = _block(verdict_text, "markets:")
    criteria_lines = _block(verdict_text, "criteria:")
    equation_result = None
    technique_line = None
    market_hash = None
    for source in (verdict_text, diagnosis_text):
        equation_result = equation_result or _grab(source, "result: ")
        technique_line = technique_line or _grab(source, "technique: ")
        market_hash = market_hash or _grab(source, "market_combined: ")
    grade_key = _grab(diagnosis_text, "grade_key: ") or ""
    cell_grades = _block(diagnosis_text, "cell_grades:")
    problems = []
    for line in diagnosis_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [") and "]" in stripped:
            problems.append(stripped[2:])
    edges = _block(diagnosis_text, "edges_present:")

    passed = verdict_line.endswith("_CONFIRMED") and not (
        verdict_line.endswith("_NOT_CONFIRMED")
    )
    verdict_class = "pass" if passed else "fail"
    today = date.today().isoformat()

    document = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>검증 감정서 — {html.escape(family)}</title>
<style>
  @page {{ size: A4; margin: 18mm; }}
  body {{ font-family: 'Noto Sans KR', 'Malgun Gothic', sans-serif;
         color: #111; margin: 0; font-size: 11pt; }}
  h1 {{ font-size: 20pt; margin: 0 0 2mm; letter-spacing: 0.05em; }}
  .sub {{ color: #555; font-size: 9pt; margin-bottom: 6mm; }}
  .verdict {{ font-size: 16pt; font-weight: 700; padding: 4mm 5mm;
              border: 1.2pt solid #111; margin: 4mm 0 6mm;
              font-family: 'Courier New', monospace; }}
  .verdict.fail {{ background: #f3f3f3; }}
  .verdict.pass {{ background: #eef6ee; }}
  h2 {{ font-size: 11pt; border-bottom: 0.6pt solid #999;
        padding-bottom: 1mm; margin: 5mm 0 2mm; }}
  table {{ border-collapse: collapse; width: 100%;
           font-size: 9.5pt; }}
  td {{ border: 0.4pt solid #bbb; padding: 1.2mm 2mm;
        font-family: 'Courier New', monospace; }}
  .mono {{ font-family: 'Courier New', monospace; font-size: 8.5pt;
           word-break: break-all; color: #333; }}
  .boundary {{ margin-top: 7mm; padding: 3mm; border: 0.6pt solid #999;
               font-size: 8.5pt; color: #333; }}
  .foot {{ margin-top: 4mm; font-size: 8pt; color: #777; }}
</style>
</head>
<body>
<h1>검증 감정서</h1>
<div class="sub">Verification Certificate ·
family: <b>{html.escape(family)}</b> · issued: {today} ·
description of a registered adjudication, nothing more</div>

<div class="verdict {verdict_class}">{html.escape(verdict_line)}</div>
<div class="mono">{html.escape(cell_line)}<br>
pooled: {html.escape(pooled_line)}</div>

<h2>판정 기준 (registered bar)</h2>
<table>{_rows(criteria_lines)}</table>

<h2>마켓별 판독 (per market)</h2>
<table>{_rows(market_lines)}</table>

<h2>임상 진단 발췌 (clinical grades)</h2>
<div class="mono">{html.escape(grade_key)}</div>
<table>{_rows(cell_grades)}</table>
<h2>구조적 소견 (problems)</h2>
<table>{_rows(problems if problems else ['none'])}</table>
<h2>유의 구간 (edges present)</h2>
<table>{_rows(edges if edges else ['none'])}</table>

<h2>등식 (market + technique + order = result)</h2>
<div class="mono">
market_combined: {html.escape(market_hash or 'see report')}<br>
technique: {html.escape(technique_line or 'see report')}<br>
verdict result: {html.escape(equation_result or 'see report')}
</div>

<div class="boundary">{html.escape(_BOUNDARY)}</div>
<div class="foot">같은 시장·기법·주문은 같은 결과 해시를 재현한다.
이 감정서의 모든 수치는 인용된 리포트 파일에 존재하며, 해시가 그
파일들을 특정한다. Research diagnostics only.</div>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
    return CertificateResult(
        family=family.strip(),
        verdict_name=verdict_line,
        output_path=output_path,
    )
