# cmnd/context_bridge.py — Captain's Log Startup/Shutdown Hooks
# Injects recent session context on startup, writes summary on exit.
# SECURITY CONTROL: SC-7 (Boundary Protection) — Captain's Log is an external trust boundary
# SECURITY CONTROL: AU-2 (Audit Events) — Session start/end logged for continuity
# SECURITY CONTROL: MP-4 (Media Storage) — Session context written to temp file, cleaned on exit
# DAIV CERTIFIED

import atexit
import os
import tempfile
import time
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aider.coders import Coder

from cmnd.mcp_client import log_to_captains_log, get_recent_context

# Path where we write the injected context so Aider can read it
_CONTEXT_FILE: str | None = None
_SESSION_START: float = time.time()
_CODER_REF: "Coder | None" = None
_SESSION_TAGS: list[str] = []


def inject_startup_context(coder: "Coder") -> str | None:
    """
    On Rebel startup: fetch recent Captain's Log entries and write them
    to a temp file that Aider reads as --read context.

    Returns path to the temp file, or None if Captain's Log is unreachable.
    SECURITY CONTROL: MP-4 — Temp file restricted to current user (mode 0600).
    """
    global _CONTEXT_FILE, _SESSION_START, _CODER_REF

    _SESSION_START = time.time()
    _CODER_REF = coder

    try:
        print("[rebel] Connecting to Captain's Log for session context...")
        context_text = get_recent_context(hours=24)

        if not context_text or "Error" in context_text:
            print("[rebel] Captain's Log offline — starting without prior context")
            return None

        # Write to temp file — SECURITY CONTROL: MP-4 (0600 permissions)
        fd, path = tempfile.mkstemp(prefix="rebel-context-", suffix=".md")
        os.chmod(path, 0o600)
        _CONTEXT_FILE = path

        with os.fdopen(fd, "w") as f:
            f.write("<!-- REBEL SESSION CONTEXT — from Captain's Log -->\n")
            f.write(f"<!-- Loaded: {datetime.now().isoformat()} -->\n\n")
            f.write("## Recent Session History\n\n")
            f.write(context_text)
            f.write("\n\n<!-- END REBEL SESSION CONTEXT -->\n")

        print(f"[rebel] Context loaded from Captain's Log → {path}")

        # Log session start
        log_to_captains_log(
            agent="rebel",
            message=f"Rebel session started. Model: {getattr(coder.main_model, 'name', 'unknown')}. "
                    f"Files: {', '.join(coder.get_inchat_relative_files()) or 'none'}",
            tags=["rebel", "session-start"],
        )

        return path

    except Exception as e:
        print(f"[rebel] Context bridge startup error: {e}")
        return None


def record_session_end(exit_reason: str = "normal") -> None:
    """
    On Rebel exit: write session summary to Captain's Log.
    SECURITY CONTROL: AU-2 — Session boundary events recorded for audit continuity.
    """
    global _CONTEXT_FILE, _CODER_REF

    duration_s = int(time.time() - _SESSION_START)
    duration_str = f"{duration_s // 60}m {duration_s % 60}s"

    try:
        files_changed = []
        tokens_used = 0

        if _CODER_REF:
            try:
                files_changed = list(getattr(_CODER_REF, "aider_edited_files", set()))
                tokens_used = getattr(_CODER_REF, "total_tokens_sent", 0) + \
                              getattr(_CODER_REF, "total_tokens_received", 0)
                cost = getattr(_CODER_REF, "total_cost", 0.0)
            except Exception:
                pass

        summary_parts = [
            f"Rebel session ended ({exit_reason}). Duration: {duration_str}.",
        ]
        if files_changed:
            summary_parts.append(f"Files modified: {', '.join(files_changed[:10])}.")
        if tokens_used:
            summary_parts.append(f"Tokens used: {tokens_used:,} (~${cost:.4f}).")

        log_to_captains_log(
            agent="rebel",
            message=" ".join(summary_parts),
            tags=["rebel", "session-end", exit_reason],
        )
        print(f"[rebel] Session summary written to Captain's Log.")

    except Exception as e:
        # Best-effort — don't crash on shutdown
        print(f"[rebel] Could not write session end to Captain's Log: {e}")

    finally:
        # Clean up context temp file
        if _CONTEXT_FILE and os.path.exists(_CONTEXT_FILE):
            try:
                os.unlink(_CONTEXT_FILE)
            except Exception:
                pass
        _CONTEXT_FILE = None
        _CODER_REF = None


def install_exit_hook() -> None:
    """Register the session-end hook with atexit."""
    atexit.register(record_session_end, "normal")


def patch_coder(coder: "Coder") -> None:
    """
    Apply context bridge to an existing Coder instance.
    Called by rebel_main.py after Coder is created.
    """
    path = inject_startup_context(coder)
    if path:
        # Add the context file as read-only — Aider sees it but won't edit it
        coder.add_rel_fname(path)

    install_exit_hook()
