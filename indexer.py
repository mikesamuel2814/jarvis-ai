#!/usr/bin/env python3
"""
Jarvis RAG Indexer — reads Claude sessions, Cursor sessions, git repos,
shell history, and project files, then stores embeddings in ChromaDB.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import chromadb
import ollama
import yaml

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
CONFIG_FILE = JARVIS_HOME / "config" / "jarvis.yaml"
LOG_FILE = JARVIS_HOME / "logs" / "indexer.log"


def load_config():
    if not CONFIG_FILE.exists():
        return {
            "memory": {
                "path": str(JARVIS_HOME / "memory"),
                "embedding_model": "nomic-embed-text",
            },
            "data_sources": {
                "claude_sessions": str(JARVIS_HOME / "data" / "claude"),
                "cursor_sessions": str(JARVIS_HOME / "data" / "cursor"),
                "git_repos": str(Path.home() / "Projects"),
                "shell_history": str(Path.home() / ".zsh_history"),
                "vps_logs": str(JARVIS_HOME / "data" / "deployments"),
            },
        }
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_chroma_client(config):
    memory_path = config["memory"]["path"]
    Path(memory_path).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=memory_path)


def get_embedding(text, model="nomic-embed-text"):
    try:
        response = ollama.embeddings(model=model, prompt=text[:4096])
        return response["embedding"]
    except Exception as e:
        log(f"Embedding error: {e}")
        return None


def chunk_text(text, chunk_size=800, overlap=100):
    text = text.strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


def doc_id(source, chunk_index, content):
    h = hashlib.md5(f"{source}:{chunk_index}:{content[:64]}".encode()).hexdigest()[:12]
    return f"{Path(source).stem}_{chunk_index}_{h}"


def already_indexed(collection, source_path):
    try:
        results = collection.get(where={"source": str(source_path)}, limit=1)
        return len(results["ids"]) > 0
    except Exception:
        return False


def index_text_file(collection, file_path, source_type, embed_model, force=False):
    file_path = Path(file_path)
    if not file_path.exists():
        return 0

    if not force and already_indexed(collection, str(file_path)):
        return 0

    try:
        text = file_path.read_text(errors="ignore")
    except Exception as e:
        log(f"  Read error {file_path}: {e}")
        return 0

    chunks = chunk_text(text)
    if not chunks:
        return 0

    added = 0
    for i, chunk in enumerate(chunks):
        emb = get_embedding(chunk, embed_model)
        if emb is None:
            continue
        try:
            collection.upsert(
                ids=[doc_id(str(file_path), i, chunk)],
                embeddings=[emb],
                documents=[chunk],
                metadatas=[{
                    "source": str(file_path),
                    "source_type": source_type,
                    "chunk_index": i,
                    "file_name": file_path.name,
                    "indexed_at": datetime.now().isoformat(),
                }],
            )
            added += 1
        except Exception as e:
            log(f"  Upsert error: {e}")

    return added


def index_claude_sessions(collection, config, embed_model):
    sessions_dir = Path(config["data_sources"].get("claude_sessions", ""))
    if not sessions_dir.exists():
        return 0
    total = 0
    for f in sorted(sessions_dir.glob("*.txt")):
        # Always force re-index sessions — new content may have been appended
        n = index_text_file(collection, f, "claude_session", embed_model, force=True)
        if n:
            log(f"  Indexed Claude session: {f.name} ({n} chunks)")
            total += n
    return total


def _extract_claude_code_text(jsonl_path):
    """Parse a Claude Code .jsonl session and return conversation text."""
    lines_text = []
    title = ""
    try:
        with open(jsonl_path, errors="ignore") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                t = obj.get("type", "")
                if t == "ai-title":
                    title = obj.get("aiTitle", "")
                    continue
                if t not in ("user", "assistant"):
                    continue
                msg = obj.get("message", {})
                role = msg.get("role", t)
                content = msg.get("content", "")
                parts = []
                if isinstance(content, str):
                    if content.strip():
                        parts.append(content.strip())
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        bt = block.get("type", "")
                        # Skip tool calls, results, and internal thinking
                        if bt in ("tool_use", "tool_result", "thinking"):
                            continue
                        if bt == "text":
                            txt = block.get("text", "").strip()
                            if txt:
                                parts.append(txt)
                if parts:
                    prefix = "User" if role == "user" else "Jarvis"
                    lines_text.append(f"{prefix}: {' '.join(parts)}")
    except Exception:
        return ""
    if not lines_text:
        return ""
    header = f"[Claude Code Session: {jsonl_path.stem}]\n"
    if title:
        header += f"Topic: {title}\n"
    return header + "\n".join(lines_text)


def index_claude_code_sessions(collection, config, embed_model):
    """Index actual Claude Code JSONL sessions from ~/.claude/projects/."""
    cc_dir = Path(config["data_sources"].get(
        "claude_code_sessions",
        Path.home() / ".claude" / "projects" / "-home-kali",
    ))
    if not cc_dir.exists():
        return 0

    total = 0
    for f in sorted(cc_dir.glob("*.jsonl")):
        # Skip tiny/empty files (< 2KB) — likely unstarted sessions
        if f.stat().st_size < 2048:
            continue
        # Use already_indexed to skip unchanged files (keyed by path)
        if already_indexed(collection, str(f)):
            continue
        text = _extract_claude_code_text(f)
        if not text.strip():
            continue
        chunks = chunk_text(text, chunk_size=500, overlap=60)
        added = 0
        for i, chunk in enumerate(chunks):
            emb = get_embedding(chunk, embed_model)
            if emb is None:
                continue
            try:
                collection.upsert(
                    ids=[doc_id(str(f), i, chunk)],
                    embeddings=[emb],
                    documents=[chunk],
                    metadatas=[{
                        "source": str(f),
                        "source_type": "claude_code_session",
                        "chunk_index": i,
                        "file_name": f.name,
                        "indexed_at": datetime.now().isoformat(),
                    }],
                )
                added += 1
            except Exception as e:
                log(f"  Upsert error {f.name}: {e}")
        if added:
            log(f"  Indexed Claude Code session: {f.name} ({added} chunks)")
            total += added
    return total


def index_cursor_sessions(collection, config, embed_model):
    cursor_dir = Path(config["data_sources"].get("cursor_sessions", ""))
    if not cursor_dir.exists():
        return 0
    total = 0
    for f in sorted(cursor_dir.glob("*.txt")) + sorted(cursor_dir.glob("*.json")):
        n = index_text_file(collection, f, "cursor_session", embed_model)
        if n:
            log(f"  Indexed Cursor session: {f.name} ({n} chunks)")
            total += n
    return total


def index_shell_history(collection, config, embed_model):
    history_file = Path(config["data_sources"].get("shell_history", ""))
    jarvis_shell_log = JARVIS_HOME / "data" / "shell" / "history.log"

    total = 0
    for hfile in [history_file, jarvis_shell_log]:
        if hfile.exists():
            # Always force re-index — shell history grows continuously
            n = index_text_file(collection, hfile, "shell_history", embed_model, force=True)
            if n:
                log(f"  Indexed shell history: {hfile.name} ({n} chunks)")
                total += n
    return total


def index_git_repos(collection, config, embed_model):
    repos_root = Path(config["data_sources"].get("git_repos", ""))
    if not repos_root.exists():
        return 0

    code_extensions = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs",
                       ".sh", ".bash", ".zsh", ".md", ".yaml", ".yml",
                       ".json", ".toml", ".env.example", ".txt", ".sql"}
    skip_dirs = {
        # version control
        ".git",
        # JS/TS build noise
        "node_modules", ".next", ".turbo", ".parcel-cache",
        "dist", "build", "out", "coverage", "storybook-static",
        # Python noise
        "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
        # compiled / generated
        "target",       # Rust/Java
        ".cache",
        # misc
        ".idea", ".vscode",
    }
    total = 0

    def _is_binary(path: Path) -> bool:
        """Quick binary-file sniff — read first 8 KB, look for null bytes."""
        try:
            chunk = path.read_bytes()[:8192]
            return b"\x00" in chunk
        except Exception:
            return True

    repos = [d for d in repos_root.iterdir() if d.is_dir()]
    for repo in sorted(repos):
        if repo.name in skip_dirs:
            continue
        repo_total = 0
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fname in files:
                fpath = Path(root) / fname
                if fpath.suffix.lower() not in code_extensions:
                    continue
                if fpath.stat().st_size > 200_000:
                    continue
                if _is_binary(fpath):
                    continue
                n = index_text_file(collection, fpath, "git_repo", embed_model)
                repo_total += n
        if repo_total:
            log(f"  Indexed repo {repo.name}: {repo_total} chunks")
            total += repo_total

    # Also index git commit logs
    for repo in sorted(repos):
        git_dir = repo / ".git"
        if not git_dir.exists():
            continue
        try:
            result = subprocess.run(
                ["git", "-C", str(repo), "log", "--oneline", "-200"],
                capture_output=True, text=True, timeout=10
            )
            if result.stdout.strip():
                log_text = f"Git log for {repo.name}:\n{result.stdout}"
                tmp_id = f"gitlog_{repo.name}"
                emb = get_embedding(log_text[:4096], embed_model)
                if emb:
                    collection.upsert(
                        ids=[tmp_id],
                        embeddings=[emb],
                        documents=[log_text[:4096]],
                        metadatas=[{
                            "source": str(repo / ".git/log"),
                            "source_type": "git_log",
                            "file_name": f"git_log_{repo.name}",
                            "indexed_at": datetime.now().isoformat(),
                        }],
                    )
                    total += 1
        except Exception:
            pass

    return total


def index_vps_logs(collection, config, embed_model):
    logs_dir = Path(config["data_sources"].get("vps_logs", ""))
    if not logs_dir.exists():
        return 0
    total = 0
    for f in sorted(logs_dir.glob("*")):
        if f.is_file() and f.stat().st_size < 500_000:
            n = index_text_file(collection, f, "vps_log", embed_model)
            if n:
                log(f"  Indexed VPS log: {f.name} ({n} chunks)")
                total += n
    return total


def print_stats(collection):
    try:
        count = collection.count()
        print(f"\n{'='*40}")
        print(f"  Jarvis Memory Stats")
        print(f"{'='*40}")
        print(f"  Total chunks in memory: {count}")
        if count > 0:
            sample = collection.get(limit=5, include=["metadatas"])
            types = {}
            for meta in sample["metadatas"]:
                t = meta.get("source_type", "unknown")
                types[t] = types.get(t, 0) + 1
            print(f"  Sample source types: {types}")
        print(f"{'='*40}\n")
    except Exception as e:
        print(f"Stats error: {e}")


def run_indexing(config):
    embed_model = config["memory"].get("embedding_model", "nomic-embed-text")
    client = get_chroma_client(config)
    collection = client.get_or_create_collection(
        name="jarvis_memory",
        metadata={"hnsw:space": "cosine"},
    )

    log("Starting Jarvis indexing...")
    total = 0

    log("Indexing Claude sessions...")
    total += index_claude_sessions(collection, config, embed_model)

    log("Indexing Claude Code sessions...")
    total += index_claude_code_sessions(collection, config, embed_model)

    log("Indexing Cursor sessions...")
    total += index_cursor_sessions(collection, config, embed_model)

    log("Indexing shell history...")
    total += index_shell_history(collection, config, embed_model)

    log("Indexing git repos...")
    total += index_git_repos(collection, config, embed_model)

    log("Indexing VPS logs...")
    total += index_vps_logs(collection, config, embed_model)

    log(f"Indexing complete. Added {total} new chunks.")
    return collection


def query_memory(collection, query_text, n_results=5, embed_model="nomic-embed-text"):
    emb = get_embedding(query_text, embed_model)
    if emb is None:
        return []
    try:
        results = collection.query(
            query_embeddings=[emb],
            n_results=min(n_results, collection.count()),
            include=["documents", "metadatas", "distances"],
        )
        if not results["ids"][0]:
            return []
        return [
            {
                "text": results["documents"][0][i],
                "source": results["metadatas"][0][i].get("source", ""),
                "source_type": results["metadatas"][0][i].get("source_type", ""),
                "distance": results["distances"][0][i],
            }
            for i in range(len(results["ids"][0]))
        ]
    except Exception as e:
        log(f"Query error: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(description="Jarvis RAG Indexer")
    parser.add_argument("--now", action="store_true", help="Run full indexing now")
    parser.add_argument("--stats", action="store_true", help="Show memory stats")
    parser.add_argument("--query", type=str, help="Test a query against memory")
    args = parser.parse_args()

    config = load_config()
    embed_model = config["memory"].get("embedding_model", "nomic-embed-text")
    client = get_chroma_client(config)
    collection = client.get_or_create_collection(
        name="jarvis_memory",
        metadata={"hnsw:space": "cosine"},
    )

    if args.stats:
        print_stats(collection)
    elif args.now:
        run_indexing(config)
        print_stats(collection)
    elif args.query:
        results = query_memory(collection, args.query, embed_model=embed_model)
        print(f"\nQuery: {args.query}")
        print(f"Top {len(results)} results:")
        for i, r in enumerate(results, 1):
            print(f"\n[{i}] Source: {r['source_type']} — {Path(r['source']).name}")
            print(f"     Distance: {r['distance']:.4f}")
            print(f"     Text: {r['text'][:200]}...")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
