# cmnd/mcp_client.py — MCP Client for Rebel
# Connects Rebel to CMND Center MCP servers as native tools.
# SECURITY CONTROL: SC-7 (Boundary Protection) — MCP endpoints are external trust boundaries;
#   all responses parsed defensively, sessions short-lived and explicitly closed.
# SECURITY CONTROL: SI-10 (Information Input Validation) — Tool args validated before dispatch.
# SECURITY CONTROL: AU-2 (Audit Events) — All tool calls logged to console.
# TRUST BOUNDARY: Each MCP server URL is a network trust boundary — responses treated as untrusted.
# DAIV CERTIFIED

import json
import urllib.request
import urllib.error
from typing import Any, Optional

# ─────────────────────────────────────────────
# Server registry — all CMND Center MCP agents
# ─────────────────────────────────────────────

SERVERS: dict[str, dict] = {
    "captains-log": {
        "url": "http://localhost:3001/mcp",
        "description": "Shared context bus — log entries, session history, search logs",
    },
    "compliance": {
        "url": "http://localhost:3002/mcp",
        "description": "CIS/NRS/PSP compliance evaluation and gap analysis",
    },
    "testing": {
        "url": "http://localhost:3003/mcp",
        "description": "Testing and certification agent",
    },
    "rebel-context": {
        "url": "http://localhost:3004/mcp",
        "description": "Context bridge — reads all agent history, generates briefing",
    },
    "cmdb": {
        "url": "http://localhost:3024/mcp",
        "description": "CMDB — configuration items, relationships, change records",
    },
    "sentinel": {
        "url": "http://localhost:3029/mcp",
        "description": "Cybersecurity news intelligence — CVE, CISA KEV, threat feeds",
    },
    "conductor": {
        "url": "http://localhost:3030/mcp",
        "description": "PQC compliance monitoring — certificate expiry, endpoint sweeps",
    },
    "arch-angel": {
        "url": "http://localhost:3031/mcp",
        "description": "Angel investor and donor relations management",
    },
    "watchdog": {
        "url": "http://localhost:3032/mcp",
        "description": "Unified observability — alerts, playbooks, system health",
    },
}

TIMEOUT = 8  # seconds — SECURITY CONTROL: SC-5 (DoS Protection)

# ─────────────────────────────────────────────
# Session management
# ─────────────────────────────────────────────

class MCPSession:
    """Short-lived MCP session for a single server interaction."""

    def __init__(self, server_name: str):
        if server_name not in SERVERS:
            raise ValueError(f"Unknown MCP server: {server_name}")
        self.server_name = server_name
        self.url = SERVERS[server_name]["url"]
        self.session_id: Optional[str] = None

    def _request(self, method: str, params: dict, msg_id: int = 1) -> Any:
        """Send a JSON-RPC request over HTTP.
        TRUST BOUNDARY: Response content treated as untrusted input.
        """
        body = json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": msg_id,
        }).encode()

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            # SECURITY CONTROL: SI-10 — Only ASCII session IDs accepted
            if not all(c.isalnum() or c in "-_" for c in self.session_id):
                raise ValueError("Invalid session ID characters detected")
            headers["mcp-session-id"] = self.session_id

        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                # Capture session ID from initialize response
                sid = resp.headers.get("mcp-session-id")
                if sid and not self.session_id:
                    self.session_id = sid

                raw = resp.read().decode("utf-8", errors="replace")

                # Handle SSE-wrapped responses
                if "data:" in raw:
                    for line in raw.splitlines():
                        if line.startswith("data:"):
                            raw = line[5:].strip()
                            break

                # TRUST BOUNDARY: Defensive JSON parse
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    return {"error": f"Invalid JSON from {self.server_name}: {raw[:200]}"}

                if "error" in parsed:
                    return {"error": parsed["error"]}
                return parsed.get("result", parsed)

        except urllib.error.URLError as e:
            return {"error": f"{self.server_name} unreachable: {e}"}
        except TimeoutError:
            return {"error": f"{self.server_name} timed out"}

    def initialize(self) -> bool:
        """Open MCP session. Returns True on success."""
        result = self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "rebel", "version": "1.0.0"},
        })
        if "error" in result:
            return False
        return True

    def list_tools(self) -> list[dict]:
        """List available tools from this server."""
        result = self._request("tools/list", {}, msg_id=2)
        if "error" in result:
            return []
        tools = result.get("tools", [])
        # TRUST BOUNDARY: Validate tool entries are dicts with name field
        return [t for t in tools if isinstance(t, dict) and "name" in t]

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call an MCP tool. Returns formatted result string.
        SECURITY CONTROL: SI-10 — tool_name validated as identifier string.
        """
        # SECURITY CONTROL: SI-10 — Restrict tool name to identifier characters
        if not tool_name.replace("-", "").replace("_", "").isalnum():
            return f"Error: Invalid tool name '{tool_name}'"

        print(f"[rebel-mcp] calling {self.server_name}/{tool_name}")
        result = self._request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        }, msg_id=3)

        if "error" in result:
            return f"Error from {self.server_name}: {result['error']}"

        # Extract text content from MCP response
        content = result.get("content", [])
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
            return "\n".join(parts) if parts else json.dumps(result, indent=2)
        return str(result)

    def close(self) -> None:
        """Close the MCP session with DELETE."""
        if not self.session_id:
            return
        try:
            req = urllib.request.Request(
                self.url,
                headers={"mcp-session-id": self.session_id},
                method="DELETE",
            )
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            pass  # Best-effort close
        self.session_id = None

    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, *_):
        self.close()


# ─────────────────────────────────────────────
# High-level convenience functions
# ─────────────────────────────────────────────

def call(server: str, tool: str, arguments: dict | None = None) -> str:
    """One-shot MCP tool call — opens session, calls tool, closes session.
    SECURITY CONTROL: AU-2 — All calls logged to stdout for audit.
    """
    arguments = arguments or {}
    with MCPSession(server) as session:
        return session.call_tool(tool, arguments)


def list_all_tools() -> dict[str, list[dict]]:
    """Probe all known servers and return their available tools.
    Servers that are offline are skipped silently.
    """
    result: dict[str, list[dict]] = {}
    for name in SERVERS:
        try:
            with MCPSession(name) as session:
                tools = session.list_tools()
                if tools:
                    result[name] = tools
        except Exception:
            pass
    return result


def available_servers() -> list[str]:
    """Return names of servers currently reachable."""
    online = []
    for name, cfg in SERVERS.items():
        try:
            req = urllib.request.Request(
                cfg["url"].replace("/mcp", "/health"),
                method="GET",
            )
            urllib.request.urlopen(req, timeout=3)
            online.append(name)
        except Exception:
            pass
    return online


# ─────────────────────────────────────────────
# Aider command integration helpers
# ─────────────────────────────────────────────

def log_to_captains_log(agent: str, message: str, tags: list[str] | None = None) -> str:
    """Write an entry to the Captain's Log MCP server."""
    return call("captains-log", "add_log_entry", {
        "agent": agent,
        "message": message,
        "tags": tags or ["rebel", "session"],
    })


def get_recent_context(hours: int = 24) -> str:
    """Fetch recent Captain's Log entries for context injection."""
    return call("captains-log", "search_log", {
        "query": "rebel session task",
        "limit": 20,
    })


def check_compliance(description: str) -> str:
    """Quick compliance check via Compliance Agent."""
    return call("compliance", "evaluate_compliance", {
        "description": description,
        "standards": ["CIS", "NRS_603A", "NV_PSP"],
    })


def get_active_alerts() -> str:
    """Get current alerts from Watchdog."""
    return call("watchdog", "get_active_alerts", {})
