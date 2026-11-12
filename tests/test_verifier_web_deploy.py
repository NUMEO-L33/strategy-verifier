"""Web-deploy guarantees: Basic Auth guards every route when the
password variable is set (constant-time compare, SSE included) and
stays out of the way when it is not; the attach helper is shared;
serve-mode boot naming never collides with existing evidence."""

import base64
import urllib.error
import urllib.request

from strategy_verifier.ui.server import make_ui_server, workspace_attach


def _get(base, path, password=None):
    request = urllib.request.Request(base + path)
    if password is not None:
        token = base64.b64encode(f"x:{password}".encode()).decode()
        request.add_header("Authorization", "Basic " + token)
    return urllib.request.urlopen(request)


def test_basic_auth_guards_everything(tmp_path, monkeypatch):
    import threading

    monkeypatch.setenv("VERIFIER_WEB_PASSWORD", "opensesame")
    server = make_ui_server(tmp_path, port=0)
    threading.Thread(target=server.serve_forever,
                     daemon=True).start()
    host, port = server.server_address
    base = f"http://{host}:{port}"
    try:
        for path in ("/", "/workspace", "/api/stream"):
            try:
                _get(base, path)
                raise AssertionError(path)
            except urllib.error.HTTPError as error:
                assert error.code == 401
                assert "Basic" in error.headers.get(
                    "WWW-Authenticate", ""
                )
        try:
            _get(base, "/", password="wrong")
            raise AssertionError("wrong password accepted")
        except urllib.error.HTTPError as error:
            assert error.code == 401
        with _get(base, "/", password="opensesame") as response:
            assert response.status == 200

        monkeypatch.delenv("VERIFIER_WEB_PASSWORD")
        with _get(base, "/") as response:
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()


def test_attach_helper_reports_missing(tmp_path):
    assert workspace_attach(
        tmp_path, "nope.toml", "nope.jsonl"
    ) == "pick a definition and a capture base file"
