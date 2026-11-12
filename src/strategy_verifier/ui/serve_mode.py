"""Cloud-resident boot for the personal web deployment: one process
that records a live stream, serves the web UI, and auto-attaches
the workspace. Kept out of the CLI module so the thin-CLI guard
(no runtime loops in cli.py) stays meaningful.

Research diagnostics only. No orders, no advice, no trading paths.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path


def run_serve(
    workdir: Path,
    symbol: str | None,
    stream_host: str | None,
    definition: str | None,
    bind: str | None,
    port: int | None,
    out,
) -> int:
    from strategy_verifier.ui import server as ui_server
    from strategy_verifier.verifier.recorder import run_record

    workdir = workdir.resolve()
    symbol = symbol or os.environ.get("VERIFIER_SYMBOL", "BTCUSDT")
    stream_host = stream_host or os.environ.get(
        "VERIFIER_STREAM_HOST", "fstream.binance.com"
    )
    definition = definition or os.environ.get(
        "VERIFIER_DEFINITION", "ma_touch_trend.toml"
    )
    bind = bind or os.environ.get("VERIFIER_BIND", "0.0.0.0")
    port = port or int(os.environ.get("PORT", "8080"))

    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    base_name = f"{symbol.lower()}-live-{stamp}.jsonl"
    base_path = workdir / "data" / "captures" / base_name

    threading.Thread(
        target=run_record,
        kwargs={
            "symbol": symbol, "output": base_path,
            "duration_seconds": None, "max_messages": None,
            "replay_file": None, "host": stream_host,
        },
        daemon=True,
    ).start()

    def _attach_when_ready() -> None:
        for _ in range(600):
            if base_path.exists():
                error = ui_server.workspace_attach(
                    workdir, definition, base_name
                )
                out.write(
                    "workspace attach: "
                    + (error or f"{definition} on {base_name}")
                    + "\n"
                )
                return
            time.sleep(1.0)
        out.write("workspace attach: capture never appeared\n")

    threading.Thread(target=_attach_when_ready, daemon=True).start()

    if os.environ.get("VERIFIER_WEB_PASSWORD"):
        ui_server._ACCESS_LABEL = "private \u00b7 authenticated"
        out.write("auth: enabled (Basic)\n")
    else:
        out.write(
            "auth: DISABLED - VERIFIER_WEB_PASSWORD is empty or "
            "unset; the instance is open\n"
        )
    server = ui_server.make_ui_server(workdir, port=port, bind=bind)
    out.write(
        f"serving on {bind}:{port} \u00b7 recording {symbol} "
        f"from {stream_host} \u00b7 {base_name}\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
