# cmnd/dashboard.py — Rebel Status Dashboard (port 3033)
# Lightweight REST API showing active sessions, token usage, task status.
# SECURITY CONTROL: SC-7 (Boundary Protection) — Bound to localhost only
# SECURITY CONTROL: AC-3 (Access Enforcement) — No auth required (localhost-only)
# DAIV CERTIFIED

import json
import os
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

DASHBOARD_PORT = int(__import__("os").environ.get("REBEL_DASHBOARD_PORT", "3033"))
_SERVER: HTTPServer | None = None
_SESSION_DATA: dict[str, Any] = {
    "started_at": None,
    "model": "unknown",
    "tokens_sent": 0,
    "tokens_received": 0,
    "cost": 0.0,
    "files_in_chat": [],
    "last_activity": None,
}


def update_session(coder) -> None:
    """Update session data from coder state."""
    global _SESSION_DATA
    _SESSION_DATA.update({
        "model": getattr(getattr(coder, "main_model", None), "name", "unknown"),
        "tokens_sent": getattr(coder, "total_tokens_sent", 0),
        "tokens_received": getattr(coder, "total_tokens_received", 0),
        "cost": getattr(coder, "total_cost", 0.0),
        "files_in_chat": list(coder.get_inchat_relative_files())[:20],
        "last_activity": datetime.now().isoformat(),
    })


class RebetHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for Rebel dashboard endpoints."""

    def log_message(self, *args) -> None:
        pass  # Silence access log

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]

        if path == "/health":
            self._send_json({"status": "ok", "service": "rebel-dashboard"})

        elif path == "/api/session":
            self._send_json(_SESSION_DATA)

        elif path == "/api/tasks":
            try:
                from cmnd.shared_state import read_tasks
                self._send_json({"tasks": read_tasks()})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif path == "/api/memory":
            try:
                from cmnd.shared_state import read_memory
                mem = read_memory()
                self._send_json({"memory": mem[:5000], "length": len(mem)})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif path == "/api/alerts":
            try:
                from cmnd.mcp_client import get_active_alerts
                alerts = get_active_alerts()
                self._send_json({"alerts": alerts})
            except Exception as e:
                self._send_json({"alerts": [], "error": str(e)})

        elif path in ("/", "/dashboard"):
            self._send_html(_dashboard_html())

        elif path == "/api/models":
            try:
                from cmnd.mcp_server import _tool_rebel_list_models
                self._send_json(_tool_rebel_list_models({}))
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif path == "/api/watch":
            try:
                from cmnd.watch_mode import is_watching, scan_once
                project_root = os.environ.get("REBEL_PROJECT_ROOT", ".")
                pending = scan_once(project_root)
                self._send_json({
                    "watching": is_watching(),
                    "pending_annotations": len(pending),
                    "annotations": pending[:20],
                })
            except Exception as e:
                self._send_json({"watching": False, "error": str(e)})

        elif path == "/api/mcp-server":
            try:
                from cmnd.mcp_server import _sessions, TOOLS, MCP_PORT
                self._send_json({
                    "port": MCP_PORT,
                    "active_sessions": len(_sessions),
                    "tools": list(TOOLS.keys()),
                })
            except Exception as e:
                self._send_json({"error": str(e)})

        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        """
        POST /api/chat — Structured session endpoint (inspired by SShadowS/aider-restapi, Apache-2.0)
          Request:  { "message": "..." }
          Response: { "messages": [{ "type": "assistant|error|system", "content": "...",
                                     "tokens": N, "cost": N }] }
        """
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        body = b""
        if length:
            body = self.rfile.read(length)

        if path == "/api/chat":
            try:
                req = json.loads(body) if body else {}
            except Exception:
                self._send_json({"error": "invalid JSON"}, 400)
                return

            message = req.get("message", "").strip()
            if not message:
                self._send_json({"error": "message is required"}, 400)
                return

            coder = _SESSION_DATA.get("_coder")
            if not coder:
                self._send_json({
                    "messages": [{
                        "type": "error",
                        "content": "No active Rebel session. Start rebel first.",
                        "tokens": 0,
                        "cost": 0.0,
                    }]
                }, 503)
                return

            tokens_before = (getattr(coder, "total_tokens_sent", 0) +
                             getattr(coder, "total_tokens_received", 0))
            cost_before = getattr(coder, "total_cost", 0.0)

            try:
                coder.run(with_message=message)
                tokens_after = (getattr(coder, "total_tokens_sent", 0) +
                                getattr(coder, "total_tokens_received", 0))
                cost_after = getattr(coder, "total_cost", 0.0)
                self._send_json({
                    "messages": [{
                        "type": "assistant",
                        "content": "Rebel processed the request.",
                        "tokens": tokens_after - tokens_before,
                        "cost": round(cost_after - cost_before, 6),
                    }]
                })
            except Exception as e:
                self._send_json({
                    "messages": [{
                        "type": "error",
                        "content": str(e),
                        "tokens": 0,
                        "cost": 0.0,
                    }]
                }, 500)
        else:
            self._send_json({"error": "Not found"}, 404)


def _dashboard_html() -> str:
    """Minimal dark-themed Rebel dashboard."""
    uptime = "–"
    if _SESSION_DATA["started_at"]:
        s = int(time.time() - _SESSION_DATA["started_at"])
        uptime = f"{s // 3600}h {(s % 3600) // 60}m"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="10">
<title>Rebel Dashboard</title>
<style>
  body {{ background: #080808; color: #E0E0E0; font-family: Calibri, Arial, sans-serif; margin: 0; padding: 24px; }}
  h1 {{ color: #76B900; font-size: 1.4rem; border-bottom: 1px solid #333; padding-bottom: 8px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin: 16px 0; }}
  .card {{ background: #121212; border-radius: 8px; padding: 16px; }}
  .card-value {{ font-size: 1.6rem; color: #76B900; font-family: 'Courier New', monospace; }}
  .card-label {{ font-size: 0.75rem; color: #888; text-transform: uppercase; margin-top: 4px; }}
  .badge {{ background: #76B900; color: #080808; border-radius: 4px; padding: 2px 6px; font-size: 0.7rem; font-weight: bold; }}
  a {{ color: #76B900; }}
</style>
</head>
<body>
<h1>⚡ Rebel — CMND Center AI Environment</h1>
<div class="grid">
  <div class="card">
    <div class="card-value">{_SESSION_DATA['model'].split('/')[-1]}</div>
    <div class="card-label">Active Model</div>
  </div>
  <div class="card">
    <div class="card-value">{_SESSION_DATA['tokens_sent'] + _SESSION_DATA['tokens_received']:,}</div>
    <div class="card-label">Tokens Used</div>
  </div>
  <div class="card">
    <div class="card-value">${_SESSION_DATA['cost']:.4f}</div>
    <div class="card-label">Session Cost</div>
  </div>
  <div class="card">
    <div class="card-value">{uptime}</div>
    <div class="card-label">Session Uptime</div>
  </div>
</div>
<p>Files in chat: {', '.join(_SESSION_DATA['files_in_chat']) or 'none'}</p>
<p>Last activity: {_SESSION_DATA['last_activity'] or '–'}</p>
<p style="margin-top:24px; font-size:0.75rem; color:#555;">
  API: <a href="/api/session">/api/session</a> |
  <a href="/api/tasks">/api/tasks</a> |
  <a href="/api/memory">/api/memory</a> |
  <a href="/api/alerts">/api/alerts</a><br>
  v1.0.0 | PQC-TLS via pqc-proxy | DAIV CERTIFIED
</p>
</body>
</html>"""


def start_background() -> None:
    """Start dashboard HTTP server on port 3033 in a daemon thread."""
    global _SERVER

    _SESSION_DATA["started_at"] = time.time()

    try:
        # SECURITY CONTROL: SC-7 — Bind to 127.0.0.1 only
        _SERVER = HTTPServer(("127.0.0.1", DASHBOARD_PORT), RebetHandler)
        t = threading.Thread(target=_SERVER.serve_forever, daemon=True)
        t.start()
        print(f"[rebel] Dashboard: http://localhost:{DASHBOARD_PORT}")
    except OSError as e:
        print(f"[rebel] Dashboard could not start on port {DASHBOARD_PORT}: {e}")
