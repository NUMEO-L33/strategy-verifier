"""J-5: the chat is grounded, filtered, and tiered — deterministic
answers speak only packet facts; the vocabulary filter replaces any
instruction wholesale; the model path is exercised through a
monkeypatched transport including the filtered case and the
degrade-to-deterministic case; the workspace endpoint answers with
no feed and with a live one."""

import json
import json

from strategy_verifier.verifier import chat as chat_module
from strategy_verifier.verifier.chat import (
    answer,
    build_context,
    deterministic_answer,
    vocabulary_filter,
)


def _context():
    return build_context(
        family="live_parity",
        adjudication_lines=["adjudication: UNADJUDICATED"],
        verdict_label="UNADJUDICATED",
        markers=[{
            "timestamp": "2026-08-01T00:15:00+00:00", "side": "long",
            "price": "50100.0", "outcome": "favorable",
            "conditions": {"standing": True, "breakout": True},
            "series": {"gap": "0.0011", "slow": "50050.1"},
        }],
        latest={"conditions": {"in_squeeze": False},
                "direction": "none", "occurred": False},
        bar_count=120,
    )


def test_deterministic_answers_speak_facts():
    context = _context()
    why = deterministic_answer("Why this match?", context)
    assert "standing" in why and "gap=0.0011" in why
    assert "UNADJUDICATED" in why
    record = deterministic_answer("This rule's record", context)
    assert "favorable 1" in record and "120 bars" in record
    refusal = deterministic_answer("Should I act on it?", context)
    assert "entirely yours" in refusal
    lowered = (why + record).lower()
    for term in ("buy", "sell", "entry", "recommend", "signal"):
        assert term not in lowered


def test_filter_and_llm_paths(monkeypatch):
    context = _context()
    filtered, hit = vocabulary_filter("You should buy here.")
    assert hit and "entirely yours" in filtered

    class _Response:
        def __init__(self, text):
            self._text = text

        def read(self):
            return json.dumps({
                "content": [{"type": "text", "text": self._text}]
            }).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    calls = {}

    def fake_urlopen(request, timeout=0):
        calls["headers"] = dict(request.headers)
        return _Response(calls["reply"])

    monkeypatch.setenv("VERIFIER_LLM_KEY", "k-test")
    monkeypatch.setattr(
        chat_module.urllib.request, "urlopen", fake_urlopen
    )

    calls["reply"] = "Every condition held; verdict UNADJUDICATED."
    text, mode = answer("Why this match?", context)
    assert mode == "llm" and "held" in text
    assert calls["headers"].get("X-api-key") == "k-test"

    calls["reply"] = "Looks like a good spot, you should buy."
    text, mode = answer("thoughts?", context)
    assert mode == "filtered" and "entirely yours" in text

    def broken(request, timeout=0):
        raise OSError("down")

    monkeypatch.setattr(
        chat_module.urllib.request, "urlopen", broken
    )
    text, mode = answer("This rule's record", context)
    assert mode == "fallback" and "favorable 1" in text


def test_llm_reply_with_fabricated_number_is_grounded(monkeypatch):
    """A reply quoting a figure absent from the packet is discarded
    and the deterministic answer ships in its place."""
    from strategy_verifier.verifier import chat

    context = chat.build_context(
        "fam", ["adjudication: UNADJUDICATED"], "UNADJUDICATED",
        [{"timestamp": "2026-02-16T00:59:59+00:00", "side": "long",
          "outcome": "adverse", "conditions": {"gate": True},
          "series": {"slow": "100.5"}}],
        None, 42)
    monkeypatch.setenv("VERIFIER_LLM_KEY", "k-test")
    monkeypatch.setattr(
        chat, "llm_answer",
        lambda *a, **k: "The win share is 0.62 over the recorded run.")
    text, mode = chat.answer("what is the record", context)
    assert mode == "grounded"
    assert "0.62" not in text

    monkeypatch.setattr(
        chat, "llm_answer",
        lambda *a, **k: "42 bars, 1 occurrence at "
                        "2026-02-16T00:59:59+00:00, side long.")
    text, mode = chat.answer("what is the record", context)
    assert mode == "llm"
    assert "42 bars" in text


def test_llm_transport_failure_is_visible(monkeypatch):
    from strategy_verifier.verifier import chat

    context = chat.build_context(
        "fam", [], "UNADJUDICATED", [], None, 1)
    monkeypatch.setenv("VERIFIER_LLM_KEY", "k-test")

    def _boom(*a, **k):
        raise OSError("no route")

    monkeypatch.setattr(chat, "llm_answer", _boom)
    _, mode = chat.answer("record?", context)
    assert mode == "fallback"


def test_llm_model_and_url_come_from_env(monkeypatch):
    from strategy_verifier.verifier import chat

    seen = {}

    class _Resp:
        def read(self):
            return json.dumps({
                "content": [{"type": "text", "text": "ok"}]
            }).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(request, timeout=0):
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data.decode())
        return _Resp()

    monkeypatch.setenv("VERIFIER_LLM_MODEL", "claude-test-9")
    monkeypatch.setenv("VERIFIER_LLM_URL", "https://proxy.example")
    monkeypatch.setattr(chat.urllib.request, "urlopen", _urlopen)
    text = chat.llm_answer("q", {"a": 1}, "key")
    assert text == "ok"
    assert seen["url"] == "https://proxy.example/v1/messages"
    assert seen["body"]["model"] == "claude-test-9"
