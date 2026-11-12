"""Tests for the UI shell (part one): loopback-only binding, the
boundary text on every page, the triage endpoint mirroring the
refusal engine with guidance, admissible save producing a canonical
claim TOML that the CLI front door re-admits, duplicate-save refusal,
and the absence of instruction vocabulary in the shell."""

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from strategy_verifier.ui.server import make_ui_server


def _serve(tmp_path):
    server = make_ui_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def _post(base, path, payload):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def test_shell_pages_and_boundary(tmp_path):
    server, base = _serve(tmp_path)
    try:
        assert server.server_address[0] == "127.0.0.1"
        for path in ("/", "/claim", "/docs"):
            with urllib.request.urlopen(base + path) as response:
                page = response.read().decode("utf-8")
            assert "Research instrument only" in page
            assert "DISCLAIMER" in page
        with urllib.request.urlopen(base + "/") as response:
            board = response.read().decode("utf-8")
        assert "No families yet" in board
        lowered = board.lower()
        for forbidden in ("buy", "sell", "signal", "entry",
                          "recommend"):
            assert forbidden not in lowered, forbidden
    finally:
        server.shutdown()
        server.server_close()


def test_triage_refusal_and_admission(tmp_path):
    server, base = _serve(tmp_path)
    try:
        refused = _post(base, "/api/triage", {
            "family": "x",
            "direction": "느낌 좋을 때 롱",
            "data": ["orderbook"],
            "conditions": ["적절히 눌리면"],
        })
        assert refused["admissible"] is False
        codes = {reason["code"] for reason in refused["reasons"]}
        assert "HIDDEN_DISCRETION" in codes
        assert "UNAVAILABLE_DATA" in codes
        assert all(reason["guidance"] for reason in refused["reasons"])

        admissible = _post(base, "/api/triage", {
            "family": "ui_fam",
            "direction": "종가가 평균 위이고 평균이 상승이면 롱, 미러면 숏",
            "data": ["klines"],
            "conditions": ["종가와 평균의 거리가 delta 이하"],
        })
        assert admissible["admissible"] is True
        assert admissible["reasons"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_save_roundtrips_through_cli_front_door(tmp_path, capsys):
    from strategy_verifier import cli

    server, base = _serve(tmp_path)
    try:
        payload = {
            "family": "ui_fam",
            "direction": "종가가 평균 위이고 평균이 상승이면 롱, 미러면 숏",
            "data": ["klines", "trades"],
            "conditions": [
                '거리 조건 "delta" 이하',
                "게이트 20봉 경과",
            ],
        }
        saved = _post(base, "/api/save-claim", payload)
        path = Path(saved["path"])
        assert path.exists()
        assert cli.main(["verify-triage", "--claim", str(path)]) == 0
        assert "admissible: yes" in capsys.readouterr().out

        duplicate = _post(base, "/api/save-claim", payload)
        assert "already exists" in duplicate["error"]

        refused_save = _post(base, "/api/save-claim", {
            "family": "y", "direction": "감으로",
            "data": ["klines"], "conditions": ["c"],
        })
        assert "refused claim" in refused_save["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_register_and_advance_flow(tmp_path):
    # 스캐폴드: 정의(패키지 데모)와 클레임을 UI 저장 경로로 준비
    from strategy_verifier import cli

    assert cli.main(["init", "--dir", str(tmp_path)]) == 0
    server, base = _serve(tmp_path)
    try:
        _post(base, "/api/save-claim", {
            "family": "ma_touch_trend",
            "direction": "종가가 200이평 위이고 상승이면 롱, 미러면 숏",
            "data": ["klines"],
            "conditions": ["종가와 20이평의 거리가 delta 이하"],
        })
        overlap = _post(base, "/api/register", {
            "claim": "ma_touch_trend_claim.toml",
            "definition": "ma_touch_trend.toml",
            "explore": "2026-01-01..2026-06-30",
            "confirm": "2026-05-01..2026-09-30",
        })
        assert "overlap" in overlap["error"]

        registered = _post(base, "/api/register", {
            "claim": "ma_touch_trend_claim.toml",
            "definition": "ma_touch_trend.toml",
            "explore": "2026-01-01..2026-03-31",
            "confirm": "2024-01-01..2024-06-30",
        })
        assert registered["admissible"] is True
        assert registered["family"] == "ma_touch_trend"
        assert (tmp_path / "state" / "ma_touch_trend.toml").exists()
        assert (
            tmp_path / "docs" / "MA_TOUCH_TREND_REGISTRATION.md"
        ).exists()

        with urllib.request.urlopen(base + "/") as response:
            board = response.read().decode("utf-8")
        assert "ma_touch_trend" in board
        assert "registered" in board

        bad = _post(base, "/api/advance", {
            "family": "ma_touch_trend", "to": "frozen", "note": "x",
        })
        assert "allowed next" in bad["error"]

        advanced = _post(base, "/api/advance", {
            "family": "ma_touch_trend", "to": "explored", "note": "",
        })
        assert advanced["stage"] == "explored"
        assert advanced["guidance"]
        assert set(advanced["allowed"]) == {"frozen", "closed"}
    finally:
        server.shutdown()
        server.server_close()


def _wait_job(base, job_id, timeout=30.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        with urllib.request.urlopen(base + "/api/job?id=" + job_id) as r:
            job = json.loads(r.read().decode("utf-8"))
        if job["status"] != "running":
            return job
        time.sleep(0.15)
    raise AssertionError("job timeout")


def test_full_pipeline_through_ui(tmp_path, capsys):
    """측정→진단→판정→감정서→판독 전 구간을 UI API 로 완주하고,
    측정 산출물이 CLI 와 바이트 동일함을 고정한다."""
    from strategy_verifier import cli
    import tests.test_verifier_graph as graph_tests

    assert cli.main(["init", "--dir", str(tmp_path)]) == 0
    capsys.readouterr()  # init 출력 배수
    capture = graph_tests._ohlc_capture(
        tmp_path / "data" / "captures", graph_tests._REVERSAL_ROWS,
        "mini-100-1.jsonl",
    )
    # 미니 graph 정의를 작업 폴더에 배치
    raw = graph_tests._reversal_raw(["gate"])
    raw["family"] = "mini_ui"
    definition_path = tmp_path / "definitions" / "mini_ui.toml"
    lines = ['family = "mini_ui"', 'engine = "graph_v1"', "",
             "[frame]", "bar_seconds = 900", "", "[params]", 'k = "1"',
             ""]
    for spec in raw["series"]:
        lines += ["[[series]]"] + [
            f'{k} = "{v}"' if isinstance(v, str) else f"{k} = {v}"
            for k, v in spec.items()
        ] + [""]
    for spec in raw["conditions"]:
        lines.append("[[conditions]]")
        for k, v in spec.items():
            if isinstance(v, str):
                lines.append(f'{k} = "{v}"')
            elif isinstance(v, list):
                lines.append(
                    f"{k} = [" + ", ".join(f'"{i}"' for i in v) + "]"
                )
            else:
                lines.append(f"{k} = {v}")
        lines.append("")
    lines += ["[direction]",
              'long_when = ["close < slow", "slow < slow_prior"]',
              'short_when = ["close > slow", "slow > slow_prior"]',
              "", "[event]", 'when = ["gate"]', "gate_bars = 2", "",
              "[controls]",
              "stride = 50", "offset = 1", "min_distance_bars = 2",
              "", "[outcomes]", "horizons = [8]",
              'threshold_pcts = ["0.004"]']
    definition_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    server, base = _serve(tmp_path)
    try:
        # 측정 잡
        started = _post(base, "/api/measure", {
            "definition": "mini_ui.toml",
            "inputs": ["mini-100-1.jsonl"],
            "calibrate": False,
            "output": "mini_measure.txt",
        })
        job = _wait_job(base, started["job"])
        assert job["status"] == "done"
        report_path = tmp_path / "reports" / "mini_measure.txt"
        assert report_path.exists()

        # CLI 동일 인자 산출물과 바이트 동일
        exit_code = cli.main([
            "verify-run", "--definition", str(definition_path),
            "--input", str(capture),
            "--input-session-mode", "single",
        ])
        assert exit_code == 0
        kept = []
        skipping = False
        for line in capsys.readouterr().out.splitlines():
            if line.startswith("capture_integrity_manifest"):
                skipping = True
                continue
            if skipping and (line.startswith("  ") or line.startswith("- ")):
                continue
            skipping = False
            kept.append(line)
        cli_body = "\n".join(kept) + "\n"
        # UI 산출물 = CLI 본문+등식 (CLI 전용 무결성 매니페스트 줄 제외)
        assert cli_body == report_path.read_text(encoding="utf-8")

        # 중복 저장 거부
        duplicate = _post(base, "/api/measure", {
            "definition": "mini_ui.toml",
            "inputs": ["mini-100-1.jsonl"],
            "calibrate": False,
            "output": "mini_measure.txt",
        })
        assert "already exists" in duplicate["error"]

        # 진단·판정 (단일 리포트 3회 = 3마켓 취급)
        judge_payload = {
            "reports": ["mini_measure.txt"],
            "family": "mini_ui", "horizon": "8",
            "threshold": "0.004", "arm": "base_grid",
            "control": "control_grid",
        }
        diagnosed = _post(base, "/api/diagnose", judge_payload)
        assert diagnosed["path"] == "reports/mini_ui_diagnosis.txt"
        adjudicated = _post(base, "/api/verdict", judge_payload)
        assert adjudicated["path"] == "reports/mini_ui_verdict.txt"
        assert "VERDICT: MINI_UI_" in (
            tmp_path / "reports" / "mini_ui_verdict.txt"
        ).read_text(encoding="utf-8")

        # 감정서
        certified = _post(base, "/api/certificate", judge_payload)
        assert certified["path"] == "reports/mini_ui_certificate.html"
        assert (
            tmp_path / "reports" / "mini_ui_certificate.html"
        ).exists()

        # 판독 잡 (UNADJUDICATED 부착 확인)
        read_started = _post(base, "/api/read", {
            "definition": "mini_ui.toml",
            "inputs": ["mini-100-1.jsonl"],
            "last_bars": "2",
        })
        read_job = _wait_job(base, read_started["job"])
        assert read_job["status"] == "done"
        assert "adjudication: UNADJUDICATED" in read_job["detail"]
        assert "equation:" in read_job["detail"]

        # 오류 경로: 없는 정의
        missing = _post(base, "/api/measure", {
            "definition": "ghost.toml", "inputs": ["mini-100-1.jsonl"],
            "calibrate": False, "output": "x.txt",
        })
        assert "definition file" in missing["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_report_serving_and_traversal_guard(tmp_path):
    (tmp_path / "reports").mkdir(parents=True)
    (tmp_path / "reports" / "sheet.html").write_text(
        "<html>검증 감정서</html>", encoding="utf-8"
    )
    (tmp_path / "reports" / "note.txt").write_text(
        "본문", encoding="utf-8"
    )
    (tmp_path / "secret.txt").write_text("밖", encoding="utf-8")
    server, base = _serve(tmp_path)
    try:
        with urllib.request.urlopen(base + "/reports/sheet.html") as r:
            assert r.status == 200
            assert "text/html" in r.headers["Content-Type"]
            assert "검증 감정서" in r.read().decode("utf-8")
        with urllib.request.urlopen(base + "/reports/note.txt") as r:
            assert "text/plain" in r.headers["Content-Type"]
        for bad in ("/reports/../secret.txt", "/reports/ghost.html",
                    "/reports/note.md"):
            try:
                urllib.request.urlopen(base + bad)
                raise AssertionError(f"served: {bad}")
            except urllib.error.HTTPError as error:
                assert error.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_docs_serving_and_guard(tmp_path):
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "docs" / "DISCLAIMER.md").write_text(
        "면책", encoding="utf-8"
    )
    (tmp_path / "docs" / "secret.md").write_text("x", encoding="utf-8")
    server, base = _serve(tmp_path)
    try:
        with urllib.request.urlopen(base + "/docs/DISCLAIMER.md") as r:
            assert r.status == 200
            assert "면책" in r.read().decode("utf-8")
        for bad in ("/docs/secret.md", "/docs/../pyproject.toml"):
            try:
                urllib.request.urlopen(base + bad)
                raise AssertionError(bad)
            except urllib.error.HTTPError as error:
                assert error.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_pages_scripts_parse_and_mobile_nav(tmp_path):
    import re
    import shutil
    import subprocess

    import pytest as _pytest

    from strategy_verifier.verifier import initialize_family_state

    if shutil.which("node") is None:
        _pytest.skip("node unavailable")
    initialize_family_state(tmp_path / "state", "fam_x")
    server, base = _serve(tmp_path)
    try:
        for route in ("/", "/claim", "/register", "/measure",
                      "/judge", "/read"):
            with urllib.request.urlopen(base + route) as r:
                page = r.read().decode("utf-8")
            assert "mobile-head" in page and "MENU" in page, route
            for script in re.findall(
                r"<script>(.*?)</script>", page, re.S
            ):
                check = tmp_path / "chk.js"
                check.write_text(script, encoding="utf-8")
                result = subprocess.run(
                    ["node", "--check", str(check)],
                    capture_output=True, text=True,
                )
                assert result.returncode == 0, (route, result.stderr)
    finally:
        server.shutdown()
        server.server_close()


def test_selectors_exclude_pipeline_artifacts(tmp_path):
    """Register lists only *_claim.toml; Judge hides diagnosis/verdict
    artifacts from the measurement-report checkboxes."""
    (tmp_path / "claims").mkdir()
    (tmp_path / "claims" / "fam_claim.toml").write_text("x", encoding="utf-8")
    (tmp_path / "claims" / "fam_registration.toml").write_text(
        "x", encoding="utf-8")
    (tmp_path / "reports").mkdir()
    for name in ("fam_measure.txt", "fam_diagnosis.txt", "fam_verdict.txt"):
        (tmp_path / "reports" / name).write_text("x", encoding="utf-8")
    server, base = _serve(tmp_path)
    try:
        with urllib.request.urlopen(base + "/register") as response:
            register = response.read().decode("utf-8")
        assert "fam_claim.toml" in register
        assert "fam_registration.toml" not in register

        with urllib.request.urlopen(base + "/judge") as response:
            judge = response.read().decode("utf-8")
        assert "fam_measure.txt" in judge
        assert "fam_diagnosis.txt" not in judge
        assert "fam_verdict.txt" not in judge
    finally:
        server.shutdown()
        server.server_close()


def test_empty_states_point_forward(tmp_path):
    """Empty capture and report lists name the commands that fill them."""
    server, base = _serve(tmp_path)
    try:
        with urllib.request.urlopen(base + "/measure") as response:
            measure = response.read().decode("utf-8")
        assert "record a live stream" in measure
        with urllib.request.urlopen(base + "/judge") as response:
            judge = response.read().decode("utf-8")
        assert "run Measure first" in judge
        with urllib.request.urlopen(base + "/workspace") as response:
            workspace = response.read().decode("utf-8")
        assert "record or convert first" in workspace
    finally:
        server.shutdown()
        server.server_close()


def test_judge_family_is_a_registered_select(tmp_path):
    """Judge offers registered families as a select, not free text."""
    from strategy_verifier import cli

    server, base = _serve(tmp_path)
    try:
        with urllib.request.urlopen(base + "/judge") as response:
            empty = response.read().decode("utf-8")
        assert "no families — register first" in empty
        assert '<input id="jfam">' not in empty
    finally:
        server.shutdown()
        server.server_close()

    assert cli.main(["init", "--dir", str(tmp_path)]) == 0
    server, base = _serve(tmp_path)
    try:
        _post(base, "/api/save-claim", {
            "family": "ma_touch_trend",
            "direction": "종가가 200이평 위이고 상승이면 롱, 미러면 숏",
            "data": ["klines"],
            "conditions": ["종가와 20이평의 거리가 delta 이하"],
        })
        _post(base, "/api/register", {
            "claim": "ma_touch_trend_claim.toml",
            "definition": "ma_touch_trend.toml",
            "explore": "2026-01-01..2026-03-31",
            "confirm": "2024-01-01..2024-06-30",
        })
        with urllib.request.urlopen(base + "/judge") as response:
            judge = response.read().decode("utf-8")
        assert "<option>ma_touch_trend</option>" in judge
    finally:
        server.shutdown()
        server.server_close()


def test_saved_claim_points_to_register(tmp_path):
    """The claim screen's save handler carries the next-step link."""
    server, base = _serve(tmp_path)
    try:
        with urllib.request.urlopen(base + "/claim") as response:
            claim = response.read().decode("utf-8")
        assert "next → Register" in claim
        assert 'href="/register"' in claim
    finally:
        server.shutdown()
        server.server_close()


def test_judge_prefills_primary_cell(tmp_path):
    """The registered definition's first outcome cell lands in the
    judge inputs without hand copying."""
    from strategy_verifier import cli

    assert cli.main(["init", "--dir", str(tmp_path)]) == 0
    server, base = _serve(tmp_path)
    try:
        _post(base, "/api/save-claim", {
            "family": "ma_touch_trend",
            "direction": "종가가 200이평 위이고 상승이면 롱, 미러면 숏",
            "data": ["klines"],
            "conditions": ["종가와 20이평의 거리가 delta 이하"],
        })
        _post(base, "/api/register", {
            "claim": "ma_touch_trend_claim.toml",
            "definition": "ma_touch_trend.toml",
            "explore": "2026-01-01..2026-03-31",
            "confirm": "2024-01-01..2024-06-30",
        })
        with urllib.request.urlopen(base + "/judge") as response:
            judge = response.read().decode("utf-8")
        assert ('"ma_touch_trend": {"horizon": "200000", '
                '"threshold": "0.005"}') in judge
        assert "fillJudgeDefaults" in judge
    finally:
        server.shutdown()
        server.server_close()


def test_vocabulary_reads_at_first_sight(tmp_path):
    """Round-two terms: coin flip named, TRANSITIONS, SOURCE, candles."""
    from strategy_verifier import cli

    assert cli.main(["init", "--dir", str(tmp_path)]) == 0
    server, base = _serve(tmp_path)
    try:
        _post(base, "/api/save-claim", {
            "family": "ma_touch_trend",
            "direction": "종가가 200이평 위이고 상승이면 롱, 미러면 숏",
            "data": ["klines"],
            "conditions": ["종가와 20이평의 거리가 delta 이하"],
        })
        _post(base, "/api/register", {
            "claim": "ma_touch_trend_claim.toml",
            "definition": "ma_touch_trend.toml",
            "explore": "2026-01-01..2026-03-31",
            "confirm": "2024-01-01..2024-06-30",
        })
        with urllib.request.urlopen(base + "/") as response:
            board = response.read().decode("utf-8")
        assert "TRANSITIONS" in board
        assert "MOVES" not in board
        with urllib.request.urlopen(base + "/judge") as response:
            judge = response.read().decode("utf-8")
        assert "the coin flip, 50%" in judge
        with urllib.request.urlopen(base + "/workspace") as response:
            workspace = response.read().decode("utf-8")
        assert "<span>SOURCE</span>" in workspace
        with urllib.request.urlopen(base + "/claim") as response:
            claim = response.read().decode("utf-8")
        assert "klines (candles)" in claim
        assert "value='klines'" in claim
    finally:
        server.shutdown()
        server.server_close()


def test_mobile_drawer_discipline(tmp_path):
    """The chat drawer ships closed, has open/close controls, and the
    boot cascade targets elements that exist."""
    server, base = _serve(tmp_path)
    try:
        with urllib.request.urlopen(base + "/workspace") as response:
            workspace = response.read().decode("utf-8")
        assert '<aside class="chat">' in workspace
        assert 'class="chat open"' not in workspace
        assert "chat-fab" in workspace
        assert "closeChat()" in workspace
        assert "?.classList.remove('open')" in workspace
        assert ".workspace .work-head{animation-delay:.02s}" in workspace
        assert ".workspace .chart-top{animation" not in workspace
        assert "button.chat-fab{position:fixed" in workspace
        assert ".chat.open+.chat-fab{display:none}" in workspace
        with urllib.request.urlopen(base + "/") as response:
            board = response.read().decode("utf-8")
        assert "?.classList.remove('open')" in board
    finally:
        server.shutdown()
        server.server_close()
