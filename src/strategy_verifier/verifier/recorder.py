"""Live recorder (J-1): a Binance UM aggTrade WebSocket feed
written as the exact capture JSONL the converters produce.

The transform reuses the converter chain byte for byte —
map_binance_um_aggtrade_row, PublicTradeStreamEvent, and the same
line renderer — so a recorded file and a converted file of the same
trades are identical, and every downstream instrument (integrity
gate, graph engine, reading) accepts recordings unchanged.
Reconnects never contaminate a file: each connection writes its own
session file (base, then -s2, -s3), matching the prefix session
mode, and existing files are never overwritten. The WebSocket client
is a minimal, dependency-free RFC 6455 implementation: TLS,
handshake, client masking, ping and pong, fragmented text.

Research diagnostics only. No orders, no advice, no trading paths.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import time
from dataclasses import dataclass
from pathlib import Path

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.public_market_data.binance_um_aggtrade_history import (
    BINANCE_UM_AGGTRADE_SOURCE,
    PublicTradeStreamEvent,
    _event_to_jsonl_line,
    map_binance_um_aggtrade_row,
)

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def transform_ws_message(
    payload: dict, symbol: str, sequence: int
) -> str:
    if not isinstance(payload, dict) or payload.get("e") != "aggTrade":
        raise DomainValidationError("payload must be an aggTrade event")
    if str(payload.get("s", "")).upper() != symbol.upper():
        raise DomainValidationError(
            f"symbol mismatch: {payload.get('s')} != {symbol}"
        )
    row = [
        str(payload.get("a", "")), str(payload.get("p", "")),
        str(payload.get("q", "")), str(payload.get("f", "")),
        str(payload.get("l", "")), str(payload.get("T", "")),
        "true" if payload.get("m") else "false",
    ]
    tick, raw_payload = map_binance_um_aggtrade_row(row, symbol.upper())
    event = PublicTradeStreamEvent(
        sequence=sequence,
        received_at=tick.timestamp,
        source=BINANCE_UM_AGGTRADE_SOURCE,
        raw_payload=raw_payload,
        mapped_trade_tick=tick,
        mapping_error=None,
        is_duplicate=False,
        is_out_of_order=False,
    )
    return _event_to_jsonl_line(event)


def _session_path(base: Path, session: int) -> Path:
    if session == 1:
        return base
    return base.with_name(f"{base.stem}-s{session}{base.suffix}")


@dataclass
class RecorderSummary:
    sessions: int
    events: int
    files: list[str]


class SessionWriter:
    def __init__(self, base: Path) -> None:
        self._base = base
        self._session = 0
        self._handle = None
        self.events = 0
        self.file_events = 0
        self.files: list[str] = []

    def rotate(self) -> Path:
        self.close()
        self._session += 1
        path = _session_path(self._base, self._session)
        if path.exists():
            raise DomainValidationError(
                f"capture exists and recordings never overwrite: {path}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("w", encoding="utf-8")
        self.file_events = 0
        self.files.append(str(path))
        return path

    def write_line(self, line: str) -> None:
        if self._handle is None:
            self.rotate()
        self._handle.write(line)
        self.events += 1
        self.file_events += 1

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    @property
    def sessions(self) -> int:
        return self._session


# ---- 최소 RFC 6455 클라이언트 ----

def ws_encode_frame(opcode: int, payload: bytes) -> bytes:
    header = bytes([0x80 | opcode])
    mask = os.urandom(4)
    length = len(payload)
    if length < 126:
        header += bytes([0x80 | length])
    elif length < 1 << 16:
        header += bytes([0x80 | 126]) + struct.pack(">H", length)
    else:
        header += bytes([0x80 | 127]) + struct.pack(">Q", length)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return header + mask + masked


class BufferedSocket:
    """Socket wrapper that serves handshake leftovers first, so a
    frame arriving in the same TCP segment as the 101 response is
    never lost and framing never desynchronizes."""

    def __init__(self, sock, leftover: bytes = b"") -> None:
        self._sock = sock
        self._leftover = leftover

    def recv(self, count: int) -> bytes:
        if self._leftover:
            piece = self._leftover[:count]
            self._leftover = self._leftover[count:]
            return piece
        return self._sock.recv(count)

    def sendall(self, data: bytes) -> None:
        self._sock.sendall(data)

    def settimeout(self, value) -> None:
        self._sock.settimeout(value)

    def close(self) -> None:
        self._sock.close()


def _read_exact(sock, count: int) -> bytes:
    chunks = b""
    while len(chunks) < count:
        piece = sock.recv(count - len(chunks))
        if not piece:
            raise ConnectionError("socket closed")
        chunks += piece
    return chunks


def ws_read_frame(sock) -> tuple[int, bool, bytes]:
    first, second = _read_exact(sock, 2)
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        (length,) = struct.unpack(">H", _read_exact(sock, 2))
    elif length == 127:
        (length,) = struct.unpack(">Q", _read_exact(sock, 8))
    mask = _read_exact(sock, 4) if masked else b""
    payload = _read_exact(sock, length) if length else b""
    if masked:
        payload = bytes(
            b ^ mask[i % 4] for i, b in enumerate(payload)
        )
    return opcode, fin, payload


def ws_messages(sock):
    """Yield decoded text messages; answer pings; stop on close."""
    buffer = b""
    while True:
        opcode, fin, payload = ws_read_frame(sock)
        if opcode == 9:
            sock.sendall(ws_encode_frame(10, payload))
            continue
        if opcode == 8:
            return
        if opcode in (1, 0):
            buffer += payload
            if fin:
                text = buffer.decode("utf-8")
                buffer = b""
                yield text


def ws_connect(host: str, path: str, timeout: float = 10.0):
    port = 443
    if ":" in host:
        host, port_text = host.rsplit(":", 1)
        port = int(port_text)
    context = ssl.create_default_context()
    raw = socket.create_connection((host, port), timeout=timeout)
    sock = context.wrap_socket(raw, server_hostname=host)
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(request.encode())
    response = b""
    while b"\r\n\r\n" not in response:
        response += sock.recv(4096)
    head = response.split(b"\r\n\r\n", 1)[0].decode(errors="replace")
    expected = base64.b64encode(
        hashlib.sha1((key + _GUID).encode()).digest()
    ).decode()
    if "101" not in head.split("\r\n")[0] or expected not in head:
        raise ConnectionError(f"handshake refused: {head[:120]}")
    leftover = response.split(b"\r\n\r\n", 1)[1]
    sock.settimeout(30.0)
    return BufferedSocket(sock, leftover)


def run_record(
    symbol: str,
    output: Path,
    duration_seconds: int | None,
    max_messages: int | None,
    replay_file: Path | None,
    host: str = "fstream.binance.com",
    stall_rotate: bool = True,
) -> RecorderSummary:
    writer = SessionWriter(output)
    sequence = 0
    deadline = (
        time.monotonic() + duration_seconds
        if duration_seconds else None
    )

    def _limits_hit() -> bool:
        if max_messages is not None and sequence >= max_messages:
            return True
        return deadline is not None and time.monotonic() >= deadline

    try:
        if replay_file is not None:
            writer.rotate()
            for raw_line in replay_file.read_text(
                encoding="utf-8"
            ).splitlines():
                stripped = raw_line.strip()
                if not stripped:
                    continue
                if stripped == "__gap__":
                    writer.rotate()
                    continue
                writer.write_line(transform_ws_message(
                    json.loads(stripped), symbol, sequence
                ))
                sequence += 1
                if _limits_hit():
                    break
        else:
            path = f"/ws/{symbol.lower()}@aggTrade"
            empty_streak = 0

            def _back_off() -> None:
                if not _limits_hit():
                    time.sleep(min(
                        2.0 * (2 ** min(empty_streak - 1, 7)),
                        300.0,
                    ))

            while not _limits_hit():
                try:
                    sock = ws_connect(host, path)
                except (OSError, ConnectionError) as exc:
                    empty_streak += 1
                    print(f"recorder: connect failed "
                          f"(streak={empty_streak}): {exc}",
                          flush=True)
                    _back_off()
                    continue
                # A session file opens on the first message, never
                # on the handshake: an empty reconnect reuses the
                # current file instead of rotating a new one.
                if writer.sessions == 0:
                    writer.rotate()
                elif writer.file_events > 0:
                    writer.close()
                before = writer.events
                reason = "clean close"
                try:
                    for text in ws_messages(sock):
                        writer.write_line(transform_ws_message(
                            json.loads(text), symbol, sequence
                        ))
                        sequence += 1
                        if _limits_hit():
                            break
                except TimeoutError:
                    reason = "stall timeout"
                    if not stall_rotate:
                        raise
                except (OSError, ConnectionError, ValueError) as exc:
                    reason = f"{type(exc).__name__}: {exc}"
                finally:
                    try:
                        sock.close()
                    except OSError:
                        pass
                if writer.events == before:
                    empty_streak += 1
                    print(f"recorder: empty session ({reason}) "
                          f"streak={empty_streak}", flush=True)
                    _back_off()
                else:
                    if empty_streak:
                        print("recorder: stream recovered",
                              flush=True)
                    empty_streak = 0
                if _limits_hit():
                    break
    except KeyboardInterrupt:
        pass
    finally:
        writer.close()
    return RecorderSummary(
        sessions=writer.sessions,
        events=writer.events,
        files=writer.files,
    )
