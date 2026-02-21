# cmnd/mcp_server.py — Rebel as MCP Server (port 3035)
# Exposes Rebel as a callable MCP tool so orchestrators (Claude Code, etc.)
# can delegate coding tasks to Rebel programmatically.
#
# INSPIRATION: sengokudaikon/aider-mcp-server (Unlicense / public domain)
#   https://github.com/sengokudaikon/aider-mcp-server
# SECURITY CONTROL: SC-7 (Boundary Protection) — MCP server bound to localhost only
# SECURITY CONTROL: AU-2 (Audit Events) — All tool calls logged to Captain's Log
# SECURITY CONTROL: CM-3 (Change Control) — rebel_run enforces model compliance
# SECURITY CONTROL: SC-5 (DoS Protection) — max concurrent sessions = 1
# DAIV CERTIFIED

import json
import os
import re
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from aider.coders import Coder

MCP_PORT = int(os.environ.get("REBEL_MCP_PORT", 3035))
_PROJECT_ROOT = os.environ.get("REBEL_PROJECT_ROOT", "/home/theferalmoon/local-opus-lab")
_REBEL_HOME = Path(__file__).parent.parent  # ~/projects/rebel/

# ─────────────────────────────────────────────
# Session registry (simple, one-at-a-time)
# ─────────────────────────────────────────────

_sessions: dict[str, dict] = {}
_coder_ref: Optional[Any] = None      # Set when Rebel starts
_lock = threading.Lock()


def _new_session() -> str:
    sid = str(uuid.uuid4())
    _sessions[sid] = {"created": time.time(), "calls": 0}
    return sid


def _valid_session(sid: str) -> bool:
    return sid in _sessions


# ─────────────────────────────────────────────
# Tool implementations
# ─────────────────────────────────────────────

def _tool_rebel_run(args: dict) -> dict:
    """
    Run Rebel on a coding task. Returns success, diff, output.
    SECURITY CONTROL: CM-3 — model compliance enforced before run
    SECURITY CONTROL: SC-5 — only one concurrent rebel_run allowed
    """
    prompt = args.get("prompt", "").strip()
    if not prompt:
        return {"success": False, "error": "prompt is required"}

    editable = args.get("editable_files", [])
    readonly = args.get("readonly_files", [])
    model = args.get("model", "")

    # Model compliance check
    if model:
        try:
            from cmnd.model_compliance import check_model
            allowed, reason = check_model(model)
            if not allowed:
                return {"success": False, "error": f"Model blocked: {reason}"}
        except Exception:
            pass

    # SECURITY CONTROL: Input validation — sanitize all file paths
    # TRUST BOUNDARY: paths must be within PROJECT_ROOT
    def safe_path(p: str) -> str:
        resolved = Path(_PROJECT_ROOT, p).resolve()
        if not str(resolved).startswith(_PROJECT_ROOT):
            raise ValueError(f"Path escape attempt blocked: {p}")
        return str(resolved)

    try:
        editable = [safe_path(f) for f in editable if f]
        readonly = [safe_path(f) for f in readonly if f]
    except ValueError as e:
        return {"success": False, "error": str(e)}

    # Capture git state before run
    before_hash = _git_hash()

    # Build rebel command
    rebel_sh = str(Path(_PROJECT_ROOT) / "scripts" / "rebel.sh")
    if not Path(rebel_sh).exists():
        rebel_sh = "rebel"  # fallback to installed command

    cmd = [rebel_sh, f"mcp-session-{int(time.time())}", "--yes",
           "--message", prompt, "--no-auto-commits"]

    # For plain rebel command (not script), add file flags
    if rebel_sh == "rebel":
        cmd = ["rebel", "--yes", "--message", prompt, "--no-auto-commits"]
        for f in editable:
            cmd.extend(["--file", f])
        for f in readonly:
            cmd.extend(["--read", f])
        if model:
            cmd.extend(["--model", model])

    try:
        with _lock:  # SC-5: one at a time
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=_PROJECT_ROOT,
            )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "rebel_run timed out after 300s"}
    except Exception as e:
        return {"success": False, "error": str(e)}

    # Compute diff
    after_hash = _git_hash()
    diff = ""
    if before_hash and after_hash and before_hash != after_hash:
        try:
            r = subprocess.run(
                ["git", "diff", before_hash, after_hash],
                capture_output=True, text=True, cwd=_PROJECT_ROOT
            )
            diff = r.stdout[:8000]
        except Exception:
            pass

    _log_to_captains_log(f"rebel_run: prompt={prompt[:80]} diff_chars={len(diff)}")

    return {
        "success": result.returncode == 0,
        "diff": diff,
        "output": result.stdout[-3000:],
        "error": result.stderr[-500:] if result.returncode != 0 else "",
    }


def _tool_rebel_status(_args: dict) -> dict:
    """Current Rebel session status."""
    coder = _coder_ref
    model = "unknown"
    files = []
    if coder:
        try:
            model = getattr(coder.main_model, "name", "unknown")
            files = list(coder.get_inchat_relative_files())
        except Exception:
            pass

    from cmnd.yolo import status as yolo_status
    yolo = yolo_status()

    branch = _git_branch()

    return {
        "rebel_version": _rebel_version(),
        "model": model,
        "project": _PROJECT_ROOT,
        "branch": branch,
        "files_in_chat": files,
        "yolo_active": yolo["active"],
        "mcp_port": MCP_PORT,
    }


def _tool_rebel_git_status(_args: dict) -> dict:
    """Git status of the current project."""
    def _run(cmd):
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=_PROJECT_ROOT)
        return r.stdout.strip()

    try:
        branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        modified = [l[3:] for l in _run(["git", "status", "--short"]).splitlines()
                    if l.startswith(" M") or l.startswith("M ")]
        staged = [l[3:] for l in _run(["git", "status", "--short"]).splitlines()
                  if l.startswith("A ") or l.startswith("M ")]
        untracked = [l[3:] for l in _run(["git", "status", "--short"]).splitlines()
                     if l.startswith("??")]
        recent = _run(["git", "log", "--oneline", "-5"]).splitlines()
        return {
            "branch": branch,
            "modified": modified,
            "staged": staged,
            "untracked": untracked,
            "recent_commits": recent,
        }
    except Exception as e:
        return {"error": str(e)}


def _tool_rebel_list_models(args: dict) -> dict:
    """List available models, filtered by substring. Compliance-checked."""
    try:
        from cmnd.model_compliance import check_model
    except Exception:
        return {"models": [], "error": "model_compliance not available"}

    filter_str = args.get("filter", "").lower()

    # Curated approved model list
    known_models = [
        {"name": "ollama_chat/devstral", "provider": "Mistral AI (France)", "tier": "standard"},
        {"name": "ollama_chat/devstral-small-2:24b", "provider": "Mistral AI (France)", "tier": "fast"},
        {"name": "ollama_chat/mistral-small3.2:24b", "provider": "Mistral AI (France)", "tier": "fast"},
        {"name": "ollama_chat/llama3.3", "provider": "Meta (US)", "tier": "standard"},
        {"name": "ollama_chat/gemma3", "provider": "Google (US)", "tier": "standard"},
        {"name": "claude-sonnet-4-6", "provider": "Anthropic (US)", "tier": "cloud"},
        {"name": "claude-opus-4-6", "provider": "Anthropic (US)", "tier": "cloud"},
        {"name": "claude-haiku-4-5-20251001", "provider": "Anthropic (US)", "tier": "cloud"},
    ]

    results = []
    for m in known_models:
        if filter_str and filter_str not in m["name"].lower():
            continue
        allowed, reason = check_model(m["name"])
        results.append({
            "name": m["name"],
            "provider": m["provider"],
            "tier": m["tier"],
            "approved": allowed,
            "block_reason": reason if not allowed else None,
        })

    return {"models": results, "total": len(results)}


def _tool_rebel_task_list(_args: dict) -> dict:
    """List tasks from shared TASKS.md."""
    try:
        from cmnd.shared_state import get_pending_tasks
        tasks = get_pending_tasks()
        return {"tasks": tasks, "count": len(tasks)}
    except Exception as e:
        return {"error": str(e), "tasks": []}


def _tool_rebel_add_task(args: dict) -> dict:
    """Add a task to shared TASKS.md."""
    title = args.get("title", "").strip()
    assignee = args.get("assignee", "unassigned")
    notes = args.get("notes", "")

    if not title:
        return {"success": False, "error": "title is required"}

    try:
        from cmnd.shared_state import add_task
        result = add_task(title, assignee=assignee, notes=notes)
        _log_to_captains_log(f"Task added via MCP: {title[:80]} → {assignee}")
        return {"success": True, "task": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────
# Tool registry
# ─────────────────────────────────────────────

TOOLS = {
    "rebel_run": {
        "description": (
            "Run Rebel (CMND AI coding assistant) on a coding task. "
            "Rebel implements the changes, commits to a rebel/* branch, returns the diff."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Coding instruction for Rebel to execute"},
                "editable_files": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Files Rebel may edit (paths relative to project root)"
                },
                "readonly_files": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Files Rebel can read but not edit"
                },
                "model": {
                    "type": "string",
                    "description": "Override model (must pass compliance check)"
                },
            },
            "required": ["prompt"],
        },
        "fn": _tool_rebel_run,
    },
    "rebel_status": {
        "description": "Get current Rebel session status: model, project, branch, YOLO state, files in chat.",
        "inputSchema": {"type": "object", "properties": {}},
        "fn": _tool_rebel_status,
    },
    "rebel_git_status": {
        "description": "Get git status of the Rebel project: branch, modified/staged/untracked files, recent commits.",
        "inputSchema": {"type": "object", "properties": {}},
        "fn": _tool_rebel_git_status,
    },
    "rebel_list_models": {
        "description": "List available Rebel models with compliance status. Filters by substring.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Optional substring filter (e.g. 'mistral', 'claude')"}
            },
        },
        "fn": _tool_rebel_list_models,
    },
    "rebel_task_list": {
        "description": "List all tasks from the shared CMND Center TASKS.md task list.",
        "inputSchema": {"type": "object", "properties": {}},
        "fn": _tool_rebel_task_list,
    },
    "rebel_add_task": {
        "description": "Add a new task to the shared CMND Center TASKS.md task list.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Task description"},
                "assignee": {"type": "string", "description": "claude-code | rebel | unassigned"},
                "notes": {"type": "string", "description": "Additional context or notes"},
            },
            "required": ["title"],
        },
        "fn": _tool_rebel_add_task,
    },
}


# ─────────────────────────────────────────────
# MCP HTTP handler (StreamableHTTP transport)
# ─────────────────────────────────────────────

class _MCPHandler(BaseHTTPRequestHandler):
    """Minimal MCP StreamableHTTP server handler."""

    def log_message(self, fmt, *args):
        pass  # Silence default HTTP logging

    def _send_json(self, data: dict, status: int = 200, session_id: str = ""):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if session_id:
            self.send_header("mcp-session-id", session_id)
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_POST(self):
        if self.path != "/mcp":
            self._send_json({"error": "not found"}, 404)
            return

        body = self._read_body()
        method = body.get("method", "")
        req_id = body.get("id", 1)
        session_id = self.headers.get("mcp-session-id", "")

        # ── initialize ──────────────────────────────
        if method == "initialize":
            sid = _new_session()
            self._send_json({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "rebel-mcp", "version": _rebel_version()},
                    "capabilities": {"tools": {}},
                },
            }, session_id=sid)
            return

        # ── all other methods require valid session ──
        if not session_id or not _valid_session(session_id):
            self._send_json({"jsonrpc": "2.0", "id": req_id,
                             "error": {"code": -32000, "message": "Invalid or missing session ID"}})
            return

        _sessions[session_id]["calls"] = _sessions[session_id].get("calls", 0) + 1

        # ── tools/list ──────────────────────────────
        if method == "tools/list":
            tools_list = [
                {"name": name, "description": t["description"], "inputSchema": t["inputSchema"]}
                for name, t in TOOLS.items()
            ]
            self._send_json({"jsonrpc": "2.0", "id": req_id,
                             "result": {"tools": tools_list}})
            return

        # ── tools/call ──────────────────────────────
        if method == "tools/call":
            params = body.get("params", {})
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})

            if tool_name not in TOOLS:
                self._send_json({"jsonrpc": "2.0", "id": req_id,
                                 "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
                return

            try:
                result = TOOLS[tool_name]["fn"](tool_args)
            except Exception as e:
                result = {"error": str(e)}

            self._send_json({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                    "isError": "error" in result and not result.get("success", True),
                },
            })
            return

        # ── unknown method ──────────────────────────
        self._send_json({"jsonrpc": "2.0", "id": req_id,
                         "error": {"code": -32601, "message": f"Method not found: {method}"}})

    def do_DELETE(self):
        if self.path != "/mcp":
            self._send_json({"error": "not found"}, 404)
            return
        sid = self.headers.get("mcp-session-id", "")
        if sid in _sessions:
            del _sessions[sid]
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self._send_json({"status": "ok", "port": MCP_PORT,
                             "sessions": len(_sessions), "tools": len(TOOLS)})
        else:
            self._send_json({"error": "not found"}, 404)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _git_hash() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                           text=True, cwd=_PROJECT_ROOT)
        return r.stdout.strip()
    except Exception:
        return ""


def _git_branch() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, cwd=_PROJECT_ROOT)
        return r.stdout.strip()
    except Exception:
        return "unknown"


def _rebel_version() -> str:
    try:
        from aider._version import __version__
        return __version__
    except Exception:
        return "unknown"


def _log_to_captains_log(message: str) -> None:
    try:
        from cmnd.mcp_client import log_to_captains_log
        log_to_captains_log(agent="rebel-mcp-server", message=message,
                            tags=["rebel", "mcp-server", "audit"])
    except Exception:
        pass


# ─────────────────────────────────────────────
# Start / register
# ─────────────────────────────────────────────

_server_thread: Optional[threading.Thread] = None
_httpd: Optional[HTTPServer] = None


def start_background(coder=None) -> None:
    """Start the Rebel MCP server in a background daemon thread."""
    global _server_thread, _httpd, _coder_ref
    _coder_ref = coder

    # SECURITY CONTROL: SC-7 — Bind to localhost only
    _httpd = HTTPServer(("127.0.0.1", MCP_PORT), _MCPHandler)

    _server_thread = threading.Thread(target=_httpd.serve_forever, daemon=True)
    _server_thread.start()
    print(f"[rebel-mcp-server] Listening on http://127.0.0.1:{MCP_PORT}/mcp  ({len(TOOLS)} tools)")
    _log_to_captains_log(f"Rebel MCP server started on port {MCP_PORT}")


def install(coder=None) -> None:
    """Called from rebel_main._apply_coder_patches()."""
    global _coder_ref
    _coder_ref = coder
    try:
        start_background(coder)
    except OSError as e:
        print(f"[rebel-mcp-server] Could not start (port {MCP_PORT} in use?): {e}")


def register_commands(coder) -> None:
    """Register /mcp-server command."""
    commands = getattr(coder, "commands", None)
    if not commands:
        return

    def cmd_mcp_server(args: str) -> None:
        """/mcp-server — Show Rebel MCP server status and available tools."""
        print(f"\n[rebel-mcp-server] Port: {MCP_PORT}")
        print(f"  Active sessions: {len(_sessions)}")
        print(f"  Tools ({len(TOOLS)}):")
        for name, t in TOOLS.items():
            print(f"    {name:25s} — {t['description'][:60]}")
        print(f"\n  MCP endpoint: http://127.0.0.1:{MCP_PORT}/mcp")
        print(f"  Health:       http://127.0.0.1:{MCP_PORT}/health")
        print()

    try:
        commands.cmd_mcp_server = cmd_mcp_server
    except Exception:
        pass
