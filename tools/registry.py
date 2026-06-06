"""
Jarvis v3 Tool Registry
Semantic search over tools using ChromaDB embeddings.
"""

import os
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional

import chromadb

from .decorator import get_all_metadata, get_tool_func, get_tool_metadata
from .result import ToolResult

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", "/home/kali/.jarvis"))
log = logging.getLogger("jarvis.tools.registry")


class ToolRegistry:
    """
    Maintains an in-memory catalog + ChromaDB-backed semantic index of all tools.
    Enables the brain router to find relevant tools by natural language query.
    """

    def __init__(self, chroma_path: Optional[Path] = None):
        self.chroma_path = chroma_path or (JARVIS_HOME / "memory")
        self._client: Optional[chromadb.PersistentClient] = None
        self._collection = None
        self._tools: Dict[str, dict] = {}

    def _ensure_chroma(self):
        if self._client is None:
            self._client = chromadb.PersistentClient(path=str(self.chroma_path))
            self._collection = self._client.get_or_create_collection("jarvis_tools_v3")

    def index_all(self):
        """Index all currently registered tools into ChromaDB."""
        self._ensure_chroma()
        all_meta = get_all_metadata()
        if not all_meta:
            log.warning("No tools registered yet.")
            return

        ids = []
        documents = []
        metadatas = []
        for name, meta in all_meta.items():
            doc = f"{name}: {meta['description']}. Category: {meta['category']}. Tags: {', '.join(meta.get('tags', []))}."
            ids.append(name)
            documents.append(doc)
            metadatas.append({
                "name": name,
                "category": meta["category"],
                "rank": meta["rank"],
                "scope": meta["scope"],
            })

        # Clear and re-index
        existing = self._collection.get()
        if existing and existing["ids"]:
            self._collection.delete(ids=existing["ids"])
        self._collection.add(ids=ids, documents=documents, metadatas=metadatas)
        log.info("Indexed %d tools into ChromaDB", len(ids))

    def find_tools(self, query: str, n: int = 10, category: Optional[str] = None) -> List[dict]:
        """Semantic search for tools matching a natural language query."""
        self._ensure_chroma()
        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=n,
                where={"category": category} if category else None,
                include=["metadatas", "distances"],
            )
            tools = []
            for meta, dist in zip(results["metadatas"][0], results["distances"][0]):
                full = get_tool_metadata(meta["name"])
                if full:
                    full["distance"] = dist
                    tools.append(full)
            return tools
        except Exception as exc:
            log.warning("Semantic search failed: %s", exc)
            # Fallback: keyword search
            return self._keyword_search(query, n, category)

    def _keyword_search(self, query: str, n: int, category: Optional[str] = None) -> List[dict]:
        q = query.lower()
        scores = []
        for name, meta in get_all_metadata().items():
            if category and meta.get("category") != category:
                continue
            score = 0
            if q in name.lower():
                score += 10
            if q in meta.get("description", "").lower():
                score += 5
            for tag in meta.get("tags", []):
                if q in tag.lower():
                    score += 3
            if score > 0:
                scores.append((score, meta))
        scores.sort(key=lambda x: -x[0])
        return [m for _, m in scores[:n]]

    def get(self, name: str) -> Optional[dict]:
        return get_tool_metadata(name)

    def execute(self, name: str, **kwargs) -> ToolResult:
        """Execute a tool by name with given kwargs."""
        func = get_tool_func(name)
        if func is None:
            return ToolResult.fail(f"Tool '{name}' not found.", tool_name=name)
        meta = get_tool_metadata(name)
        import time
        t0 = time.time()
        try:
            result = func(**kwargs)
            elapsed = (time.time() - t0) * 1000
            if isinstance(result, ToolResult):
                result.duration_ms = elapsed
                result.tool_name = name
                return result
            # If function returns plain string, wrap it
            if isinstance(result, str):
                return ToolResult.ok(output=result, duration_ms=elapsed, tool_name=name)
            # If function returns dict, treat as data
            if isinstance(result, dict):
                return ToolResult.ok(data=result, duration_ms=elapsed, tool_name=name)
            return ToolResult.ok(output=str(result), duration_ms=elapsed, tool_name=name)
        except Exception as exc:
            elapsed = (time.time() - t0) * 1000
            return ToolResult.fail(error=str(exc), duration_ms=elapsed, tool_name=name)

    def list_categories(self) -> List[str]:
        cats = set()
        for meta in get_all_metadata().values():
            cats.add(meta.get("category", "general"))
        return sorted(cats)

    def list_tools(self, category: Optional[str] = None) -> List[str]:
        names = []
        for name, meta in get_all_metadata().items():
            if category is None or meta.get("category") == category:
                names.append(name)
        return sorted(names)
