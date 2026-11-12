# Personal Web Deployment (Railway)

One service, one volume, three variables. Single-user by design —
set the password or the instance is open to the internet.

1. Railway → New Project → Deploy from GitHub repo (this repo; the
   Dockerfile is picked up automatically).
2. Add a Volume mounted at `/data` (the working folder: captures,
   reports, state, registry all persist across deploys).
3. Variables:
   - `VERIFIER_WEB_PASSWORD` — required in practice; any username,
     this password (HTTP Basic Auth on every request).
   - `VERIFIER_SYMBOL` — default `BTCUSDT`.
   - `VERIFIER_STREAM_HOST` — default `stream.binance.com:9443`
     (spot). On a non-KR region you may use the UM futures host
     `fstream.binance.com`.
   - `VERIFIER_DEFINITION` — default `ma_touch_trend.toml`.
4. Networking → Generate Domain. Open it, enter the password, and
   the workspace is already following: every boot opens a fresh
   dated capture file and auto-attaches it. Nothing is ever
   overwritten; old sessions remain as evidence in the volume.

## Field notes (2026-08-02 first deployment)

- Variables do NOT reach a running container by themselves: after
  adding or editing any variable, a new deployment must run (push
  to main, or Redeploy the LATEST successful card — never an old
  FAILED card, which resurrects old code).
- Diagnosis one-liner from any terminal:
  curl -s -o /dev/null -w "%{http_code}\n" https://<domain>
  200 = auth not active (old container); 401 = password live.
- HEAD requests (curl -I) return 501 by design; harmless.
- The docker VOLUME instruction is forbidden by Railway; the
  attached Railway volume at /data is the only persistence.

Research diagnostics only. No orders, no advice, no trading paths.
