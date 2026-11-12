"""Capture handoff: list, download, prove-the-hash retire.
Evidence mirrors and never retires."""
import hashlib
import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

from strategy_verifier.ui.server import make_ui_server


def _serve(workdir):
    server = make_ui_server(Path(workdir), 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _post(base, path, payload):
    request = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=6) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(base, path):
    with urllib.request.urlopen(base + path, timeout=6) as response:
        return json.loads(response.read().decode("utf-8"))


def _age(path, seconds=3600):
    stamp = path.stat().st_mtime - seconds
    os.utime(path, (stamp, stamp))


def test_capture_handoff_protocol(tmp_path):
    root = tmp_path / "data" / "captures"
    root.mkdir(parents=True)
    cold = root / "old-900s-1.jsonl"
    cold.write_text('{"n":1}\n{"n":2}\n', encoding="utf-8")
    _age(cold)
    hot = root / "new-900s-1.jsonl"
    hot.write_text('{"n":3}\n', encoding="utf-8")

    server, base = _serve(tmp_path)
    try:
        listing = _get_json(base, "/api/captures")["captures"]
        by_name = {entry["name"]: entry for entry in listing}
        assert by_name["new-900s-1.jsonl"]["active"] is True
        assert by_name["old-900s-1.jsonl"]["active"] is False

        with urllib.request.urlopen(
                base + "/captures/old-900s-1.jsonl", timeout=6) as r:
            blob = r.read()
        assert blob == cold.read_bytes()
        digest = hashlib.sha256(blob).hexdigest()

        wrong = _post(base, "/api/captures/retire", {
            "name": "old-900s-1.jsonl", "sha256": "0" * 64})
        assert "mismatch" in wrong["error"]
        assert cold.exists()

        refused = _post(base, "/api/captures/retire", {
            "name": "new-900s-1.jsonl",
            "sha256": hashlib.sha256(hot.read_bytes()).hexdigest()})
        assert "active" in refused["error"]
        assert hot.exists()

        retired = _post(base, "/api/captures/retire", {
            "name": "old-900s-1.jsonl", "sha256": digest})
        assert retired["retired"] == "old-900s-1.jsonl"
        assert retired["sha256"] == digest
        assert not cold.exists()

        traversal = _post(base, "/api/captures/retire", {
            "name": "../state.jsonl", "sha256": digest})
        assert "no such capture" in traversal["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_reports_mirror_lists_and_serves_jsonl(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    evidence = reports / "detections-fam.jsonl"
    evidence.write_text('{"t":"x"}\n', encoding="utf-8")

    server, base = _serve(tmp_path)
    try:
        names = [entry["name"] for entry
                 in _get_json(base, "/api/reports")["reports"]]
        assert "detections-fam.jsonl" in names
        with urllib.request.urlopen(
                base + "/reports/detections-fam.jsonl", timeout=6) as r:
            assert r.read() == evidence.read_bytes()
    finally:
        server.shutdown()
        server.server_close()

def test_health_is_auth_exempt(tmp_path, monkeypatch):
    """Platform healthchecks carry no credentials: /health answers
    without auth while every other path stays guarded."""
    monkeypatch.setenv("VERIFIER_WEB_PASSWORD", "secret")
    server, base = _serve(tmp_path)
    try:
        with urllib.request.urlopen(base + "/health", timeout=6) as r:
            assert r.read().decode() == "ok"
        try:
            urllib.request.urlopen(base + "/", timeout=6)
            raise AssertionError("unauthenticated / must fail")
        except urllib.error.HTTPError as error:
            assert error.code == 401
    finally:
        server.shutdown()
        server.server_close()

