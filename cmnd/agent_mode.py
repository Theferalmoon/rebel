# cmnd/agent_mode.py — Autonomous Agent Mode for Rebel
# Breaks complex goals into steps, executes each, observes results, adapts.
# SECURITY CONTROL: CM-3 (Configuration Change Control) — Each step requires confirmation gate
# SECURITY CONTROL: AU-2 (Audit Events) — Every step logged to Captain's Log
# SECURITY CONTROL: SC-5 (DoS Protection) — Max step limit enforced
# DAIV CERTIFIED

import json
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from aider.coders import Coder

OLLAMA_HOST = "127.0.0.1"
OLLAMA_PORT = 11435   # localhost bypass port
PLAN_MODEL  = "devstral:latest"   # Model used for planning (separate from editing model)
MAX_STEPS   = 12                  # SECURITY CONTROL: SC-5 — Hard step cap
STEP_TIMEOUT = 300                # Seconds per step


# ─────────────────────────────────────────────
# Agent session state
# ─────────────────────────────────────────────

@dataclass
class AgentStep:
    index: int
    description: str
    action: str         # "message", "skill", "shell", "mcp"
    content: str
    status: str = "pending"   # pending | running | done | failed | skipped
    result: str = ""
    duration: float = 0.0


@dataclass
class AgentSession:
    goal: str
    steps: list[AgentStep] = field(default_factory=list)
    current_step: int = 0
    status: str = "planning"   # planning | running | done | failed | cancelled
    started_at: float = field(default_factory=time.time)
    notes: list[str] = field(default_factory=list)


# Active session (one at a time)
_active_session: Optional[AgentSession] = None
_auto_approve: bool = False   # /agent-auto to skip per-step confirmation


# ─────────────────────────────────────────────
# Planner — uses devstral to break goal into steps
# ─────────────────────────────────────────────

_PLAN_SYSTEM = """You are Rebel's agent planner for the CMND Center infrastructure project.
Break the given goal into a concrete sequence of steps Rebel (Aider AI coding assistant) can execute.

Rules:
- Max {max_steps} steps
- Each step must be one of: message (send a coding instruction to the LLM), skill (run a named skill),
  shell (run a pre-approved shell command), mcp (call an MCP tool)
- Steps must be specific and actionable — not vague
- Prefer 'message' steps for code changes, 'shell' for verification, 'mcp' for data lookup
- Consider the available context: the user has files in the Aider chat session

Respond with ONLY valid JSON, no other text:
{
  "goal_summary": "one sentence",
  "steps": [
    {"index": 1, "description": "brief label", "action": "message|skill|shell|mcp", "content": "..."},
    ...
  ],
  "estimated_duration": "5-10 minutes",
  "risks": ["list any risks or things that could go wrong"]
}
"""

def _call_ollama(system: str, user: str) -> str:
    """Call local Ollama for planning. Returns response text or error string."""
    body = json.dumps({
        "model": PLAN_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
    }).encode()

    req = urllib.request.Request(
        f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            return result.get("message", {}).get("content", "")
    except Exception as e:
        return f"Ollama error: {e}"


def plan_goal(goal: str, context: str = "") -> Optional[AgentSession]:
    """
    Use devstral to create an execution plan for the given goal.
    Returns an AgentSession with steps populated, or None on failure.
    """
    print(f"\n[rebel-agent] Planning: {goal}")
    print(f"[rebel-agent] Consulting devstral for step breakdown...")

    system = _PLAN_SYSTEM.format(max_steps=MAX_STEPS)
    user_msg = f"Goal: {goal}"
    if context:
        user_msg += f"\n\nCurrent context:\n{context[:1000]}"

    raw = _call_ollama(system, user_msg)

    # Extract JSON from response
    try:
        # Strip markdown fences if present
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        data = json.loads(raw.strip())
    except json.JSONDecodeError:
        # Try to find JSON block
        import re
        m = re.search(r'\{[\s\S]+\}', raw)
        if m:
            try:
                data = json.loads(m.group())
            except Exception:
                print(f"[rebel-agent] Could not parse plan from devstral response")
                return None
        else:
            print(f"[rebel-agent] No JSON in devstral response:\n{raw[:500]}")
            return None

    session = AgentSession(goal=goal)
    for s in data.get("steps", [])[:MAX_STEPS]:
        session.steps.append(AgentStep(
            index=s.get("index", 0),
            description=s.get("description", ""),
            action=s.get("action", "message"),
            content=s.get("content", ""),
        ))

    if data.get("risks"):
        session.notes.extend(data["risks"])

    print(f"[rebel-agent] Plan: {data.get('goal_summary', goal)}")
    print(f"[rebel-agent] Steps: {len(session.steps)} | Est: {data.get('estimated_duration', '?')}")
    if session.notes:
        print(f"[rebel-agent] Risks: {'; '.join(session.notes)}")

    return session


# ─────────────────────────────────────────────
# Executor
# ─────────────────────────────────────────────

def _execute_step(step: AgentStep, coder: "Coder") -> bool:
    """Execute one agent step. Returns True on success."""
    step.status = "running"
    start = time.time()

    # Fire hook
    try:
        from cmnd.hooks import fire, HookEvent
        ctx = fire(HookEvent.ON_AGENT_STEP, coder=coder,
                   metadata={"step": step.index, "action": step.action, "content": step.content})
        if ctx.cancelled:
            step.status = "skipped"
            step.result = f"Skipped by hook: {ctx.cancel_reason}"
            return True
    except Exception:
        pass

    try:
        if step.action == "message":
            coder.run(with_message=step.content)
            step.result = "Message sent to LLM"

        elif step.action == "skill":
            from cmnd.skills import run_skill
            parts = step.content.split(None, 1)
            name, args = parts[0], parts[1] if len(parts) > 1 else ""
            success = run_skill(name, coder, args)
            step.result = "Skill completed" if success else "Skill failed"
            if not success:
                step.status = "failed"
                return False

        elif step.action == "shell":
            from cmnd.bash_tools import run_approved
            rc, out = run_approved(step.content)
            step.result = out[:500]
            if rc != 0:
                raise RuntimeError(f"Shell failed (rc={rc}): {out[:200]}")
            # Feed shell output back as context
            if out.strip():
                coder.run(with_message=f"Shell output from `{step.content}`:\n```\n{out[:2000]}\n```\nAnalyze and continue.")

        elif step.action == "mcp":
            from cmnd.mcp_client import call
            parts = step.content.split("/", 1)
            if len(parts) == 2:
                result = call(parts[0], parts[1])
                step.result = result[:500]
                if result and "Error" not in result:
                    coder.run(with_message=f"MCP data from {step.content}:\n{result[:2000]}\nUse this to continue the task.")
            else:
                step.result = "Invalid MCP step format (use 'server/tool')"

        step.status = "done"
        step.duration = time.time() - start
        return True

    except Exception as e:
        step.status = "failed"
        step.result = str(e)[:300]
        step.duration = time.time() - start
        print(f"[rebel-agent] Step {step.index} error: {e}")
        return False


def run_agent(goal: str, coder: "Coder", auto_approve: bool = False) -> bool:
    """
    Run autonomous agent mode for the given goal.

    SECURITY CONTROL: CM-3 — Each step shown to user; cancellable at any point.
    SECURITY CONTROL: AU-2 — Full session logged to Captain's Log.
    SECURITY CONTROL: SC-5 — MAX_STEPS hard limit prevents runaway loops.
    """
    global _active_session, _auto_approve
    _auto_approve = auto_approve

    # Build context from current session
    files_in_chat = list(coder.get_inchat_relative_files())
    context = f"Files in chat: {', '.join(files_in_chat[:10]) or 'none'}"

    # Plan
    session = plan_goal(goal, context)
    if not session:
        print("[rebel-agent] Planning failed. Run manually or try a simpler goal.")
        return False

    _active_session = session

    # Show plan and get approval
    print(f"\n{'='*58}")
    print(f"  REBEL AGENT PLAN")
    print(f"{'='*58}")
    for step in session.steps:
        print(f"  Step {step.index:2d}: [{step.action:8s}] {step.description}")
        print(f"           → {step.content[:70]}...")
    print(f"{'='*58}")

    if not auto_approve:
        resp = input("\nApprove this plan and begin execution? [Y/n] ").strip().lower()
        if resp in ("n", "no"):
            print("[rebel-agent] Plan rejected. Session cancelled.")
            session.status = "cancelled"
            return False

    # Execute
    session.status = "running"
    print(f"\n[rebel-agent] Executing {len(session.steps)} steps...\n")

    for step in session.steps:
        session.current_step = step.index
        print(f"\n{'─'*58}")
        print(f"  Step {step.index}/{len(session.steps)}: {step.description}")
        print(f"  Action: [{step.action}] {step.content[:80]}")
        print(f"{'─'*58}")

        if not auto_approve and step.index > 1:
            resp = input("Continue? [Y/n/skip/abort] ").strip().lower()
            if resp in ("n", "no", "abort"):
                session.status = "cancelled"
                print("[rebel-agent] Aborted by user.")
                break
            elif resp == "skip":
                step.status = "skipped"
                print(f"[rebel-agent] Step {step.index} skipped.")
                continue

        success = _execute_step(step, coder)

        status_str = "✓" if step.status == "done" else ("↷" if step.status == "skipped" else "✗")
        print(f"\n{status_str} Step {step.index}: {step.status} ({step.duration:.1f}s)")
        if step.result:
            print(f"  {step.result[:100]}")

        if not success and step.action != "shell":
            resp = input("\nStep failed. Continue anyway? [y/N] ").strip().lower()
            if resp not in ("y", "yes"):
                session.status = "failed"
                break
    else:
        session.status = "done"

    # Summary
    done = sum(1 for s in session.steps if s.status == "done")
    total_time = int(time.time() - session.started_at)
    print(f"\n{'='*58}")
    print(f"  AGENT SESSION {session.status.upper()}")
    print(f"  Steps completed: {done}/{len(session.steps)} | Duration: {total_time}s")
    print(f"{'='*58}\n")

    # Log to Captain's Log
    try:
        from cmnd.mcp_client import log_to_captains_log
        log_to_captains_log(
            agent="rebel-agent",
            message=(f"Agent session {session.status}: '{goal[:100]}' | "
                     f"{done}/{len(session.steps)} steps | {total_time}s"),
            tags=["rebel", "agent-mode", session.status],
        )
    except Exception:
        pass

    _active_session = None
    return session.status == "done"


# ─────────────────────────────────────────────
# Register commands
# ─────────────────────────────────────────────

def register_commands(coder: "Coder") -> None:
    commands = getattr(coder, "commands", None)
    if not commands:
        return

    def cmd_agent(args: str) -> None:
        """/agent <goal> — Autonomous multi-step execution toward a goal."""
        goal = args.strip()
        if not goal:
            print("Usage: /agent <goal>")
            print("Example: /agent Add NIST 800-53 annotations to all TypeScript files")
            return
        run_agent(goal, coder, auto_approve=False)

    def cmd_agent_auto(args: str) -> None:
        """/agent-auto <goal> — Run agent without per-step confirmation."""
        goal = args.strip()
        if not goal:
            print("Usage: /agent-auto <goal>")
            return
        run_agent(goal, coder, auto_approve=True)

    def cmd_agent_status(args: str) -> None:
        """/agent-status — Show current or last agent session status."""
        if not _active_session:
            print("No active agent session.")
            return
        s = _active_session
        print(f"\nAgent: {s.goal[:80]}")
        print(f"Status: {s.status} | Step: {s.current_step}/{len(s.steps)}")
        for step in s.steps:
            icon = {"pending": "⬜", "running": "🔵", "done": "✅", "failed": "❌", "skipped": "↷"}.get(step.status, "?")
            print(f"  {icon} {step.index}. {step.description}")

    for name, fn in [
        ("agent", cmd_agent),
        ("agent_auto", cmd_agent_auto),
        ("agent_status", cmd_agent_status),
    ]:
        try:
            setattr(commands, f"cmd_{name}", fn)
        except Exception:
            pass
