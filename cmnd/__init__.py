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
    "cmnd.shared_state",       # MEMORY.md + TASKS.md shared state
    "cmnd.hooks",              # Python hooks system (foundation for all extensions)
    "cmnd.plan_mode",          # Plan-before-edit approval gate
    "cmnd.bash_tools",         # Expanded pre-approved shell commands
    "cmnd.skills",             # Skills / macro system
    "cmnd.agent_mode",         # Autonomous multi-step agent mode
    "cmnd.router",             # Multi-model routing by task complexity
    "cmnd.analytics_bridge",   # Analytics → Captain's Log telemetry
    "cmnd.yolo",               # YOLO mode — high-risk expanded permissions
    "cmnd.chroma_repomap",     # ChromaDB semantic repo augmentation
    "cmnd.watch_mode",         # File annotation watcher (# REBEL: ...)
    "cmnd.mcp_server",         # Rebel as MCP server (port 3035)
    "cmnd.dashboard",          # REST status API on port 3033
]
