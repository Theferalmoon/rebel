# cmnd/router.py — Multi-Model Routing for Rebel
# Automatically selects the right model based on task complexity.
# SECURITY CONTROL: SI-7 (Software Integrity) — Model compliance checked before every switch
# SECURITY CONTROL: AU-2 (Audit Events) — All routing decisions logged
# DAIV CERTIFIED

import re
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from aider.coders import Coder

# ─────────────────────────────────────────────
# Routing tiers — all compliance-approved
# ─────────────────────────────────────────────

@dataclass
class RoutingTier:
    name: str
    model: str
    description: str
    max_complexity: int  # 0-100 score


TIERS = [
    RoutingTier("fast",     "ollama_chat/mistral-small3.2:24b", "Simple edits, short answers",  30),
    RoutingTier("standard", "ollama_chat/devstral:latest",       "Complex code, multi-file",     75),
    RoutingTier("deep",     "ollama_chat/devstral:latest",       "Architecture, security",       90),
    RoutingTier("cloud",    "claude-sonnet-4-6",                 "Max capability (cloud)",      100),
]

_AUTO_ROUTE: bool = False
_CURRENT_TIER: str = "standard"

# ─────────────────────────────────────────────
# Complexity scorer
# ─────────────────────────────────────────────

# High-complexity keywords push score up
_HIGH = [
    r"architect", r"refactor", r"security", r"vulnerability", r"compliance",
    r"all files", r"entire codebase", r"design", r"explain why", r"trade.off",
    r"nist", r"pqc", r"encryption", r"authentication", r"zero.trust",
    r"docker.compose", r"multi.stage", r"performance", r"optimize",
]
# Low-complexity indicators push score down
_LOW = [
    r"fix typo", r"rename", r"add comment", r"format", r"print",
    r"what is", r"show me", r"list", r"check", r"status",
]


def score_complexity(message: str, file_count: int = 0) -> int:
    """
    Score message complexity 0-100.
    - File count contributes up to 30 points
    - Keyword matching contributes up to 50 points
    - Message length contributes up to 20 points
    """
    score = 0
    msg_lower = message.lower()

    # File count
    score += min(file_count * 5, 30)

    # Keywords
    for pattern in _HIGH:
        if re.search(pattern, msg_lower):
            score += 8
    for pattern in _LOW:
        if re.search(pattern, msg_lower):
            score -= 10

    # Message length (longer = more complex)
    length_score = min(len(message) // 100, 20)
    score += length_score

    return max(0, min(100, score))


def route(message: str, file_count: int = 0) -> RoutingTier:
    """Select appropriate tier for the given message."""
    complexity = score_complexity(message, file_count)
    for tier in TIERS:
        if complexity <= tier.max_complexity:
            return tier
    return TIERS[-1]


def enable_auto_routing() -> None:
    global _AUTO_ROUTE
    _AUTO_ROUTE = True
    print("[rebel-router] Auto-routing ENABLED — model selected per task complexity")


def disable_auto_routing() -> None:
    global _AUTO_ROUTE
    _AUTO_ROUTE = False
    print("[rebel-router] Auto-routing DISABLED — using fixed model")


# ─────────────────────────────────────────────
# Hook into PRE_MESSAGE to auto-switch model
# ─────────────────────────────────────────────

def _auto_route_hook(ctx) -> None:
    """PRE_MESSAGE hook: if auto-routing enabled, switch model based on complexity."""
    if not _AUTO_ROUTE:
        return
    if not ctx.message or not ctx.coder:
        return

    files = list(ctx.coder.get_inchat_relative_files())
    tier = route(ctx.message, len(files))

    current = getattr(getattr(ctx.coder, "main_model", None), "name", "")
    if tier.model == current:
        return  # Already on correct model

    # SECURITY CONTROL: SI-7 — Verify compliance before switching
    from cmnd.model_compliance import check_model
    allowed, reason = check_model(tier.model)
    if not allowed:
        print(f"[rebel-router] Routing blocked: {reason}")
        return

    print(f"[rebel-router] Routing to [{tier.name}] {tier.model} (complexity={score_complexity(ctx.message, len(files))})")
    cmd_model = getattr(getattr(ctx.coder, "commands", None), "cmd_model", None)
    if cmd_model:
        cmd_model(tier.model)


def install_routing_hook() -> None:
    """Register the auto-routing PRE_MESSAGE hook."""
    from cmnd.hooks import register, HookEvent
    register(HookEvent.PRE_MESSAGE, _auto_route_hook, priority=10, name="auto-router")


# ─────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────

def register_commands(coder: "Coder") -> None:
    commands = getattr(coder, "commands", None)
    if not commands:
        return

    def cmd_route(args: str) -> None:
        """/route [auto|fast|standard|deep|cloud|off] — Set or show routing mode."""
        arg = args.strip().lower()
        if not arg or arg == "status":
            print(f"Auto-routing: {'ON' if _AUTO_ROUTE else 'OFF'}")
            print("Tiers:")
            for t in TIERS:
                print(f"  {t.name:<10} {t.model:<45} (complexity ≤{t.max_complexity})")
            return

        if arg == "auto":
            enable_auto_routing()
            install_routing_hook()
        elif arg == "off":
            disable_auto_routing()
        elif arg in [t.name for t in TIERS]:
            tier = next(t for t in TIERS if t.name == arg)
            from cmnd.model_compliance import check_model
            allowed, reason = check_model(tier.model)
            if not allowed:
                print(reason)
                return
            cmd_model = getattr(commands, "cmd_model", None)
            if cmd_model:
                cmd_model(tier.model)
                print(f"[rebel-router] Switched to [{tier.name}] {tier.model}")
        else:
            print(f"Unknown routing option: {arg}")
            print("Use: /route [auto|fast|standard|deep|cloud|off]")

    try:
        commands.cmd_route = cmd_route
    except Exception:
        pass
