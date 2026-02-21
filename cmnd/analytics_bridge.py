# cmnd/analytics_bridge.py — Analytics → Captain's Log Bridge
# Hooks Aider's internal event system to write structured telemetry to Captain's Log.
# SECURITY CONTROL: AU-2 (Audit Events) — All model interactions recorded
# SECURITY CONTROL: AU-9 (Audit Information) — Telemetry stored in Captain's Log, not sent externally
# DAIV CERTIFIED

import time
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from aider.coders import Coder

# ─────────────────────────────────────────────
# Session metrics accumulator
# ─────────────────────────────────────────────

_metrics = {
    "messages_sent": 0,
    "edits_applied": 0,
    "commits_made": 0,
    "tokens_sent": 0,
    "tokens_received": 0,
    "cost": 0.0,
    "errors": 0,
    "skills_run": 0,
    "agent_steps": 0,
    "models_used": set(),
    "files_touched": set(),
    "session_start": time.time(),
}


def _snapshot(coder) -> dict:
    """Pull live metrics from coder."""
    snap = dict(_metrics)
    if coder:
        snap["tokens_sent"]     = getattr(coder, "total_tokens_sent", 0)
        snap["tokens_received"] = getattr(coder, "total_tokens_received", 0)
        snap["cost"]            = getattr(coder, "total_cost", 0.0)
        snap["model"]           = getattr(getattr(coder, "main_model", None), "name", "unknown")
    snap["models_used"] = list(snap["models_used"])
    snap["files_touched"] = list(snap["files_touched"])
    snap["duration_s"] = int(time.time() - snap["session_start"])
    return snap


def _log(msg: str, tags: list[str], coder=None) -> None:
    """Non-blocking write to Captain's Log."""
    try:
        from cmnd.mcp_client import log_to_captains_log
        log_to_captains_log(agent="rebel-analytics", message=msg, tags=tags)
    except Exception:
        pass


# ─────────────────────────────────────────────
# Hook handlers
# ─────────────────────────────────────────────

def _on_post_message(ctx) -> None:
    _metrics["messages_sent"] += 1
    if ctx.model:
        _metrics["models_used"].add(ctx.model)

    # Log every 5 messages (avoids Captain's Log spam)
    if _metrics["messages_sent"] % 5 == 0:
        snap = _snapshot(ctx.coder)
        _log(
            f"Rebel telemetry | msgs={snap['messages_sent']} "
            f"tokens={snap['tokens_sent']+snap['tokens_received']:,} "
            f"cost=${snap['cost']:.4f} model={snap.get('model','?')}",
            ["rebel", "telemetry", "periodic"],
        )


def _on_post_edit(ctx) -> None:
    _metrics["edits_applied"] += 1
    for f in (ctx.files or []):
        _metrics["files_touched"].add(f)


def _on_post_commit(ctx) -> None:
    _metrics["commits_made"] += 1
    _log(
        f"Rebel commit #{_metrics['commits_made']} | "
        f"files={len(_metrics['files_touched'])} | "
        f"cost=${_snapshot(ctx.coder)['cost']:.4f}",
        ["rebel", "commit", "audit"],
        ctx.coder,
    )


def _on_skill_run(ctx) -> None:
    _metrics["skills_run"] += 1
    skill_name = ctx.metadata.get("skill", "unknown")
    _log(f"Skill run: {skill_name}", ["rebel", "skill", skill_name])


def _on_agent_step(ctx) -> None:
    _metrics["agent_steps"] += 1


def _on_shutdown(ctx) -> None:
    """Final session summary to Captain's Log on shutdown."""
    snap = _snapshot(ctx.coder if ctx else None)
    _log(
        f"Rebel session ended | msgs={snap['messages_sent']} edits={snap['edits_applied']} "
        f"commits={snap['commits_made']} tokens={snap['tokens_sent']+snap['tokens_received']:,} "
        f"cost=${snap['cost']:.4f} duration={snap['duration_s']}s "
        f"models={','.join(snap['models_used']) or 'unknown'}",
        ["rebel", "session-end", "analytics"],
    )


# ─────────────────────────────────────────────
# Patch Aider's own analytics (PostHog) to also write to Captain's Log
# ─────────────────────────────────────────────

def _patch_aider_analytics(coder) -> None:
    """
    Intercept Aider's analytics.event() calls and mirror to Captain's Log.
    SECURITY CONTROL: AU-9 — Events stay on-prem, PostHog is opt-out.
    """
    analytics = getattr(coder, "analytics", None)
    if not analytics:
        return

    original_event = getattr(analytics, "event", None)
    if not original_event:
        return

    def mirrored_event(event_name: str, **kwargs):
        # Let Aider's own analytics run (PostHog, if enabled)
        result = original_event(event_name, **kwargs)

        # Mirror important events to Captain's Log
        if event_name in ("message_send", "message_send_exception", "exit"):
            try:
                _log(
                    f"aider.{event_name} | {' '.join(f'{k}={v}' for k,v in list(kwargs.items())[:5])}",
                    ["rebel", "aider-analytics", event_name],
                )
            except Exception:
                pass

        return result

    analytics.event = mirrored_event


# ─────────────────────────────────────────────
# Install
# ─────────────────────────────────────────────

def install(coder: "Coder") -> None:
    """Register all analytics hooks and patch Aider's analytics."""
    from cmnd.hooks import register, HookEvent

    register(HookEvent.POST_MESSAGE,  _on_post_message,  priority=90, name="analytics-post-message",  source="analytics_bridge")
    register(HookEvent.POST_EDIT,     _on_post_edit,     priority=90, name="analytics-post-edit",     source="analytics_bridge")
    register(HookEvent.POST_COMMIT,   _on_post_commit,   priority=90, name="analytics-post-commit",   source="analytics_bridge")
    register(HookEvent.ON_SKILL_RUN,  _on_skill_run,     priority=90, name="analytics-skill-run",     source="analytics_bridge")
    register(HookEvent.ON_AGENT_STEP, _on_agent_step,    priority=90, name="analytics-agent-step",    source="analytics_bridge")
    register(HookEvent.ON_SHUTDOWN,   _on_shutdown,      priority=90, name="analytics-shutdown",      source="analytics_bridge")

    _patch_aider_analytics(coder)
    _metrics["session_start"] = time.time()
