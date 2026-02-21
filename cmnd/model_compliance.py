# cmnd/model_compliance.py — DoD 1260H / BIS Entity List Model Compliance Enforcement
# Blocks PRC/Russia-origin models at startup and on /model switch.
# SECURITY CONTROL: SI-7 (Software Integrity) — Prevents use of restricted AI models
# SECURITY CONTROL: AC-2 (Account Management) — Model access control per entity list
# SECURITY CONTROL: PM-9 (Risk Management Strategy) — Operationalizes DoD 1260H compliance
# TRUST BOUNDARY: Model names from config/CLI treated as untrusted input — validated here
# DAIV CERTIFIED

import re
import sys

# ─────────────────────────────────────────────
# Restricted entity registry
# Source: model-compliance-mcp/seed/restricted-entities.json
# DoD 1260H, BIS Entity List, EAR Country Group D:5
# ─────────────────────────────────────────────

RESTRICTED_PATTERNS: list[tuple[str, str, str]] = [
    # (regex_pattern, entity_name, legal_basis)
    (r"qwen",        "Alibaba Cloud (Qwen)",       "DoD 1260H / BIS Entity List"),
    (r"deepseek",    "High-Flyer (DeepSeek)",      "DoD 1260H / BIS Entity List"),
    (r"\byi[-_]",    "01.AI (Yi)",                 "DoD 1260H / EAR D:5"),
    (r"\bglm[-_]",   "Zhipu AI (GLM)",             "DoD 1260H / BIS Entity List"),
    (r"chatglm",     "Zhipu AI (ChatGLM)",         "DoD 1260H / BIS Entity List"),
    (r"ernie",       "Baidu (ERNIE)",              "DoD 1260H / BIS Entity List"),
    (r"hunyuan",     "Tencent (Hunyuan)",          "DoD 1260H / EAR D:5"),
    (r"internlm",    "Shanghai AI Lab (InternLM)", "DoD 1260H / BIS Entity List"),
    (r"minicpm",     "OpenBMB/Tsinghua (MiniCPM)", "DoD 1260H / EAR D:5"),
    (r"kimi",        "Moonshot AI (Kimi)",         "DoD 1260H (Alibaba-backed)"),
    (r"minimax",     "MiniMax (SenseTime vets)",   "DoD 1260H / EAR D:5"),
    (r"gigachat",    "Sberbank (GigaChat)",         "EAR Country Group D:5 (Russia)"),
    (r"kandinsky",   "Sberbank (Kandinsky)",        "EAR Country Group D:5 (Russia)"),
    (r"yandexgpt",   "Yandex (YandexGPT)",         "EAR Country Group D:5 (Russia)"),
    (r"aya",         "Cohere Aya (verify origin)", "Manual review required"),
    (r"baichuan",    "Baichuan AI",                "DoD 1260H / BIS Entity List"),
    (r"sensechat",   "SenseTime (SenseChat)",      "DoD 1260H / BIS Entity List"),
    (r"tigerbot",    "TigerBot",                   "EAR D:5"),
    (r"moss[-_\d]",  "Fudan Univ (MOSS)",          "EAR D:5"),
    (r"belle",       "LianjiaTech (BELLE)",         "EAR D:5"),
]

# ─────────────────────────────────────────────
# Approved model families (for reference in errors)
# ─────────────────────────────────────────────

APPROVED_LOCAL = [
    "devstral",
    "mistral-small",
    "mistral-nemo",
    "llama3",
    "llama3.1",
    "llama3.2",
    "llama3.3",
    "gemma3",
    "phi4",
    "phi3",
    "mixtral",
    "codestral",
]

APPROVED_CLOUD = [
    "claude-opus-4",
    "claude-sonnet-4",
    "claude-haiku-4",
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-3.5-turbo",
    "gemini-2",
    "gemini-1.5",
]


def check_model(model_name: str) -> tuple[bool, str | None]:
    """
    Check if a model is allowed under DoD 1260H / BIS Entity List.

    Returns:
        (allowed: bool, reason: str | None) — reason is set if blocked.

    SECURITY CONTROL: SI-7 — Input normalized to lowercase before pattern match.
    TRUST BOUNDARY: model_name is untrusted CLI/config input.
    """
    if not model_name:
        return True, None

    # Normalize — strip provider prefix (e.g., "ollama_chat/devstral" → "devstral")
    normalized = model_name.lower()
    if "/" in normalized:
        normalized = normalized.split("/")[-1]
    # Strip version suffixes: "qwen2.5-7b" → still matches "qwen"
    normalized = re.sub(r"[:\s]", "-", normalized)

    for pattern, entity, basis in RESTRICTED_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return False, (
                f"🚫 BLOCKED: '{model_name}' matches restricted entity '{entity}'\n"
                f"   Legal basis: {basis}\n"
                f"   Policy: DoD 1260H / BIS Entity List / EAR Country Group D:5\n\n"
                f"   Approved local models: {', '.join(APPROVED_LOCAL[:5])}\n"
                f"   Approved cloud models: claude-sonnet-4-6, gpt-4o, gemini-2.0-flash"
            )

    return True, None


def enforce_model(model_name: str, exit_on_block: bool = True) -> bool:
    """
    Enforce compliance at startup or on model switch.
    Prints an error and optionally exits if blocked.

    SECURITY CONTROL: SI-7 — Hard enforcement prevents inadvertent use of restricted models.
    """
    allowed, reason = check_model(model_name)
    if not allowed:
        print("\n" + "=" * 60)
        print("  REBEL MODEL COMPLIANCE VIOLATION")
        print("=" * 60)
        print(reason)
        print("=" * 60 + "\n")

        if exit_on_block:
            print("Rebel cannot start with a restricted model. Exiting.")
            sys.exit(1)

    return allowed


def patch_model_switches(coder_class) -> None:
    """
    Monkey-patch the Coder's model switch logic to run compliance check.
    SECURITY CONTROL: SI-7 — Intercepts model changes at the coder level.
    """
    original_switch = getattr(coder_class, "cmd_model", None)
    if not original_switch:
        return

    def patched_cmd_model(self, args: str):
        model_name = args.strip()
        if model_name:
            allowed, reason = check_model(model_name)
            if not allowed:
                print("\n" + "=" * 60)
                print("  MODEL SWITCH BLOCKED — COMPLIANCE VIOLATION")
                print("=" * 60)
                print(reason)
                print("=" * 60 + "\n")
                return
        return original_switch(self, args)

    coder_class.cmd_model = patched_cmd_model
