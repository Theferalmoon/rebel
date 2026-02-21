# cmnd/watch_mode.py — File annotation watcher for Rebel
# Monitors source files for # REBEL: ... comments and triggers Rebel automatically.
#
# INSPIRATION:
#   aweis89/aider.nvim (MIT) — https://github.com/aweis89/aider.nvim
#   MatthewZMD/aidermacs (Apache-2.0) — https://github.com/MatthewZMD/aidermacs
#
# Usage:
#   Add anywhere in any source file:   # REBEL: fix the null pointer exception
#   Or for C/JS/TS/Go:                  // REBEL: add input validation
#   Or for block comments:              /* REBEL: refactor this function */
#
# Rebel detects the annotation, runs on that file with the instruction as the
# prompt, then marks it done: # REBEL[done 2026-02-21]: fix the null...
#
# SECURITY CONTROL: CM-3 — Each annotation triggers a targeted, audited session
# SECURITY CONTROL: AU-2 — All triggers logged to Captain's Log
# SECURITY CONTROL: SC-5 — Max one active watch task at a time
# DAIV CERTIFIED

import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from aider.coders import Coder

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────

POLL_INTERVAL = float(os.environ.get("REBEL_WATCH_INTERVAL", "3.0"))  # seconds
WATCH_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".sh", ".yml", ".yaml",
    ".toml", ".json", ".md", ".sql",
}
WATCH_EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
    "build", ".rebel", ".aider", "rebel.egg-info",
}

# Pattern: # REBEL: <instruction>  or  // REBEL: <instruction>
# Captures the instruction text
_ANNOTATION_RE = re.compile(
    r"(?:#|//|/\*)\s*REBEL:\s*(\S.*?)(?:\s*\*/)?$",
    re.IGNORECASE,
)
# Already-processed marker — skip these
_DONE_RE = re.compile(r"(?:#|//|/\*)\s*REBEL\[done", re.IGNORECASE)

_watching = False
_watch_thread: Optional[threading.Thread] = None
_coder_ref: Optional["Coder"] = None
_active_task = False  # SC-5: one at a time


# ─────────────────────────────────────────────
# File scanner
# ─────────────────────────────────────────────

def _find_annotations(root: str) -> list[dict]:
    """
    Walk the project tree, return list of:
      { file, line_number, instruction, original_line }
    for each un-processed REBEL: annotation.
    """
    results = []
    root_path = Path(root)

    for dirpath, dirnames, filenames in os.walk(root_path):
        # Prune excluded dirs in-place
        dirnames[:] = [d for d in dirnames if d not in WATCH_EXCLUDE_DIRS]

        for fname in filenames:
            ext = Path(fname).suffix.lower()
            if ext not in WATCH_EXTENSIONS:
                continue

            fpath = Path(dirpath) / fname
            try:
                lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue

            for i, line in enumerate(lines):
                if _DONE_RE.search(line):
                    continue
                m = _ANNOTATION_RE.search(line)
                if m:
                    results.append({
                        "file": str(fpath),
                        "line_number": i + 1,
                        "instruction": m.group(1).strip(),
                        "original_line": line,
                    })

    return results


# ─────────────────────────────────────────────
# Annotation processor
# ─────────────────────────────────────────────

def _mark_done(annotation: dict) -> None:
    """Replace the annotation line with a [done] marker."""
    fpath = Path(annotation["file"])
    try:
        lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        line_idx = annotation["line_number"] - 1
        if line_idx < len(lines):
            orig = lines[line_idx]
            date_str = datetime.now().strftime("%Y-%m-%d")
            instruction_short = annotation["instruction"][:60]
            # Preserve indentation
            indent = len(orig) - len(orig.lstrip())
            prefix = orig[:indent]
            # Determine comment style
            stripped = orig.strip()
            if stripped.startswith("//"):
                marker = "//"
            elif stripped.startswith("/*"):
                marker = "//"
            else:
                marker = "#"
            lines[line_idx] = f"{prefix}{marker} REBEL[done {date_str}]: {instruction_short}\n"
            fpath.write_text("".join(lines), encoding="utf-8")
    except Exception as e:
        print(f"[rebel-watch] Could not mark done: {e}")


def _run_rebel_on_annotation(annotation: dict, coder: "Coder") -> bool:
    """
    Trigger Rebel to process a file annotation.
    Uses coder.run() if available, otherwise falls back to subprocess.
    """
    global _active_task
    if _active_task:
        return False  # SC-5: one at a time

    _active_task = True
    file_path = annotation["file"]
    instruction = annotation["instruction"]
    rel_path = os.path.relpath(file_path, os.environ.get("REBEL_PROJECT_ROOT", "."))

    print(f"\n[rebel-watch] Annotation detected in {rel_path}:{annotation['line_number']}")
    print(f"[rebel-watch] Instruction: {instruction}")
    print(f"[rebel-watch] Running Rebel...")

    _log_to_captains_log(
        f"Watch trigger: {rel_path}:{annotation['line_number']} — {instruction[:80]}"
    )

    try:
        prompt = (
            f"The developer has annotated line {annotation['line_number']} of "
            f"`{rel_path}` with this instruction:\n\n"
            f"  {instruction}\n\n"
            f"Please implement this change in the file. "
            f"Focus only on what the annotation asks for."
        )

        if coder:
            # Add file to chat if not already there
            try:
                abs_path = str(Path(file_path).resolve())
                if abs_path not in coder.abs_fnames:
                    coder.add_rel_fname(rel_path)
            except Exception:
                pass
            coder.run(with_message=prompt)
        else:
            # Fallback: subprocess
            import subprocess
            subprocess.run(
                ["rebel", "--yes", "--message", prompt, "--file", rel_path],
                cwd=os.environ.get("REBEL_PROJECT_ROOT", "."),
                timeout=300,
            )

        _mark_done(annotation)
        print(f"[rebel-watch] Done. Annotation marked complete.")
        _log_to_captains_log(f"Watch task complete: {rel_path}:{annotation['line_number']}")
        return True

    except Exception as e:
        print(f"[rebel-watch] Error processing annotation: {e}")
        return False
    finally:
        _active_task = False


# ─────────────────────────────────────────────
# Watch loop
# ─────────────────────────────────────────────

def _watch_loop(project_root: str) -> None:
    """Background thread: poll for annotations every POLL_INTERVAL seconds."""
    global _coder_ref
    print(f"[rebel-watch] Watching {project_root} for REBEL: annotations "
          f"(every {POLL_INTERVAL}s)...")

    while _watching:
        try:
            annotations = _find_annotations(project_root)
            if annotations:
                coder = _coder_ref
                for ann in annotations[:1]:  # Process one at a time (SC-5)
                    if _watching:
                        _run_rebel_on_annotation(ann, coder)
        except Exception as e:
            print(f"[rebel-watch] Scanner error: {e}")

        # Sleep in small chunks so we can stop promptly
        for _ in range(int(POLL_INTERVAL / 0.5)):
            if not _watching:
                break
            time.sleep(0.5)

    print("[rebel-watch] Watch mode stopped.")


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def start(project_root: str, coder=None) -> None:
    """Start the file annotation watcher."""
    global _watching, _watch_thread, _coder_ref
    if _watching:
        print("[rebel-watch] Already watching. Use /watch off to stop.")
        return

    _coder_ref = coder
    _watching = True
    _watch_thread = threading.Thread(
        target=_watch_loop,
        args=(project_root,),
        daemon=True,
        name="rebel-watch",
    )
    _watch_thread.start()


def stop() -> None:
    """Stop the file annotation watcher."""
    global _watching
    _watching = False


def is_watching() -> bool:
    return _watching


def scan_once(project_root: str) -> list[dict]:
    """One-shot scan — returns list of found annotations without running Rebel."""
    return _find_annotations(project_root)


def _log_to_captains_log(message: str) -> None:
    try:
        from cmnd.mcp_client import log_to_captains_log
        log_to_captains_log(agent="rebel-watch", message=message,
                            tags=["rebel", "watch-mode", "annotation"])
    except Exception:
        pass


# ─────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────

def register_commands(coder, project_root: str) -> None:
    """Register /watch command."""
    commands = getattr(coder, "commands", None)
    if not commands:
        return

    def cmd_watch(args: str) -> None:
        """/watch [on|off|scan] — File annotation watcher. # REBEL: <instruction> in any file triggers Rebel."""
        arg = args.strip().lower()

        if arg == "off":
            if is_watching():
                stop()
                print("[rebel-watch] Stopped.")
            else:
                print("[rebel-watch] Not currently watching.")
            return

        if arg == "scan":
            found = scan_once(project_root)
            if found:
                print(f"\n[rebel-watch] Found {len(found)} pending annotation(s):")
                for a in found:
                    rel = os.path.relpath(a["file"], project_root)
                    print(f"  {rel}:{a['line_number']} — {a['instruction'][:70]}")
            else:
                print("[rebel-watch] No pending REBEL: annotations found.")
            return

        if arg in ("on", "start", ""):
            global _coder_ref
            _coder_ref = coder
            start(project_root, coder)
            print(f"[rebel-watch] Watching for REBEL: annotations in {project_root}")
            print(f"  Add  # REBEL: <instruction>  to any source file to trigger Rebel.")
            print(f"  Use /watch off to stop, /watch scan to list pending annotations.")
            return

        print("Usage: /watch [on|off|scan]")

    try:
        commands.cmd_watch = cmd_watch
    except Exception:
        pass
