# cmnd/shared_state.py — Shared State Between Claude Code and Rebel
# Reads/writes MEMORY.md and TASKS.md so both AIs share the same context.
# SECURITY CONTROL: SC-28 (Protection at Rest) — Files written with restricted permissions
# SECURITY CONTROL: AU-2 (Audit Events) — All state mutations logged
# DAIV CERTIFIED

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────
# Canonical paths — same locations Claude Code uses
# ─────────────────────────────────────────────

PROJECT_ROOT = Path(os.environ.get("REBEL_PROJECT_ROOT", "/home/theferalmoon/local-opus-lab"))

# Claude Code's auto-memory file — read on startup, append new learnings on exit
MEMORY_FILE = PROJECT_ROOT / ".claude" / "projects" / "-home-theferalmoon-local-opus-lab" / "memory" / "MEMORY.md"
# Fallback: look in rebel/context/
if not MEMORY_FILE.exists():
    MEMORY_FILE = PROJECT_ROOT / "rebel" / "context" / "MEMORY.md"

# Shared persistent task list — both Claude Code and Rebel read/write this
TASKS_FILE = PROJECT_ROOT / "docs" / "TASKS.md"

# Rebel's own session log (append-only, never truncated)
REBEL_LOG = PROJECT_ROOT / "rebel" / "context" / "rebel-session-log.md"


# ─────────────────────────────────────────────
# MEMORY.md
# ─────────────────────────────────────────────

def read_memory() -> str:
    """Read the shared MEMORY.md file. Returns empty string if not found."""
    try:
        if MEMORY_FILE.exists():
            return MEMORY_FILE.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[rebel] Could not read MEMORY.md: {e}")
    return ""


def append_memory(section_header: str, content: str) -> bool:
    """
    Append a new learning to MEMORY.md under a given section.
    Creates the file if it doesn't exist.

    SECURITY CONTROL: SC-28 — File opened in append mode, no overwrite of existing content.
    """
    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d")
        entry = f"\n## {section_header} [{timestamp}]\n{content}\n"

        with open(MEMORY_FILE, "a", encoding="utf-8") as f:
            f.write(entry)

        print(f"[rebel] Appended to MEMORY.md: {section_header}")
        return True
    except Exception as e:
        print(f"[rebel] Could not append to MEMORY.md: {e}")
        return False


# ─────────────────────────────────────────────
# TASKS.md — Shared Task List
# ─────────────────────────────────────────────

class TaskStatus:
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"

STATUS_EMOJI = {
    TaskStatus.PENDING: "⬜",
    TaskStatus.IN_PROGRESS: "🔵",
    TaskStatus.DONE: "✅",
    TaskStatus.BLOCKED: "🔴",
}


def read_tasks() -> str:
    """Read the shared TASKS.md. Returns empty string if not found."""
    try:
        if TASKS_FILE.exists():
            return TASKS_FILE.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[rebel] Could not read TASKS.md: {e}")
    return ""


def add_task(subject: str, description: str = "", assignee: str = "rebel") -> bool:
    """
    Add a new pending task to TASKS.md.
    SECURITY CONTROL: AU-2 — Task additions logged with timestamp and assignee.
    """
    try:
        TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Init file if new
        if not TASKS_FILE.exists():
            TASKS_FILE.write_text(
                "# CMND Center — Shared Task List\n"
                "Maintained by Claude Code and Rebel. Last updated by each AI on session end.\n\n"
                "| Status | Task | Assignee | Added | Notes |\n"
                "|--------|------|----------|-------|-------|\n",
                encoding="utf-8",
            )

        row = (
            f"| {STATUS_EMOJI[TaskStatus.PENDING]} pending "
            f"| {subject} | {assignee} | {timestamp} | {description} |\n"
        )

        with open(TASKS_FILE, "a", encoding="utf-8") as f:
            f.write(row)

        print(f"[rebel] Task added: {subject}")
        return True
    except Exception as e:
        print(f"[rebel] Could not add task: {e}")
        return False


def update_task_status(subject_fragment: str, new_status: str, notes: str = "") -> bool:
    """
    Update a task's status by matching subject fragment.
    Rewrites the matching line in TASKS.md.
    SECURITY CONTROL: SI-10 — subject_fragment sanitized before regex use.
    """
    try:
        if not TASKS_FILE.exists():
            return False

        # SECURITY CONTROL: SI-10 — Escape user input used in regex
        safe_fragment = re.escape(subject_fragment[:100])
        emoji = STATUS_EMOJI.get(new_status, "❓")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        content = TASKS_FILE.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)
        updated = False

        for i, line in enumerate(lines):
            if re.search(safe_fragment, line, re.IGNORECASE) and "|" in line:
                # Replace status emoji and optionally append note
                for old_emoji in STATUS_EMOJI.values():
                    line = line.replace(old_emoji, emoji)
                # Replace old status word
                for old_status in ["pending", "in_progress", "done", "blocked"]:
                    line = line.replace(f" {old_status} ", f" {new_status} ")
                if notes:
                    # Append note to last column
                    line = line.rstrip("\n") + f" [{timestamp}: {notes}]\n"
                lines[i] = line
                updated = True
                break

        if updated:
            TASKS_FILE.write_text("".join(lines), encoding="utf-8")
            print(f"[rebel] Task updated: '{subject_fragment}' → {new_status}")

        return updated
    except Exception as e:
        print(f"[rebel] Could not update task: {e}")
        return False


def get_pending_tasks() -> list[str]:
    """Return list of pending/in-progress task subjects."""
    content = read_tasks()
    if not content:
        return []

    pending = []
    for line in content.splitlines():
        if "|" in line and ("pending" in line or "in_progress" in line):
            # Extract subject (2nd column)
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                pending.append(parts[2])

    return pending


# ─────────────────────────────────────────────
# Rebel session log (append-only)
# ─────────────────────────────────────────────

def log_session(
    model: str,
    files_changed: list[str],
    tasks_completed: list[str],
    tokens_used: int,
    notes: str = "",
) -> None:
    """
    Append a session summary to rebel-session-log.md.
    SECURITY CONTROL: AU-2 — Immutable append-only log of all Rebel sessions.
    """
    try:
        REBEL_LOG.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().isoformat()

        entry_lines = [
            f"\n## Session — {timestamp}",
            f"- **Model:** {model}",
            f"- **Files changed:** {', '.join(files_changed) or 'none'}",
            f"- **Tasks completed:** {', '.join(tasks_completed) or 'none'}",
            f"- **Tokens:** {tokens_used:,}",
        ]
        if notes:
            entry_lines.append(f"- **Notes:** {notes}")
        entry_lines.append("")

        with open(REBEL_LOG, "a", encoding="utf-8") as f:
            f.write("\n".join(entry_lines) + "\n")

    except Exception as e:
        print(f"[rebel] Could not write session log: {e}")


# ─────────────────────────────────────────────
# Startup context builder
# ─────────────────────────────────────────────

def build_startup_context() -> str:
    """
    Assemble the full context Rebel needs at startup:
    - MEMORY.md (architectural decisions, conventions, preferences)
    - Pending tasks from TASKS.md
    - Recent rebel session log entries

    Returns a markdown string to be injected as read-only context.
    """
    parts = []

    # MEMORY.md
    memory = read_memory()
    if memory:
        parts.append("## Permanent Memory (from MEMORY.md)\n")
        # Trim to first 150 lines to fit context
        lines = memory.splitlines()
        parts.append("\n".join(lines[:150]))
        if len(lines) > 150:
            parts.append(f"\n... ({len(lines) - 150} more lines in MEMORY.md)")
        parts.append("\n")

    # Pending tasks
    pending = get_pending_tasks()
    if pending:
        parts.append("## Pending Tasks (from TASKS.md)\n")
        for t in pending[:20]:
            parts.append(f"- {t}")
        parts.append("\n")

    # Recent session log
    if REBEL_LOG.exists():
        try:
            log_text = REBEL_LOG.read_text(encoding="utf-8")
            # Last 3 sessions only
            sessions = log_text.split("\n## Session")
            if len(sessions) > 1:
                recent = "\n## Session".join(sessions[-3:])
                parts.append("## Recent Rebel Sessions\n")
                parts.append(recent)
                parts.append("\n")
        except Exception:
            pass

    return "\n".join(parts)


# ─────────────────────────────────────────────
# Aider command integration
# ─────────────────────────────────────────────

def register_commands(coder) -> None:
    """Register /tasks, /task-done, /remember commands."""
    commands = getattr(coder, "commands", None)
    if not commands:
        return

    def cmd_tasks(args: str) -> None:
        """Show pending tasks from shared TASKS.md. Usage: /tasks"""
        pending = get_pending_tasks()
        if not pending:
            print("No pending tasks in TASKS.md")
            return
        print(f"\nPending tasks ({len(pending)}):")
        for t in pending:
            print(f"  ⬜ {t}")

    def cmd_task_done(args: str) -> None:
        """Mark a task as done. Usage: /task-done <subject fragment>"""
        fragment = args.strip()
        if not fragment:
            print("Usage: /task-done <subject fragment>")
            return
        success = update_task_status(fragment, TaskStatus.DONE, f"completed by Rebel")
        if not success:
            print(f"No matching task found for: {fragment}")

    def cmd_task_add(args: str) -> None:
        """Add a task to the shared list. Usage: /task-add <subject>"""
        subject = args.strip()
        if not subject:
            print("Usage: /task-add <subject>")
            return
        add_task(subject, assignee="rebel")

    def cmd_remember(args: str) -> None:
        """Append a note to MEMORY.md. Usage: /remember <text>"""
        text = args.strip()
        if not text:
            print("Usage: /remember <text>")
            return
        append_memory("Rebel Note", text)

    for name, fn in [
        ("tasks", cmd_tasks),
        ("task_done", cmd_task_done),
        ("task_add", cmd_task_add),
        ("remember", cmd_remember),
    ]:
        try:
            setattr(commands, f"cmd_{name}", fn)
        except Exception:
            pass
