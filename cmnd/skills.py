# cmnd/skills.py — Skills / Macro System for Rebel
# Reusable task templates. Define once in YAML, run with /skill <name>.
# SECURITY CONTROL: CM-7 (Least Functionality) — Skills execute only pre-defined safe steps
# SECURITY CONTROL: AU-2 (Audit Events) — Every skill run logged to Captain's Log
# DAIV CERTIFIED

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from aider.coders import Coder

try:
    import yaml
    _YAML_OK = True
except ImportError:
    _YAML_OK = False

# ─────────────────────────────────────────────
# Skill definition
# ─────────────────────────────────────────────

@dataclass
class SkillStep:
    """One step in a skill — either a message to send or a command to run."""
    type: str           # "message", "command", "shell", "mcp"
    content: str        # Message text, /command string, shell cmd, or "server/tool"
    model: Optional[str] = None     # Override model for this step
    args: Optional[dict] = None     # For MCP tool calls: arguments
    confirm: bool = False           # Pause and ask user before this step
    condition: Optional[str] = None # Skip if condition evaluates to False


@dataclass
class Skill:
    name: str
    description: str
    steps: list[SkillStep]
    tags: list[str] = field(default_factory=list)
    model_override: Optional[str] = None   # Override model for entire skill
    files: list[str] = field(default_factory=list)  # Files to add to chat
    source: str = "unknown"


# ─────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────

_skills: dict[str, Skill] = {}


def register_skill(skill: Skill) -> None:
    _skills[skill.name] = skill


def get_skill(name: str) -> Optional[Skill]:
    return _skills.get(name)


def list_skills() -> list[dict]:
    return [
        {"name": s.name, "description": s.description, "tags": s.tags, "source": s.source}
        for s in _skills.values()
    ]


# ─────────────────────────────────────────────
# YAML loader — rebel/skills/*.yml
# ─────────────────────────────────────────────

def load_skills_dir(project_root: str) -> int:
    """
    Load skill definitions from rebel/skills/*.yml.
    SECURITY CONTROL: CM-7 — Only YAML from known skills directory loaded.
    """
    skills_dir = Path(project_root) / "rebel" / "skills"
    if not skills_dir.exists() or not _YAML_OK:
        return 0

    loaded = 0
    for yml_file in sorted(skills_dir.glob("*.yml")):
        try:
            data = yaml.safe_load(yml_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue

            steps = []
            for s in data.get("steps", []):
                steps.append(SkillStep(
                    type=s.get("type", "message"),
                    content=s.get("content", ""),
                    model=s.get("model"),
                    args=s.get("args"),
                    confirm=s.get("confirm", False),
                    condition=s.get("condition"),
                ))

            skill = Skill(
                name=data.get("name", yml_file.stem),
                description=data.get("description", ""),
                steps=steps,
                tags=data.get("tags", []),
                model_override=data.get("model"),
                files=data.get("files", []),
                source=str(yml_file),
            )
            register_skill(skill)
            loaded += 1
        except Exception as e:
            print(f"[rebel-skills] Error loading {yml_file.name}: {e}")

    if loaded:
        print(f"[rebel-skills] Loaded {loaded} skill(s)")
    return loaded


# ─────────────────────────────────────────────
# Execution engine
# ─────────────────────────────────────────────

def run_skill(name: str, coder: "Coder", extra_args: str = "") -> bool:
    """
    Execute a named skill against the current coder session.

    SECURITY CONTROL: AU-2 — Skill execution logged to Captain's Log.
    SECURITY CONTROL: CM-7 — Steps execute in order; shell steps use bash_tools allowlist.
    """
    skill = get_skill(name)
    if not skill:
        print(f"[rebel-skills] Unknown skill: '{name}'")
        print(f"Available: {', '.join(_skills.keys()) or 'none'}")
        return False

    print(f"\n{'='*55}")
    print(f"  REBEL SKILL: {skill.name}")
    print(f"  {skill.description}")
    print(f"{'='*55}")

    # Fire ON_SKILL_RUN hook
    try:
        from cmnd.hooks import fire, HookEvent
        ctx = fire(HookEvent.ON_SKILL_RUN, coder=coder,
                   metadata={"skill": name, "args": extra_args})
        if ctx.cancelled:
            print(f"[rebel-skills] Skill cancelled by hook: {ctx.cancel_reason}")
            return False
    except Exception:
        pass

    start = time.time()
    success = True

    for i, step in enumerate(skill.steps, 1):
        print(f"\n[Step {i}/{len(skill.steps)}] {step.type}: {step.content[:60]}...")

        # Optional confirmation gate
        if step.confirm:
            resp = input("  Continue with this step? [Y/n] ").strip().lower()
            if resp in ("n", "no"):
                print("[rebel-skills] Skipped.")
                continue

        try:
            if step.type == "message":
                _run_message_step(step, coder, extra_args if i == 1 else "")

            elif step.type == "command":
                _run_command_step(step, coder)

            elif step.type == "shell":
                _run_shell_step(step, coder)

            elif step.type == "mcp":
                _run_mcp_step(step, coder)

            else:
                print(f"[rebel-skills] Unknown step type: {step.type}")

        except Exception as e:
            print(f"[rebel-skills] Step {i} failed: {e}")
            success = False
            break

    duration = int(time.time() - start)
    status = "completed" if success else "failed"
    print(f"\n{'='*55}")
    print(f"  Skill '{name}' {status} in {duration}s")
    print(f"{'='*55}\n")

    # Log to Captain's Log
    try:
        from cmnd.mcp_client import log_to_captains_log
        log_to_captains_log(
            agent="rebel-skills",
            message=f"Skill '{name}' {status} in {duration}s | args={extra_args[:100]}",
            tags=["rebel", "skill", name, status],
        )
    except Exception:
        pass

    return success


def _run_message_step(step: SkillStep, coder: "Coder", extra_args: str) -> None:
    content = step.content
    if extra_args:
        content = content.replace("{args}", extra_args).replace("{input}", extra_args)

    # Temporarily switch model if overridden
    original_model = None
    if step.model:
        original_model = getattr(coder.main_model, "name", None)
        # Use Aider's cmd_model if available
        cmd_model = getattr(getattr(coder, "commands", None), "cmd_model", None)
        if cmd_model:
            cmd_model(step.model)

    coder.run(with_message=content)

    if original_model and step.model:
        cmd_model = getattr(getattr(coder, "commands", None), "cmd_model", None)
        if cmd_model:
            cmd_model(original_model)


def _run_command_step(step: SkillStep, coder: "Coder") -> None:
    commands = getattr(coder, "commands", None)
    if not commands:
        return
    cmd_text = step.content.lstrip("/")
    parts = cmd_text.split(None, 1)
    cmd_name = parts[0].replace("-", "_")
    cmd_args = parts[1] if len(parts) > 1 else ""
    cmd_fn = getattr(commands, f"cmd_{cmd_name}", None)
    if cmd_fn:
        cmd_fn(cmd_args)
    else:
        print(f"[rebel-skills] Command not found: /{cmd_name}")


def _run_shell_step(step: SkillStep, coder: "Coder") -> None:
    from cmnd.bash_tools import run_approved
    rc, out = run_approved(step.content)
    print(out)
    if rc != 0:
        raise RuntimeError(f"Shell step failed (rc={rc}): {step.content}")


def _run_mcp_step(step: SkillStep, coder: "Coder") -> None:
    """content format: "server_name/tool_name" """
    from cmnd.mcp_client import call
    parts = step.content.split("/", 1)
    if len(parts) != 2:
        print(f"[rebel-skills] MCP step format: 'server/tool'. Got: {step.content}")
        return
    server, tool = parts
    result = call(server, tool, step.args or {})
    print(f"[rebel-skills] MCP result: {result[:300]}")


# ─────────────────────────────────────────────
# Register /skill and /skills commands
# ─────────────────────────────────────────────

def register_commands(coder: "Coder") -> None:
    commands = getattr(coder, "commands", None)
    if not commands:
        return

    def cmd_skill(args: str) -> None:
        """Run a named skill. Usage: /skill <name> [extra args for {args} placeholder]"""
        parts = args.strip().split(None, 1)
        if not parts:
            print("Usage: /skill <name> [args]")
            print(f"Available: {', '.join(_skills.keys()) or 'none (no skills loaded)'}")
            return
        name = parts[0]
        extra = parts[1] if len(parts) > 1 else ""
        run_skill(name, coder, extra)

    def cmd_skills(args: str) -> None:
        """List all available skills. Usage: /skills [tag]"""
        tag_filter = args.strip().lower()
        skills = list_skills()
        if tag_filter:
            skills = [s for s in skills if tag_filter in [t.lower() for t in s["tags"]]]
        if not skills:
            print("No skills loaded. Add YAML files to rebel/skills/")
            return
        print(f"\nAvailable skills ({len(skills)}):")
        for s in skills:
            tags = f"  [{', '.join(s['tags'])}]" if s["tags"] else ""
            print(f"  /skill {s['name']:<25} {s['description']}{tags}")

    try:
        commands.cmd_skill = cmd_skill
        commands.cmd_skills = cmd_skills
    except Exception:
        pass
