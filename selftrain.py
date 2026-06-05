#!/usr/bin/env python3
import os; os.environ.setdefault("PYTHONUNBUFFERED", "1")
"""
Jarvis Self-Training — weekly deep re-index with force=True on all sources.
Cron: 0 2 * * 0 (Sunday 2am)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".jarvis"))

from indexer import load_config, get_chroma_client, index_claude_sessions, index_cursor_sessions
from indexer import index_shell_history, index_git_repos, index_vps_logs, log, print_stats
import chromadb


def run_full_reindex():
    config = load_config()
    embed_model = config["memory"].get("embedding_model", "nomic-embed-text")
    client = get_chroma_client(config)
    collection = client.get_or_create_collection(
        name="jarvis_memory",
        metadata={"hnsw:space": "cosine"},
    )

    log("=== Weekly self-training: full force re-index ===")
    total = 0

    log("Force re-indexing Claude sessions...")
    total += index_claude_sessions(collection, config, embed_model)

    log("Force re-indexing Cursor sessions...")
    total += index_cursor_sessions(collection, config, embed_model)

    log("Force re-indexing shell history...")
    total += index_shell_history(collection, config, embed_model)

    log("Re-indexing git repos (incremental)...")
    total += index_git_repos(collection, config, embed_model)

    log("Re-indexing VPS logs...")
    total += index_vps_logs(collection, config, embed_model)

    log(f"Self-training complete. Upserted {total} chunks.")
    print_stats(collection)


if __name__ == "__main__":
    run_full_reindex()
