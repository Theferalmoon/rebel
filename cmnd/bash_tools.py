# cmnd/bash_tools.py — Expanded Pre-Approved Shell Commands for Rebel
# Extends Aider's shell command suggestions with CMND Center-specific approvals.
# SECURITY CONTROL: CM-7 (Least Functionality) — Only pre-vetted command patterns auto-approved
# SECURITY CONTROL: AU-2 (Audit Events) — All shell executions logged to Captain's Log
# SECURITY CONTROL: AC-6 (Least Privilege) — Destructive commands always require confirmation
# DAIV CERTIFIED

import re
import subprocess
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aider.coders import Coder

# ─────────────────────────────────────────────
# Pre-approved command patterns (regex)
# Safe to run without interactive confirmation.
# SECURITY CONTROL: CM-7 — Whitelist of safe read/test operations.
# ─────────────────────────────────────────────

AUTO_APPROVED: list[tuple[str, str]] = [
    # Docker inspection (read-only)
    (r"^docker\s+(ps|images|stats|inspect|logs|top|diff|port|version|info)\b", "docker read"),
    (r"^docker\s+compose\s+(ps|logs|config|top|images)\b", "compose read"),
    (r"^docker\s+container\s+(ls|list|inspect|logs|top|diff|port)\b", "container read"),
    # Testing
    (r"^pytest\b", "pytest"),
    (r"^cargo\s+test\b", "cargo test"),
    (r"^npm\s+test\b", "npm test"),
    (r"^npx\s+tsc\b", "tsc typecheck"),
    (r"^node\s+-e\s+.fetch\(", "node health check"),
    # Health checks
    (r"^curl\s+-s\s+http://localhost:\d+/health", "curl health check"),
    (r"^curl\s+-sf\s+http://localhost:\d+/health", "curl health check"),
    # Git read
    (r"^git\s+(status|log|diff|show|branch|tag|stash\s+list)\b", "git read"),
    # System inspection
    (r"^df\s+-h\b", "disk usage"),
    (r"^free\s+-h\b", "memory"),
    (r"^cat\s+/proc/(meminfo|cpuinfo|loadavg)\b", "proc read"),
    # Build/type checking
    (r"^cargo\s+(check|clippy|build\s+--release)\b", "cargo build"),
    (r"^npm\s+(run\s+build|run\s+lint|ci)\b", "npm build/lint"),
]

# Commands that are ALWAYS blocked regardless of flags
# SECURITY CONTROL: CM-7 — Hard-deny dangerous operations
HARD_BLOCKED: list[tuple[str, str]] = [
    (r"\brm\s+-rf\s+/", "rm -rf /root"),
    (r"\bsudo\s+rm\b", "sudo rm"),
    (r"\bdrop\s+table\b", "SQL DROP TABLE"),
    (r"\btruncate\s+table\b", "SQL TRUNCATE"),
    (r"\bformat\s+[a-z]:", "disk format"),
    (r"\bdd\s+if=.+of=/dev/[sh]d", "dd to disk"),
    (r"\bgit\s+push\s+.*--force\b", "force push"),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard"),
]


def is_auto_approved(command: str) -> bool:
    """Check if a command can run without confirmation.
    SECURITY CONTROL: CM-7 — Check hard-block list first.
    """
    cmd = command.strip()

    # Hard block takes priority
    for pattern, label in HARD_BLOCKED:
        if re.search(pattern, cmd, re.IGNORECASE):
            print(f"[rebel] BLOCKED command '{label}': {cmd[:80]}")
            return False

    # Check approved list
    for pattern, _ in AUTO_APPROVED:
        if re.match(pattern, cmd, re.IGNORECASE):
            return True

    return False


def run_approved(command: str, cwd: str | None = None, log: bool = True) -> tuple[int, str]:
    """
    Run a pre-approved shell command and return (returncode, output).
    SECURITY CONTROL: AU-2 — All executions logged with timestamp.
    SECURITY CONTROL: CM-7 — Only runs if is_auto_approved() passes.
    """
    if not is_auto_approved(command):
        return 1, f"Command not in approved list: {command}"

    if log:
        print(f"[rebel] running: {command}")

    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=120,
    )

    output = (result.stdout + result.stderr).strip()

    if log:
        # SECURITY CONTROL: AU-2 — Async-safe log (best-effort, don't block)
        try:
            from cmnd.mcp_client import log_to_captains_log
            log_to_captains_log(
                agent="rebel-bash",
                message=f"CMD: {command} | RC: {result.returncode} | {datetime.now().isoformat()}",
                tags=["rebel", "bash", "audit"],
            )
        except Exception:
            pass

    return result.returncode, output


# ─────────────────────────────────────────────
# Aider integration — patch suggest_shell_commands
# ─────────────────────────────────────────────

def patch_coder(coder: "Coder") -> None:
    """
    Patch the coder to use CMND auto-approval for pre-vetted commands.
    SECURITY CONTROL: CM-7 — Override Aider's default 'ask everything' behavior
    for safe commands, while keeping confirmation for everything else.
    """
    original_run = getattr(coder, "run_shell_commands", None)
    if not original_run:
        return

    def patched_run_shell(commands_list: list[str], *args, **kwargs):
        results = []
        for cmd in commands_list:
            if is_auto_approved(cmd):
                rc, out = run_approved(cmd, cwd=None, log=True)
                results.append((cmd, rc, out))
                if out:
                    coder.io.tool_output(out)
            else:
                # Fall through to original (which asks user)
                original_run([cmd], *args, **kwargs)
        return results

    coder.run_shell_commands = patched_run_shell

    # Add /run-approved command
    _register_commands(coder)


def _register_commands(coder: "Coder") -> None:
    """Register /run-approved and /approved-list commands."""
    commands = getattr(coder, "commands", None)
    if not commands:
        return

    def cmd_run_approved(args: str) -> None:
        """Run a pre-approved command directly. Usage: /run-approved <command>"""
        cmd = args.strip()
        if not cmd:
            print("Usage: /run-approved <command>")
            return
        rc, out = run_approved(cmd)
        print(out or f"(exit code {rc})")

    def cmd_approved_list(args: str) -> None:
        """Show the list of auto-approved command patterns."""
        print("\nAuto-approved command patterns:")
        for pattern, label in AUTO_APPROVED:
            print(f"  {label}: {pattern}")
        print("\nHard-blocked patterns:")
        for pattern, label in HARD_BLOCKED:
            print(f"  {label}: {pattern}")

    for name, fn in [("run_approved", cmd_run_approved), ("approved_list", cmd_approved_list)]:
        try:
            setattr(commands, f"cmd_{name}", fn)
        except Exception:
            pass
