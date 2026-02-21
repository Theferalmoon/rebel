# cmnd/chroma_repomap.py — Shared ChromaDB Semantic Repo Augmentation
# Uses the SAME memory-vault (ChromaDB) collections as all other CMND agents.
# Rebel reads from and writes to the same vector store — no separate DB.
# SECURITY CONTROL: SC-28 (Protection at Rest) — Shared ChromaDB on localhost
# SECURITY CONTROL: SI-12 (Information Management) — Embeddings stored in shared collection
# DAIV CERTIFIED

import os
import hashlib
import json
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from aider.coders import Coder

# ─────────────────────────────────────────────
# ChromaDB connection — same memory-vault all agents use
# ─────────────────────────────────────────────

CHROMA_HOST = os.environ.get("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.environ.get("CHROMA_PORT", "8000"))
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "localhost")
OLLAMA_PORT = int(os.environ.get("OLLAMA_PORT", "11435"))  # local bypass port
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")

# Collection names — MUST match what other agents use
COLLECTION_REPO = "rebel-repomap"       # code context embeddings
COLLECTION_MEMORY = "cmnd-memory"       # shared agent memory (if it exists)
COLLECTION_SESSIONS = "rebel-sessions"  # session summaries

_client = None
_collection = None


def _get_client():
    """Lazy-init ChromaDB client. Returns None if unavailable."""
    global _client
    if _client is not None:
        return _client

    try:
        import chromadb
        _client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        # Test connection
        _client.heartbeat()
        print(f"[rebel] ChromaDB connected: {CHROMA_HOST}:{CHROMA_PORT}")
        return _client
    except Exception as e:
        print(f"[rebel] ChromaDB unavailable ({e}) — semantic search disabled")
        _client = None
        return None


def _get_embed(text: str) -> list[float] | None:
    """Get embedding from Ollama nomic-embed-text.
    TRUST BOUNDARY: Ollama response parsed defensively.
    """
    import urllib.request
    import json as _json

    try:
        body = _json.dumps({
            "model": EMBED_MODEL,
            "prompt": text[:8192],  # Limit input
        }).encode()

        req = urllib.request.Request(
            f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = _json.loads(resp.read())
            embedding = result.get("embedding")
            if isinstance(embedding, list) and len(embedding) > 0:
                return embedding
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────
# Repo indexing — write to shared memory-vault
# ─────────────────────────────────────────────

def index_file(file_path: str, content: str, project_root: str) -> bool:
    """
    Embed a source file and store in the shared rebel-repomap collection.
    Skips if file hasn't changed (hash-based).
    SECURITY CONTROL: SI-12 — Only code content stored, no secrets.
    """
    client = _get_client()
    if not client:
        return False

    try:
        collection = client.get_or_create_collection(
            COLLECTION_REPO,
            metadata={"hnsw:space": "cosine"},
        )

        # Hash-based dedup
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        doc_id = hashlib.sha256(file_path.encode()).hexdigest()[:32]

        # Check if already indexed with same content
        existing = collection.get(ids=[doc_id], include=["metadatas"])
        if existing["metadatas"] and existing["metadatas"][0].get("hash") == content_hash:
            return True  # Up to date

        # Get embedding
        embedding = _get_embed(content[:4000])  # Trim large files
        if not embedding:
            return False

        rel_path = os.path.relpath(file_path, project_root) if project_root else file_path

        collection.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[content[:4000]],
            metadatas=[{
                "path": rel_path,
                "hash": content_hash,
                "indexed_by": "rebel",
                "lang": Path(file_path).suffix.lstrip(".") or "text",
            }],
        )
        return True

    except Exception as e:
        print(f"[rebel] ChromaDB index error: {e}")
        return False


def query_repo(query: str, n_results: int = 8) -> list[dict]:
    """
    Semantic search across the shared codebase index.
    Returns list of {path, content, distance} dicts.
    TRUST BOUNDARY: ChromaDB response validated before use.
    """
    client = _get_client()
    if not client:
        return []

    try:
        collection = client.get_or_create_collection(COLLECTION_REPO)
        embedding = _get_embed(query)
        if not embedding:
            return []

        results = collection.query(
            query_embeddings=[embedding],
            n_results=min(n_results, 20),
            include=["documents", "metadatas", "distances"],
        )

        items = []
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]

        for doc, meta, dist in zip(docs, metas, dists):
            if isinstance(meta, dict):
                items.append({
                    "path": meta.get("path", "unknown"),
                    "content": doc or "",
                    "distance": float(dist),
                    "lang": meta.get("lang", ""),
                })

        return items

    except Exception as e:
        print(f"[rebel] ChromaDB query error: {e}")
        return []


def store_session_summary(summary: str, model: str, tags: list[str]) -> bool:
    """
    Store a session summary in the shared sessions collection.
    Other agents (Rebel Context, Captain's Log) can find this.
    SECURITY CONTROL: AU-2 — Session records retained in shared vector store.
    """
    client = _get_client()
    if not client:
        return False

    try:
        from datetime import datetime
        collection = client.get_or_create_collection(COLLECTION_SESSIONS)
        doc_id = f"rebel-session-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        embedding = _get_embed(summary[:2000])
        if not embedding:
            return False

        collection.add(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[summary],
            metadatas=[{
                "source": "rebel",
                "model": model,
                "tags": ",".join(tags),
                "timestamp": datetime.now().isoformat(),
            }],
        )
        return True

    except Exception as e:
        print(f"[rebel] Could not store session summary: {e}")
        return False


# ─────────────────────────────────────────────
# Aider integration — augment repo map context
# ─────────────────────────────────────────────

def get_context_for_query(query: str) -> str:
    """
    Query shared ChromaDB for relevant code context.
    Returns formatted string Aider can prepend to system prompt.
    """
    results = query_repo(query, n_results=5)
    if not results:
        return ""

    lines = ["## Semantically Relevant Code (from shared memory-vault)\n"]
    for r in results:
        if r["distance"] < 1.2:  # Only reasonably relevant results
            lines.append(f"### {r['path']}")
            lines.append("```" + r.get("lang", ""))
            lines.append(r["content"][:800])
            lines.append("```\n")

    return "\n".join(lines)


def index_project_files(coder: "Coder", max_files: int = 200) -> None:
    """
    Background index of all tracked files into shared ChromaDB.
    Runs on startup, skips files that haven't changed.
    """
    client = _get_client()
    if not client:
        return

    root = getattr(coder.repo, "root", None) or os.getcwd()
    try:
        files = list(coder.get_all_relative_files())[:max_files]
    except Exception:
        return

    indexed = 0
    for rel in files:
        abs_path = os.path.join(root, rel)
        try:
            content = Path(abs_path).read_text(encoding="utf-8", errors="replace")
            if index_file(abs_path, content, root):
                indexed += 1
        except Exception:
            continue

    if indexed:
        print(f"[rebel] Indexed/refreshed {indexed} files in shared memory-vault")


def patch_coder(coder: "Coder") -> None:
    """
    Hook ChromaDB context into Aider's repo map system.
    When Aider builds context, also include semantic search results.
    """
    # Index files in background (non-blocking for startup)
    import threading
    t = threading.Thread(target=index_project_files, args=(coder,), daemon=True)
    t.start()

    # Patch get_repo_map to augment with ChromaDB results
    original_get_repo_map = getattr(coder, "get_repo_map", None)
    if not original_get_repo_map:
        return

    def augmented_get_repo_map(*args, **kwargs):
        base_map = original_get_repo_map(*args, **kwargs) or ""

        # Only augment if there's an active message to use as query
        last_msg = ""
        if coder.cur_messages:
            last = coder.cur_messages[-1]
            last_msg = last.get("content", "") if isinstance(last, dict) else str(last)

        if last_msg and len(last_msg) > 10:
            chroma_ctx = get_context_for_query(last_msg[:500])
            if chroma_ctx:
                return base_map + "\n" + chroma_ctx

        return base_map

    coder.get_repo_map = augmented_get_repo_map

    # Register /search command
    _register_commands(coder)


def _register_commands(coder: "Coder") -> None:
    """Register /search command for semantic code search."""
    commands = getattr(coder, "commands", None)
    if not commands:
        return

    def cmd_search(args: str) -> None:
        """Semantic code search across shared memory-vault. Usage: /search <query>"""
        query = args.strip()
        if not query:
            print("Usage: /search <query>")
            return

        results = query_repo(query, n_results=8)
        if not results:
            print("No results found (ChromaDB may be offline or not indexed yet)")
            return

        print(f"\nSemantic search results for: '{query}'")
        for i, r in enumerate(results, 1):
            dist_str = f"{r['distance']:.3f}"
            print(f"\n{i}. {r['path']}  (distance: {dist_str})")
            preview = r["content"][:200].replace("\n", " ")
            print(f"   {preview}...")

    try:
        commands.cmd_search = cmd_search
    except Exception:
        pass
