# cmnd — CMND Center extensions for Rebel (Aider fork)
# SECURITY CONTROL: SC-7 (Boundary Protection) — Extension layer isolated from aider core
# SECURITY CONTROL: PM-11 (Mission/Business Process Definition) — CMND Center mission alignment
# DAIV CERTIFIED

__version__ = "1.0.0"
__author__ = "CMND Center"

# Extensions loaded by rebel_main.py
MODULES = [
    "cmnd.model_compliance",   # Block DoD 1260H restricted models — loads first
    "cmnd.context_bridge",     # Captain's Log startup/shutdown hooks
    "cmnd.plan_mode",          # Plan-before-edit approval gate
    "cmnd.bash_tools",         # Expanded pre-approved shell commands
    "cmnd.chroma_repomap",     # ChromaDB semantic repo augmentation
    "cmnd.dashboard",          # REST status API on port 3033
]
