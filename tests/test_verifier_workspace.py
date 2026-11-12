"""J-3 acceptance: the workspace follows a growing recording over
SSE — start attaches a live reader, the stream carries candles,
engine markers, latest-bar conditions, and the mandatory
adjudication with its tone; growth between events appears in the
next event; stop detaches; the inline script parses; instruction
vocabulary stays absent."""

import json
import re
import shutil
import socket
import subprocess
import urllib.request
from pathlib import Path


import tests.test_verifier_livereader as live_tests
from strategy_verifier.ui.server import make_ui_server


def _post(base, path, payload=None):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def _sse_events(host, port, count):
    sock = socket.create_connection((host, port), timeout=10)
    sock.sendall(
        b"GET /api/stream HTTP/1.1\r\nHost: x\r\n"
        b"Accept: text/event-stream\r\n\r\n"
    )
    buffer = b""
    events = []
    sock.settimeout(10)
    while len(events) < count:
        buffer += sock.recv(65536)
        while b"\n\n" in buffer:
            chunk, buffer = buffer.split(b"\n\n", 1)
            for line in chunk.split(b"\n"):
                if line.startswith(b"data: "):
                    events.append(json.loads(line[6:].decode("utf-8")))
            if len(events) >= count:
                break
    sock.close()
    return events


def test_workspace_stream_follows_growth(tmp_path, capsys):
    import threading

    from strategy_verifier import cli

    messages = live_tests._messages()
    half = len(messages) // 2
    replay = tmp_path / "m1.jsonl"
    replay.write_text(
        "\n".join(json.dumps(m) for m in messages[:half]) + "\n",
        encoding="utf-8",
    )
    captures = tmp_path / "data" / "captures"
    captures.mkdir(parents=True)
    base = captures / "live.jsonl"
    assert cli.main([
        "record", "--symbol", "BTCUSDT", "--output", str(base),
        "--replay-file", str(replay),
    ]) == 0
    capsys.readouterr()

    definitions = tmp_path / "definitions"
    definitions.mkdir()
    import tomllib  # noqa: F401
    raw = live_tests._RAW
    lines = [f'family = "{raw["family"]}"', 'engine = "graph_v1"',
             "", "[frame]", "bar_seconds = 60", "", "[params]",
             'sigma = "0.002"', ""]
    for spec in raw["series"]:
        lines += ["[[series]]"] + [
            f'{k} = "{v}"' if isinstance(v, str) else f"{k} = {v}"
            for k, v in spec.items()] + [""]
    for spec in raw["conditions"]:
        lines.append("[[conditions]]")
        for k, v in spec.items():
            lines.append(
                f'{k} = "{v}"' if isinstance(v, str) else f"{k} = {v}"
            )
        lines.append("")
    lines += ["[direction]",
              'long_when = ["close > slow", "slow > slow_prior"]',
              'short_when = ["close < slow", "slow < slow_prior"]',
              "", "[event]", 'when = ["standing", "breakout"]',
              "gate_bars = 3", "", "[controls]", "stride = 5",
              "offset = 2", "min_distance_bars = 3",
              'blocked_by = ["squeeze_now"]', "", "[outcomes]",
              "horizons = [300, 1500]",
              'threshold_pcts = ["0.004", "0.01"]']
    (definitions / "live_parity.toml").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )

    server = make_ui_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    http_base = f"http://{host}:{port}"
    try:
        page = urllib.request.urlopen(
            http_base + "/workspace"
        ).read().decode("utf-8")
        assert "LIVE RECORD VIEW" in page
        assert "No instruction to act" in page
        lowered = page.lower()
        for forbidden in ("buy", "sell", "entry", "recommend"):
            assert forbidden not in lowered, forbidden
        if shutil.which("node"):
            for index, script in enumerate(re.findall(
                r"<script>(.*?)</script>", page, re.S
            )):
                check = tmp_path / f"ws{index}.js"
                check.write_text(script, encoding="utf-8")
                result = subprocess.run(
                    ["node", "--check", str(check)],
                    capture_output=True, text=True,
                )
                assert result.returncode == 0, (index, result.stderr)

        idle_chat = _post(http_base, "/api/chat",
                          {"question": "record?"})
        assert idle_chat["mode"] == "deterministic"
        assert "No feed" in idle_chat["text"]
        assert "Ask the record" in page

        started = _post(http_base, "/api/workspace/start", {
            "definition": "live_parity.toml", "base": "live.jsonl",
        })
        assert started["family"] == "live_parity"

        first = _sse_events(host, port, 1)[0]
        assert first["tone"] == "unadjudicated"
        assert "UNADJUDICATED" in " ".join(first["adjudication"])
        assert len(first["candles"]) > 10
        assert first["latest"] is not None
        first_bars = first["bar_count"]

        # 성장: 후반부 append → 다음 이벤트에 반영
        with base.open("a", encoding="utf-8") as handle:
            scratch = tmp_path / "m2.jsonl"
            scratch.write_text(
                "\n".join(
                    json.dumps(m) for m in messages[half:]
                ) + "\n", encoding="utf-8",
            )
            grown = tmp_path / "grown.jsonl"
            assert cli.main([
                "record", "--symbol", "BTCUSDT",
                "--output", str(grown),
                "--replay-file", str(scratch),
            ]) == 0
            capsys.readouterr()
            handle.write(grown.read_text(encoding="utf-8"))

        later = _sse_events(host, port, 2)[-1]
        assert later["bar_count"] > first_bars

        # J-4: 성립 실측치·감지 증거
        assert later["event_count"] > 0
        marker = later["markers"][-1]
        assert marker["conditions"] and marker["series"]
        assert later["verdict_label"] == "UNADJUDICATED"
        evidence = tmp_path / "reports" / "detections-live_parity.jsonl"
        assert evidence.exists()
        rows = [json.loads(line) for line in
                evidence.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == later["event_count"]
        assert rows[-1]["family_verdict"] == "UNADJUDICATED"
        assert rows[-1]["series"]
        # 다음 이벤트에서 중복 append 없음
        again = _sse_events(host, port, 1)[0]
        rows_after = evidence.read_text(
            encoding="utf-8"
        ).splitlines()
        assert len(rows_after) == again["event_count"]

        live_chat = _post(http_base, "/api/chat", {
            "question": "This rule's record",
        })
        assert live_chat["mode"] == "deterministic"
        assert "live_parity" in live_chat["text"]
        assert "UNADJUDICATED" in live_chat["text"]

        assert _post(http_base, "/api/workspace/stop")["stopped"]
        idle = _sse_events(host, port, 1)[0]
        assert idle["candles"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_evidence_watermark_survives_reattach(tmp_path):
    """Stop and re-attach over the same capture: the detections file
    must not grow — the watermark is rebuilt from the file itself."""
    from strategy_verifier import cli

    assert cli.main(["init", "--dir", str(tmp_path)]) == 0
    server, base = _serve_ws(tmp_path)
    try:
        for _ in range(2):
            _post_ws(base, "/api/workspace/start", {
                "definition": "demo_quickstart.toml",
                "base": "demo-900s-1.jsonl"})
            _sse_first_frame(base)
            _post_ws(base, "/api/workspace/stop", {})
        evidence = (tmp_path / "reports"
                    / "detections-demo_quickstart.jsonl")
        assert evidence.exists()
        lines = evidence.read_text(encoding="utf-8").splitlines()
        keys = [(json.loads(line)["timestamp"], json.loads(line)["side"])
                for line in lines]
        assert len(keys) == len(set(keys)), "duplicate evidence rows"
    finally:
        server.shutdown()
        server.server_close()


def test_workspace_selector_orders_by_mtime(tmp_path):
    """Newest capture first, regardless of filename sort order."""
    import os

    captures = tmp_path / "data" / "captures"
    captures.mkdir(parents=True)
    older = captures / "z-older.jsonl"
    newer = captures / "a-newer.jsonl"
    older.write_text("{}\n", encoding="utf-8")
    newer.write_text("{}\n", encoding="utf-8")
    stamp = older.stat().st_mtime
    os.utime(older, (stamp - 120, stamp - 120))
    server, base = _serve_ws(tmp_path)
    try:
        with urllib.request.urlopen(base + "/workspace") as response:
            page = response.read().decode("utf-8")
        select = page.split('id="wbase"', 1)[1].split("</select>", 1)[0]
        first = select.split("<option>", 2)[1].split("<", 1)[0]
        assert first == "a-newer.jsonl"
    finally:
        server.shutdown()
        server.server_close()


def _serve_ws(workdir):
    server = make_ui_server(Path(workdir), 0)
    import threading
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _post_ws(base, path, payload):
    request = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=6) as response:
        return json.loads(response.read().decode("utf-8"))


def _sse_first_frame(base):
    request = urllib.request.Request(base + "/api/stream")
    with urllib.request.urlopen(request, timeout=6) as response:
        for _ in range(50):
            raw = response.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
    return {}
