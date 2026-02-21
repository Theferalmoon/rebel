#!/usr/bin/env python3
# rebel_main.py — Rebel entry point (CMND Center enhanced Aider fork)
# Loads all cmnd/ extensions before handing off to aider main.
# SECURITY CONTROL: SC-7 (Boundary Protection) — All CMND extensions load before any model call
# SECURITY CONTROL: PM-11 (Mission/Business Process) — CMND Center mission-aligned entry point
# SECURITY CONTROL: AU-2 (Audit Events) — Session boundaries logged to Captain's Log
# DAIV CERTIFIED

import sys
import os
from pathlib import Path

# ─────────────────────────────────────────────
# Ensure project root is on PYTHONPATH
# ─────────────────────────────────────────────
REBEL_DIR = Path(__file__).parent
if str(REBEL_DIR) not in sys.path:
    sys.path.insert(0, str(REBEL_DIR))

PROJECT_ROOT = os.environ.get(
    "REBEL_PROJECT_ROOT",
    "/home/theferalmoon/local-opus-lab",
)

# ─────────────────────────────────────────────
# Step 1: Model compliance check — BEFORE anything else
# SECURITY CONTROL: SI-7 — Block restricted models at process start
# ─────────────────────────────────────────────

def _check_model_compliance() -> None:
    """Extract model name from sys.argv and enforce compliance before Aider starts."""
    from cmnd.model_compliance import enforce_model, patch_model_switches

    model_name = None
    for i, arg in enumerate(sys.argv):
        if arg in ("--model", "-m") and i + 1 < len(sys.argv):
            model_name = sys.argv[i + 1]
            break
        elif arg.startswith("--model="):
            model_name = arg.split("=", 1)[1]
            break

    # Also check .rebel.conf.yml for model setting
    if not model_name:
        conf_path = Path(PROJECT_ROOT) / "rebel" / ".rebel.conf.yml"
        if conf_path.exists():
            import re
            content = conf_path.read_text()
            m = re.search(r"^model:\s*(.+)$", content, re.MULTILINE)
            if m:
                model_name = m.group(1).strip().strip('"\'')

    if model_name:
        enforce_model(model_name, exit_on_block=True)


# ─────────────────────────────────────────────
# Step 2: Banner
# ─────────────────────────────────────────────

def _print_banner() -> None:
    print("=" * 60)
    print("  REBEL — CMND Center AI Development Environment")
    print("  UNLV Rebels | Nevada Public Sector | DAIV CERTIFIED")
    print("  PQC: Routes through pqc-proxy (ML-KEM-768 + ML-DSA-65)")
    print("=" * 60)
    print("  Extensions: model-compliance | captain's-log | shared-memory")
    print("              plan-mode | bash-tools | chromadb | dashboard")
    print("=" * 60)

# ─────────────────────────────────────────────
# Step 3: Startup context injection
# Builds a temp file with MEMORY.md + TASKS.md + Captain's Log
# and injects it as --read argument before Aider processes args.
# ─────────────────────────────────────────────

def _inject_startup_context() -> str | None:
    """
    Assemble startup context from shared state and Captain's Log.
    Write to a temp file and inject it as a --read argument.
    SECURITY CONTROL: MP-4 — Temp file mode 0600, cleaned up on exit.
    """
    import tempfile
    import atexit

    try:
        from cmnd.shared_state import build_startup_context
        context = build_startup_context()
    except Exception as e:
        print(f"[rebel] shared_state load error: {e}")
        context = ""

    try:
        from cmnd.mcp_client import get_recent_context
        log_ctx = get_recent_context(hours=24)
        if log_ctx and "Error" not in log_ctx:
            context += f"\n## Captain's Log (last 24h)\n{log_ctx}\n"
    except Exception:
        pass

    if not context.strip():
        return None

    fd, path = tempfile.mkstemp(prefix="rebel-ctx-", suffix=".md")
    os.chmod(path, 0o600)

    header = (
        "<!-- REBEL STARTUP CONTEXT — MEMORY.md + TASKS.md + Captain's Log -->\n"
        "<!-- This file is auto-generated at session start. Do not edit. -->\n\n"
    )
    with os.fdopen(fd, "w") as f:
        f.write(header + context)

    atexit.register(lambda: os.unlink(path) if os.path.exists(path) else None)
    print(f"[rebel] Startup context injected ({len(context)} chars)")
    return path


# ─────────────────────────────────────────────
# Step 4: Post-init hooks — patch the Coder after Aider creates it
# ─────────────────────────────────────────────

def _install_coder_hooks() -> None:
    """
    Monkey-patch Coder.create() to apply all CMND extensions after creation.
    SECURITY CONTROL: CM-3 — All patches applied before first model call.
    """
    try:
        from aider.coders import Coder
        original_create = Coder.create.__func__ if hasattr(Coder.create, "__func__") else Coder.create

        @classmethod  # type: ignore
        def patched_create(cls, *args, **kwargs):
            coder = original_create(cls, *args, **kwargs)
            _apply_coder_patches(coder)
            return coder

        Coder.create = patched_create

        # Also patch model switch compliance
        from cmnd.model_compliance import patch_model_switches
        patch_model_switches(Coder)

    except Exception as e:
        print(f"[rebel] Could not install coder hooks: {e}")


def _apply_coder_patches(coder) -> None:
    """Apply all CMND extension patches to a Coder instance."""
    # Shared state commands (/tasks, /task-done, /remember)
    try:
        from cmnd.shared_state import register_commands
        register_commands(coder)
    except Exception as e:
        print(f"[rebel] shared_state patch error: {e}")

    # Plan mode (/plan, /plan-on, /plan-off)
    try:
        from cmnd.plan_mode import patch_coder as patch_plan
        patch_plan(coder)
    except Exception as e:
        print(f"[rebel] plan_mode patch error: {e}")

    # Bash tools (/run-approved, /approved-list)
    try:
        from cmnd.bash_tools import patch_coder as patch_bash
        patch_bash(coder)
    except Exception as e:
        print(f"[rebel] bash_tools patch error: {e}")

    # ChromaDB semantic search (/search)
    try:
        from cmnd.chroma_repomap import patch_coder as patch_chroma
        patch_chroma(coder)
    except Exception as e:
        print(f"[rebel] chroma_repomap patch error: {e}")

    # Captain's Log exit hook
    try:
        from cmnd.context_bridge import install_exit_hook
        install_exit_hook()
    except Exception as e:
        print(f"[rebel] context_bridge patch error: {e}")

    print("[rebel] All extensions active.")
    _log_session_start(coder)


def _log_session_start(coder) -> None:
    """Log session start to Captain's Log (best-effort)."""
    try:
        model_name = getattr(coder.main_model, "name", "unknown")
        from cmnd.mcp_client import log_to_captains_log
        log_to_captains_log(
            agent="rebel",
            message=f"Rebel session started | model={model_name} | "
                    f"files={','.join(list(coder.get_inchat_relative_files())[:5]) or 'none'}",
            tags=["rebel", "session-start"],
        )
    except Exception:
        pass


# ─────────────────────────────────────────────
# Step 5: Dashboard (background)
# ─────────────────────────────────────────────

def _start_dashboard() -> None:
    """Start the Rebel status dashboard on port 3033 in background."""
    try:
        from cmnd.dashboard import start_background
        start_background()
    except Exception:
        pass  # Dashboard is optional


# ─────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────

def main():
    _check_model_compliance()
    _print_banner()

    # Inject startup context as --read argument
    ctx_path = _inject_startup_context()
    if ctx_path and "--read" not in sys.argv and ctx_path not in sys.argv:
        sys.argv.extend(["--read", ctx_path])

    # Install coder hooks before Aider runs
    _install_coder_hooks()

    # Start dashboard in background
    _start_dashboard()

    # Hand off to Aider main
    from aider.main import main as aider_main
    aider_main()


if __name__ == "__main__":
    main()
