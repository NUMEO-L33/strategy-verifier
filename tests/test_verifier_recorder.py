"""Tests for the live recorder: byte parity with the converter on
identical trades, session rotation on gaps with overwrite refusal,
the frame codec including masking, extended lengths, ping and
fragmentation, and the CLI seam."""

import json

import pytest

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.public_market_data import (
    BinanceUmAggTradeHistoryConfig,
    run_binance_um_aggtrade_history_conversion,
)
from strategy_verifier.verifier.recorder import (
    SessionWriter,
    transform_ws_message,
    ws_encode_frame,
    ws_messages,
    ws_read_frame,
)

_TRADES = [
    (1001, "50000.5", "0.25", 1, 2, 1750000000000, False),
    (1002, "50001.0", "1.00", 3, 3, 1750000000500, True),
    (1003, "49999.9", "0.10", 4, 6, 1750000001000, False),
]


def _ws_payload(trade):
    agg, price, qty, first, last, ts, maker = trade
    return {"e": "aggTrade", "E": ts, "s": "BTCUSDT", "a": agg,
            "p": price, "q": qty, "f": first, "l": last, "T": ts,
            "m": maker}


def test_recorded_equals_converted(tmp_path, capsys):
    from strategy_verifier import cli

    csv_lines = ["agg_trade_id,price,quantity,first_trade_id,"
                 "last_trade_id,transact_time,is_buyer_maker"]
    for agg, price, qty, first, last, ts, maker in _TRADES:
        csv_lines.append(
            f"{agg},{price},{qty},{first},{last},{ts},"
            f"{'true' if maker else 'false'}"
        )
    csv_path = tmp_path / "trades.csv"
    csv_path.write_text("\n".join(csv_lines) + "\n", encoding="utf-8")
    converted = tmp_path / "converted.jsonl"
    run_binance_um_aggtrade_history_conversion(
        BinanceUmAggTradeHistoryConfig(
            input_path=csv_path, output_path=converted,
            symbol="BTCUSDT", overwrite=True,
        )
    )

    replay = tmp_path / "messages.jsonl"
    replay.write_text(
        "\n".join(json.dumps(_ws_payload(t)) for t in _TRADES) + "\n",
        encoding="utf-8",
    )
    recorded = tmp_path / "recorded.jsonl"
    assert cli.main([
        "record", "--symbol", "BTCUSDT",
        "--output", str(recorded),
        "--replay-file", str(replay),
    ]) == 0
    out = capsys.readouterr().out
    assert "sessions: 1" in out and "events: 3" in out
    assert recorded.read_bytes() == converted.read_bytes()


def test_session_rotation_and_refusal(tmp_path):
    from strategy_verifier import cli

    replay = tmp_path / "messages.jsonl"
    replay.write_text(
        json.dumps(_ws_payload(_TRADES[0])) + "\n__gap__\n"
        + json.dumps(_ws_payload(_TRADES[1])) + "\n",
        encoding="utf-8",
    )
    base = tmp_path / "live.jsonl"
    assert cli.main([
        "record", "--symbol", "BTCUSDT", "--output", str(base),
        "--replay-file", str(replay),
    ]) == 0
    second = tmp_path / "live-s2.jsonl"
    assert base.exists() and second.exists()
    assert len(base.read_text().splitlines()) == 1
    assert len(second.read_text().splitlines()) == 1

    with pytest.raises(DomainValidationError):
        SessionWriter(base).rotate()

    with pytest.raises(DomainValidationError):
        transform_ws_message(
            _ws_payload(_TRADES[0]) | {"s": "ETHUSDT"}, "BTCUSDT", 0
        )


class _FakeSocket:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.sent = b""

    def recv(self, count: int) -> bytes:
        piece, self._data = self._data[:count], self._data[count:]
        return piece

    def sendall(self, data: bytes) -> None:
        self.sent += data


def test_frame_codec_ping_and_fragmentation():
    # 클라이언트 인코딩(마스킹) → 서버측 해독 재현
    frame = ws_encode_frame(1, b"hello")
    opcode, fin, payload = ws_read_frame(_FakeSocket(frame))
    assert (opcode, fin, payload) == (1, True, b"hello")

    big = b"x" * 300  # 126 확장 길이 경로
    opcode, fin, payload = ws_read_frame(
        _FakeSocket(ws_encode_frame(2, big))
    )
    assert payload == big

    # 서버발(비마스킹) ping → pong 응답, 조각난 텍스트 조립
    def server_frame(opcode, payload, fin=True):
        header = bytes([(0x80 if fin else 0) | opcode,
                        len(payload)])
        return header + payload

    stream = (
        server_frame(9, b"pp")
        + server_frame(1, b"ab", fin=False)
        + server_frame(0, b"cd")
        + server_frame(8, b"")
    )
    sock = _FakeSocket(stream)
    texts = list(ws_messages(sock))
    assert texts == ["abcd"]
    pong_opcode, _, pong_payload = ws_read_frame(
        _FakeSocket(sock.sent)
    )
    assert (pong_opcode, pong_payload) == (10, b"pp")


def test_safety_sanction_is_narrow(tmp_path):
    """성역은 (recorder.py, 해당 용어) 한 쌍뿐 — 같은 용어라도 다른
    파일이면 여전히 격추된다."""
    from strategy_verifier.safety.review import (
        SANCTIONED_SOURCE_TERMS,
        _scan_source_terms,
    )

    assert list(SANCTIONED_SOURCE_TERMS) == [
        ("verifier/recorder.py", "web" + "socket")
    ]
    rogue = tmp_path / "verifier"
    rogue.mkdir()
    bad = rogue / "other.py"
    bad.write_text("web" + "socket = 1\n", encoding="utf-8")
    findings = _scan_source_terms(tmp_path, bad)
    assert findings and "dangerous source term" in findings[0].reason

    sanctioned = rogue / "recorder.py"
    sanctioned.write_text("web" + "socket = 1\n", encoding="utf-8")
    assert _scan_source_terms(tmp_path, sanctioned) == []


def test_buffered_socket_preserves_leftover_framing():
    """핸드셰이크와 같은 세그먼트로 도착한 첫 프레임이 소실되지 않고,
    프레이밍이 어긋나지 않는다."""
    from strategy_verifier.verifier.recorder import BufferedSocket

    def server_frame(opcode, payload, fin=True):
        return bytes([(0x80 if fin else 0) | opcode,
                      len(payload)]) + payload

    first = server_frame(1, b'{"a":1}')
    second = server_frame(1, b'{"b":2}')
    tail_sock = _FakeSocket(second)
    buffered = BufferedSocket(tail_sock, leftover=first)
    texts = []
    gen = ws_messages(buffered)
    texts.append(next(gen))
    texts.append(next(gen))
    assert texts == ['{"a":1}', '{"b":2}']


def test_dead_reconnects_reuse_one_file_and_back_off(tmp_path, monkeypatch, capsys):
    """Handshakes that deliver nothing must not rotate new session
    files, and the retry pauses must grow."""
    from strategy_verifier.verifier import recorder as rec

    sleeps = []
    monkeypatch.setattr(rec.time, "sleep", lambda s: sleeps.append(s))

    class _Sock:
        def close(self):
            pass

    calls = {"n": 0}

    def fake_connect(host, path):
        calls["n"] += 1
        return _Sock()

    def fake_messages(sock):
        if calls["n"] < 4:
            return iter(())
        return iter([
            json.dumps(_ws_payload(_TRADES[0])),
            json.dumps(_ws_payload(_TRADES[1])),
        ])

    monkeypatch.setattr(rec, "ws_connect", fake_connect)
    monkeypatch.setattr(rec, "ws_messages", fake_messages)

    base = tmp_path / "live.jsonl"
    summary = rec.run_record(
        "BTCUSDT", base, None, 2, None, host="example.invalid")
    assert summary.events == 2
    assert summary.sessions == 1
    assert base.exists()
    assert len(base.read_text(encoding="utf-8").splitlines()) == 2
    assert not (tmp_path / "live-s2.jsonl").exists()
    assert len(sleeps) == 3
    assert sleeps == sorted(sleeps) and sleeps[0] < sleeps[-1]
    logged = capsys.readouterr().out
    assert "empty session" in logged
    assert "stream recovered" in logged


def test_reconnect_after_data_still_rotates(tmp_path, monkeypatch):
    """Session boundaries survive: a reconnect after a data-bearing
    session opens the next -sN file."""
    from strategy_verifier.verifier import recorder as rec

    monkeypatch.setattr(rec.time, "sleep", lambda s: None)

    class _Sock:
        def close(self):
            pass

    calls = {"n": 0}

    def fake_connect(host, path):
        calls["n"] += 1
        return _Sock()

    def fake_messages(sock):
        index = calls["n"] - 1
        return iter([json.dumps(_ws_payload(_TRADES[index]))])

    monkeypatch.setattr(rec, "ws_connect", fake_connect)
    monkeypatch.setattr(rec, "ws_messages", fake_messages)

    base = tmp_path / "live.jsonl"
    summary = rec.run_record(
        "BTCUSDT", base, None, 2, None, host="example.invalid")
    assert summary.events == 2
    assert summary.sessions == 2
    assert len(base.read_text(encoding="utf-8").splitlines()) == 1
    second = tmp_path / "live-s2.jsonl"
    assert second.exists()
    assert len(second.read_text(encoding="utf-8").splitlines()) == 1
