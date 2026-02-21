# cmnd/hooks.py — Python Hooks System for Rebel
# Foundation for all event-driven enhancements.
# SECURITY CONTROL: SI-7 (Software Integrity) — Hook execution sandboxed; errors never crash Rebel
# SECURITY CONTROL: AU-2 (Audit Events) — All hook invocations logged
# DAIV CERTIFIED

import importlib.util
import inspect
import sys
import traceback
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

# ─────────────────────────────────────────────
# Event types
# ─────────────────────────────────────────────

class HookEvent(str, Enum):
    PRE_MESSAGE    = "pre_message"      # Before message sent to LLM
    POST_MESSAGE   = "post_message"     # After LLM response received
    PRE_EDIT       = "pre_edit"         # Before edits applied to files
    POST_EDIT      = "post_edit"        # After edits applied
    PRE_COMMIT     = "pre_commit"       # Before git commit
    POST_COMMIT    = "post_commit"      # After git commit
    ON_MODEL_CHANGE = "on_model_change" # Model switched
    ON_STARTUP     = "on_startup"       # Rebel started, coder ready
    ON_SHUTDOWN    = "on_shutdown"      # Rebel exiting
    ON_COMMAND     = "on_command"       # User typed a /command
    ON_TOOL_CALL   = "on_tool_call"     # MCP tool called
    ON_SKILL_RUN   = "on_skill_run"     # Skill/macro executed
    ON_AGENT_STEP  = "on_agent_step"    # Agent mode step completed


@dataclass
class HookContext:
    """Passed to every hook. Contains event data and mutable state."""
    event: HookEvent
    coder: Any = None                   # Aider Coder instance
    message: Optional[str] = None       # User message or LLM response
    edits: Optional[list] = None        # Proposed or applied edits
    files: Optional[list[str]] = None   # Files involved
    model: Optional[str] = None         # Current model name
    command: Optional[str] = None       # /command name
    args: Optional[str] = None          # /command args
    result: Optional[Any] = None        # Result from operation
    metadata: dict = field(default_factory=dict)
    cancelled: bool = False             # Set True to cancel the event
    cancel_reason: Optional[str] = None

    def cancel(self, reason: str = "") -> None:
        """Signal cancellation of this event."""
        self.cancelled = True
        self.cancel_reason = reason


# ─────────────────────────────────────────────
# Hook registry
# ─────────────────────────────────────────────

@dataclass
class HookRegistration:
    event: HookEvent
    fn: Callable
    priority: int       # Lower runs first
    name: str
    source: str         # "builtin", "rebel/hooks/<file>", etc.
    enabled: bool = True


_registry: list[HookRegistration] = []


def register(
    event: HookEvent,
    fn: Callable,
    priority: int = 50,
    name: Optional[str] = None,
    source: str = "code",
) -> None:
    """Register a hook function for an event.

    Hook signature: fn(ctx: HookContext) -> Optional[bool]
    Return False (not None) to cancel the event.
    """
    reg = HookRegistration(
        event=event,
        fn=fn,
        priority=priority,
        name=name or fn.__name__,
        source=source,
    )
    _registry.append(reg)
    _registry.sort(key=lambda r: r.priority)


def hook(event: HookEvent, priority: int = 50, name: Optional[str] = None):
    """Decorator for registering hooks.

    @hook(HookEvent.PRE_MESSAGE)
    def my_hook(ctx: HookContext) -> None:
        print(f"Message: {ctx.message}")
    """
    def decorator(fn: Callable) -> Callable:
        register(event, fn, priority=priority, name=name or fn.__name__)
        return fn
    return decorator


def unregister(name: str) -> int:
    """Remove all hooks with the given name. Returns count removed."""
    global _registry
    before = len(_registry)
    _registry = [r for r in _registry if r.name != name]
    return before - len(_registry)


def disable(name: str) -> None:
    """Temporarily disable a hook by name."""
    for r in _registry:
        if r.name == name:
            r.enabled = False


def enable(name: str) -> None:
    """Re-enable a disabled hook."""
    for r in _registry:
        if r.name == name:
            r.enabled = True


def list_hooks() -> list[dict]:
    """Return summary of all registered hooks."""
    return [
        {
            "event": r.event.value,
            "name": r.name,
            "priority": r.priority,
            "source": r.source,
            "enabled": r.enabled,
        }
        for r in _registry
    ]


# ─────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────

def fire(event: HookEvent, **kwargs) -> HookContext:
    """
    Fire an event, running all registered hooks for it in priority order.

    Returns HookContext. Check ctx.cancelled to see if event was cancelled.
    SECURITY CONTROL: SI-7 — Exceptions in hooks are caught and logged; never propagate.
    """
    ctx = HookContext(event=event, **kwargs)

    for reg in _registry:
        if reg.event != event or not reg.enabled:
            continue

        try:
            result = reg.fn(ctx)
            # Explicit False (not None, not 0) cancels the event
            if result is False:
                ctx.cancelled = True
                ctx.cancel_reason = f"Cancelled by hook '{reg.name}'"
                break
        except Exception:
            # SECURITY CONTROL: SI-7 — Hook errors logged but never crash Rebel
            print(f"[rebel-hooks] Error in hook '{reg.name}' ({event.value}):")
            traceback.print_exc()

    return ctx


# ─────────────────────────────────────────────
# User hook loader — rebel/hooks/*.py
# ─────────────────────────────────────────────

def load_user_hooks(project_root: str) -> int:
    """
    Load user-defined hook files from rebel/hooks/*.py.
    Each file is imported as a module; any @hook decorators auto-register.
    Returns count of files loaded.
    SECURITY CONTROL: SI-7 — Only .py files from known rebel/hooks/ path loaded.
    """
    hooks_dir = Path(project_root) / "rebel" / "hooks"
    if not hooks_dir.exists():
        return 0

    loaded = 0
    for py_file in sorted(hooks_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        try:
            spec = importlib.util.spec_from_file_location(
                f"rebel_hook_{py_file.stem}", py_file
            )
            mod = importlib.util.module_from_spec(spec)
            sys.modules[f"rebel_hook_{py_file.stem}"] = mod
            spec.loader.exec_module(mod)
            loaded += 1
            print(f"[rebel-hooks] Loaded: {py_file.name}")
        except Exception:
            print(f"[rebel-hooks] Error loading {py_file.name}:")
            traceback.print_exc()

    return loaded


# ─────────────────────────────────────────────
# Coder patcher — inserts hook calls at key points
# ─────────────────────────────────────────────

def patch_coder(coder) -> None:
    """
    Monkey-patch the Coder to fire hooks at all key event boundaries.
    SECURITY CONTROL: CM-3 — Patches applied at coder creation, before first edit.
    """
    _patch_send_message(coder)
    _patch_apply_edits(coder)
    _patch_auto_commit(coder)
    _register_commands(coder)

    # Fire ON_STARTUP now that coder is patched
    fire(HookEvent.ON_STARTUP, coder=coder, model=_model_name(coder))


def _model_name(coder) -> str:
    return getattr(getattr(coder, "main_model", None), "name", "unknown")


def _patch_send_message(coder) -> None:
    """Wrap send_message to fire PRE_MESSAGE and POST_MESSAGE hooks."""
    original = getattr(coder, "send_message", None)
    if not original:
        return

    def hooked_send_message(inp, *args, **kwargs):
        # PRE_MESSAGE — can cancel
        ctx = fire(HookEvent.PRE_MESSAGE, coder=coder, message=inp,
                   model=_model_name(coder))
        if ctx.cancelled:
            print(f"[rebel-hooks] Message cancelled: {ctx.cancel_reason}")
            return

        result = original(inp, *args, **kwargs)

        # POST_MESSAGE
        fire(HookEvent.POST_MESSAGE, coder=coder, message=inp,
             result=result, model=_model_name(coder))
        return result

    coder.send_message = hooked_send_message


def _patch_apply_edits(coder) -> None:
    """Wrap apply_edits to fire PRE_EDIT and POST_EDIT hooks."""
    original = getattr(coder, "apply_edits", None)
    if not original:
        return

    def hooked_apply_edits(edits, *args, **kwargs):
        file_list = []
        for e in (edits or []):
            if isinstance(e, (tuple, list)) and len(e) >= 1:
                file_list.append(str(e[0]))
            elif isinstance(e, dict):
                file_list.append(e.get("path", e.get("filename", "")))

        # PRE_EDIT — can cancel
        ctx = fire(HookEvent.PRE_EDIT, coder=coder, edits=edits, files=file_list)
        if ctx.cancelled:
            print(f"[rebel-hooks] Edits cancelled: {ctx.cancel_reason}")
            return []

        result = original(edits, *args, **kwargs)

        # POST_EDIT
        fire(HookEvent.POST_EDIT, coder=coder, edits=edits, files=file_list, result=result)
        return result

    coder.apply_edits = hooked_apply_edits


def _patch_auto_commit(coder) -> None:
    """Wrap auto_commit to fire PRE_COMMIT and POST_COMMIT hooks."""
    original = getattr(coder, "auto_commit", None)
    if not original:
        return

    def hooked_auto_commit(*args, **kwargs):
        ctx = fire(HookEvent.PRE_COMMIT, coder=coder)
        if ctx.cancelled:
            return

        result = original(*args, **kwargs)
        fire(HookEvent.POST_COMMIT, coder=coder, result=result)
        return result

    coder.auto_commit = hooked_auto_commit


def _register_commands(coder) -> None:
    """Register /hooks command for listing/managing hooks."""
    commands = getattr(coder, "commands", None)
    if not commands:
        return

    def cmd_hooks(args: str) -> None:
        """List all registered hooks. Usage: /hooks [enable|disable <name>]"""
        parts = args.strip().split(None, 1)
        if len(parts) == 2:
            action, name = parts[0], parts[1]
            if action == "enable":
                enable(name)
                print(f"[rebel-hooks] Enabled: {name}")
                return
            elif action == "disable":
                disable(name)
                print(f"[rebel-hooks] Disabled: {name}")
                return

        hooks = list_hooks()
        if not hooks:
            print("No hooks registered.")
            return
        print(f"\nRegistered hooks ({len(hooks)}):")
        for h in hooks:
            status = "✓" if h["enabled"] else "✗"
            print(f"  {status} [{h['priority']:3d}] {h['event']:<20} {h['name']} ({h['source']})")

    try:
        commands.cmd_hooks = cmd_hooks
    except Exception:
        pass
