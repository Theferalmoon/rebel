# cmnd/plan_mode.py — Plan-Before-Edit Approval Gate
# Shows proposed changes as a plan and requires user approval before applying.
# Mirrors Claude Code's plan mode behavior.
# SECURITY CONTROL: CM-3 (Configuration Change Control) — All code changes require approval
# SECURITY CONTROL: SA-10 (Developer Configuration Management) — Explicit change authorization
# DAIV CERTIFIED

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aider.coders import Coder

# ─────────────────────────────────────────────
# Plan mode state
# ─────────────────────────────────────────────

_PLAN_MODE_ENABLED: bool = False
_PENDING_EDITS: list = []


def is_enabled() -> bool:
    return _PLAN_MODE_ENABLED


def enable() -> None:
    global _PLAN_MODE_ENABLED
    _PLAN_MODE_ENABLED = True
    print("[rebel] Plan mode ENABLED — edits will be shown for approval before applying.")


def disable() -> None:
    global _PLAN_MODE_ENABLED
    _PLAN_MODE_ENABLED = False
    print("[rebel] Plan mode DISABLED — edits applied immediately.")


def toggle() -> None:
    if _PLAN_MODE_ENABLED:
        disable()
    else:
        enable()


# ─────────────────────────────────────────────
# Approval gate
# ─────────────────────────────────────────────

def _format_edit_summary(edits: list) -> str:
    """Format proposed edits as a human-readable plan."""
    if not edits:
        return "  (no edits proposed)"

    lines = []
    seen_files = {}

    for edit in edits:
        # Edit formats vary by coder type — handle both tuple and dict forms
        if isinstance(edit, (tuple, list)) and len(edit) >= 2:
            path = str(edit[0])
            seen_files[path] = seen_files.get(path, 0) + 1
        elif isinstance(edit, dict):
            path = edit.get("path", edit.get("filename", "unknown"))
            seen_files[path] = seen_files.get(path, 0) + 1

    for path, count in seen_files.items():
        rel = os.path.relpath(path) if os.path.isabs(path) else path
        lines.append(f"  • {rel}  ({count} change{'s' if count > 1 else ''})")

    return "\n".join(lines)


def prompt_approval(edits: list, io) -> bool:
    """
    Display the proposed edit plan and prompt user for approval.

    Args:
        edits: List of proposed edits from the coder.
        io: Aider InputOutput instance for prompting.

    Returns:
        True if user approves, False to abort.

    SECURITY CONTROL: CM-3 — Change requires explicit human authorization.
    """
    summary = _format_edit_summary(edits)

    print("\n" + "=" * 60)
    print("  REBEL PLAN MODE — Proposed Changes")
    print("=" * 60)
    print(summary)
    print("=" * 60)

    if hasattr(io, "confirm_ask"):
        return io.confirm_ask("Apply these changes?", default="y")
    else:
        # Fallback for non-interactive
        response = input("Apply these changes? [Y/n] ").strip().lower()
        return response in ("", "y", "yes")


# ─────────────────────────────────────────────
# Coder patch
# ─────────────────────────────────────────────

def patch_coder(coder: "Coder") -> None:
    """
    Intercept apply_edits to show plan and require approval when plan mode is on.
    SECURITY CONTROL: CM-3 — Intercept at the apply_edits boundary.
    """
    original_apply = coder.apply_edits

    def guarded_apply_edits(edits, *args, **kwargs):
        if _PLAN_MODE_ENABLED and edits:
            approved = prompt_approval(edits, coder.io)
            if not approved:
                print("[rebel] Changes aborted by user.")
                return []
        return original_apply(edits, *args, **kwargs)

    coder.apply_edits = guarded_apply_edits

    # Register /plan command
    _register_commands(coder)


def _register_commands(coder: "Coder") -> None:
    """Add /plan, /plan-on, /plan-off commands to Aider."""
    commands = getattr(coder, "commands", None)
    if not commands:
        return

    def cmd_plan(args: str) -> None:
        """Toggle plan mode or set on/off. Usage: /plan [on|off]"""
        arg = args.strip().lower()
        if arg == "on":
            enable()
        elif arg == "off":
            disable()
        else:
            toggle()

    def cmd_plan_on(args: str) -> None:
        """Enable plan mode — edits shown for approval before applying."""
        enable()

    def cmd_plan_off(args: str) -> None:
        """Disable plan mode — edits applied immediately."""
        disable()

    # Inject commands into Aider's command registry
    for name, fn in [("plan", cmd_plan), ("plan-on", cmd_plan_on), ("plan-off", cmd_plan_off)]:
        try:
            setattr(commands, f"cmd_{name.replace('-', '_')}", fn)
        except Exception:
            pass
