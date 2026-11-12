"""Ask-the-record chat (J-5): grounded, filtered, tiered.

Tier one is deterministic: intent-matched templates that speak only
the facts in the context packet. Tier two, active when the
VERIFIER_LLM_KEY environment variable is set, sends the same packet
to an Anthropic model over the standard library with a
describe-only system prompt — and every model reply passes a
server-side vocabulary filter before anyone sees it: if an
instruction or a spot evaluation slips through, the reply is
replaced wholesale by the fixed boundary sentence. The boundary is
code, not a promise. Any transport or parsing failure degrades to
tier one, so the chat works offline.

Research diagnostics only. No orders, no advice, no trading paths.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

_FORBIDDEN = (
    "buy", "sell", "entry", "exit", "take-profit", "take profit",
    "stop-loss", "stop loss", "recommend", "you should", "good spot",
    "bad spot", "right spot", "wrong spot", "opportunity", "act now",
    "signal",
)

_REFUSAL = (
    "I can't help with that decision. I describe what happened and "
    "what the record says; whether to act is entirely yours."
)

_SYSTEM = (
    "You are the record-keeper of a trading-claim verifier. You "
    "describe only: which registered conditions held, their measured "
    "values, and the family's adjudicated record, all in past or "
    "present-factual tense. You never advise, never evaluate the "
    "current spot, never predict, and never use trade-instruction "
    "vocabulary. If asked whether to act, or whether a spot is good, "
    f'reply exactly: "{_REFUSAL}" Answer briefly from the context '
    "packet only; if the packet lacks the answer, say so."
)


_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def grounding_ok(text: str, context: dict, question: str) -> bool:
    """Every numeric token in the reply must already exist in
    the context packet or the question. Derived figures count as
    fabrication: the chat states the record, it does not
    recompute it."""
    allowed = set(_NUMBER.findall(
        json.dumps(context, ensure_ascii=False))) | set(
        _NUMBER.findall(question))
    return set(_NUMBER.findall(text)) <= allowed


def vocabulary_filter(text: str) -> tuple[str, bool]:
    lowered = text.lower()
    for term in _FORBIDDEN:
        if term in lowered:
            return _REFUSAL, True
    return text, False


def build_context(
    family: str | None,
    adjudication_lines: list[str],
    verdict_label: str,
    markers: list[dict],
    latest: dict | None,
    bar_count: int,
) -> dict:
    favorable = sum(
        1 for marker in markers if marker.get("outcome") == "favorable"
    )
    adverse = sum(
        1 for marker in markers if marker.get("outcome") == "adverse"
    )
    return {
        "family": family,
        "adjudication": adjudication_lines,
        "verdict_label": verdict_label,
        "bar_count": bar_count,
        "occurrences_shown": len(markers),
        "favorable": favorable,
        "adverse": adverse,
        "last_occurrences": markers[-5:],
        "latest_bar": latest,
    }


def deterministic_answer(question: str, context: dict) -> str:
    lowered = question.lower()
    family = context.get("family") or "no family"
    label = context.get("verdict_label", "UNADJUDICATED")
    if context.get("family") is None:
        return (
            "No feed is being followed. Start one on the workspace "
            "and I can describe its record."
        )
    if any(k in lowered for k in ("should", "act", "good", "spot",
                                  "잡", "진입")):
        return _REFUSAL
    if "why" in lowered and ("match" in lowered or "marker" in lowered
                             or "occurrence" in lowered):
        last = (context.get("last_occurrences") or [None])[-1]
        if not last:
            return "No occurrence has fired on this feed yet."
        held = [name for name, ok in
                (last.get("conditions") or {}).items() if ok]
        series = last.get("series") or {}
        values = " · ".join(
            f"{k}={v}" for k, v in list(series.items())[:4]
        )
        return (
            f"At {last['timestamp']} every registered condition "
            f"held: {' · '.join(held) or 'none listed'}. Measured "
            f"values: {values or 'none'}. Side: "
            f"{last['side']}. This family's verdict: {label}."
        )
    if "not_confirmed" in lowered or "confirmed" in lowered:
        return (
            f"{family}: " + " ".join(context.get("adjudication", []))
        )
    if "record" in lowered or "conditions" in lowered:
        latest = context.get("latest_bar")
        chips = (
            " · ".join(
                f"{k}={'yes' if v else 'no'}"
                for k, v in (latest.get("conditions") or {}).items()
            ) if latest else "no closed bar yet"
        )
        return (
            f"{family} · verdict {label} · occurrences shown "
            f"{context['occurrences_shown']} (favorable "
            f"{context['favorable']}, adverse {context['adverse']}) "
            f"over {context['bar_count']} bars. Latest bar: {chips}."
        )
    return (
        f"{family} · verdict {label} · {context['occurrences_shown']} "
        "occurrences on this feed. Ask why a match fired, what the "
        "record is, or why the verdict reads as it does."
    )


def llm_answer(
    question: str, context: dict, key: str,
    model: str | None = None,
    base_url: str | None = None,
) -> str:
    model = model or os.environ.get(
        "VERIFIER_LLM_MODEL", "claude-sonnet-4-6")
    base_url = base_url or os.environ.get(
        "VERIFIER_LLM_URL", "https://api.anthropic.com")
    body = json.dumps({
        "model": model,
        "max_tokens": 400,
        "system": _SYSTEM,
        "messages": [{
            "role": "user",
            "content": (
                "Context packet (facts only):\n"
                + json.dumps(context, ensure_ascii=False)
                + "\n\nQuestion: " + question
            ),
        }],
    }).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    parts = [
        item.get("text", "") for item in payload.get("content", [])
        if item.get("type") == "text"
    ]
    text = "\n".join(part for part in parts if part).strip()
    if not text:
        raise ValueError("empty model reply")
    return text


def answer(question: str, context: dict) -> tuple[str, str]:
    """Returns (text, mode) with mode in deterministic | llm |
    filtered."""
    key = os.environ.get("VERIFIER_LLM_KEY", "").strip()
    if key:
        try:
            text = llm_answer(question, context, key)
        except Exception:
            return deterministic_answer(question, context), (
                "fallback"
            )
        filtered_text, hit = vocabulary_filter(text)
        if hit:
            return filtered_text, "filtered"
        if not grounding_ok(filtered_text, context, question):
            return deterministic_answer(question, context), (
                "grounded"
            )
        return filtered_text, "llm"
    return deterministic_answer(question, context), "deterministic"
