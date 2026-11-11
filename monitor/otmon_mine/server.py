#!/usr/bin/env python3
"""otmon-mine web server: OTMon-style dashboard + operator HMI for the ot-sim mine demo.

Python stdlib HTTP server, all assets served from ./static (no CDN, works offline).

  GET  /                 dashboard (single page app, hash routes)
  GET  /health           JSON health; HTTP 200 when every device is polled OK, else 503
  GET  /api/status       devices, values, comm state, alarms, recent events
  GET  /api/history      ?keys=dev:point,dev:point&seconds=600
  GET  /api/events       ?limit=200
  POST /api/ack          {"key": "<alarm key>"} or {} for ack-all
  POST /api/write        {"device": "...", "point": "...", "value": 1}   (HMI write over Modbus)
  POST /api/scenario     {"action": "auto_on" | "auto_off" | "reset" | "rogue"}

POSTs need the header `X-OTMon-HMI: 1` (blocks cross-site form posts) and a JSON body.
The Host header must be in OTMON_ALLOWED_HOSTS (default localhost,127.0.0.1) - same
local-only idea as OTMon's dashboard; in Docker the port is published on 127.0.0.1 only.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .engine import Engine, load_config

STATIC = Path(__file__).parent / "static"
ENGINE: Engine | None = None
ALLOWED_HOSTS: set[str] = set()


class Handler(BaseHTTPRequestHandler):
    server_version = "otmon-mine"

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
                         "connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _host_ok(self):
        if "*" in ALLOWED_HOSTS:
            return True
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ALLOWED_HOSTS

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return self._send(403, {"error": "Host header not allowed (set OTMON_ALLOWED_HOSTS)"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/health", "/healthz", "/api/health"):
            h = ENGINE.health()
            return self._send(200 if h["status"] == "ok" else 503, h)
        if u.path == "/api/status":
            return self._send(200, ENGINE.status())
        if u.path == "/api/history":
            keys = [k for k in q.get("keys", [""])[0].split(",") if k]
            secs = min(max(int(q.get("seconds", ["600"])[0]), 10), 3600)
            return self._send(200, ENGINE.get_history(keys, secs))
        if u.path == "/api/events":
            lim = min(max(int(q.get("limit", ["200"])[0]), 1), 2000)
            with ENGINE.lock:
                ev = list(ENGINE.events)[-lim:][::-1]
            return self._send(200, ev)
        path = "index.html" if u.path in ("/", "/index.html") else u.path.removeprefix("/static/")
        f = (STATIC / path).resolve()
        if STATIC.resolve() in f.parents and f.is_file():
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            return self._send(200, f.read_bytes(), ctype)
        return self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self._host_ok():
            return self._send(403, {"error": "Host header not allowed"})
        if self.headers.get("X-OTMon-HMI") != "1":
            return self._send(403, {"error": "missing X-OTMon-HMI header"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n <= 65536 else None
            if not isinstance(body, dict):
                raise ValueError("JSON object body required")
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        u = urlparse(self.path)
        user = str(body.get("user") or "operator")[:40]
        try:
            if u.path == "/api/ack":
                return self._send(200, {"acked": ENGINE.ack(body.get("key"))})
            if u.path == "/api/write":
                ENGINE.write(str(body["device"]), str(body["point"]), body["value"], user=user)
                return self._send(200, {"ok": True})
            if u.path == "/api/scenario":
                action = str(body.get("action"))
                if action not in ("auto_on", "auto_off", "reset", "rogue"):
                    return self._send(400, {"error": "action must be auto_on, auto_off, reset or rogue"})
                errs = ENGINE.scenario(action, user=user)
                if action == "reset":
                    ENGINE.ack()
                return self._send(200 if not errs else 502, {"ok": not errs, "errors": errs})
        except PermissionError as e:
            return self._send(403, {"error": str(e)})
        except (KeyError, ValueError, TypeError) as e:
            return self._send(400, {"error": f"bad request: {e}"})
        except Exception as e:  # noqa: BLE001  (Modbus/network errors)
            return self._send(502, {"error": f"device write failed: {e.__class__.__name__}: {e}"})
        return self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass


def main(argv=None):
    global ENGINE, ALLOWED_HOSTS
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=os.environ.get("OTMON_CONFIG", str(Path(__file__).parent.parent / "config" / "mine.yaml")))
    ap.add_argument("--bind", default=os.environ.get("OTMON_BIND", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("OTMON_PORT", "8088")))
    ap.add_argument("--state-dir", default=os.environ.get("OTMON_STATE_DIR", "/tmp/otmon-mine"))
    a = ap.parse_args(argv)
    ALLOWED_HOSTS = {h.strip() for h in os.environ.get("OTMON_ALLOWED_HOSTS", "localhost,127.0.0.1,::1").split(",") if h.strip()}
    hmi = os.environ.get("OTMON_HMI_WRITES", "1") not in ("0", "false", "no")
    ENGINE = Engine(load_config(a.config), state_dir=a.state_dir, hmi_writes=hmi)
    ENGINE.start()
    srv = ThreadingHTTPServer((a.bind, a.port), Handler)
    print(f"otmon-mine dashboard on http://{a.bind}:{a.port}  (HMI writes {'ENABLED' if hmi else 'disabled'}; "
          f"allowed hosts: {','.join(sorted(ALLOWED_HOSTS))})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
