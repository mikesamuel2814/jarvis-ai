"""
JARVIS Memory Store
ChromaDB vector store + SQLite structured store.
Integrates with v3 memory/ and data/ ecosystem.
"""
import os
import sqlite3
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime

JARVIS_DIR = Path("/home/kali/.jarvis")
MEMORY_DB_DIR = JARVIS_DIR / "memory_db"
SQLITE_PATH = JARVIS_DIR / "data" / "memory.sqlite"


@dataclass
class MemoryRecord:
    id: str
    content: str
    source: str
    category: str
    timestamp: str
    embedding_id: Optional[str] = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class ChromaStore:
    """Vector memory using ChromaDB."""

    def __init__(self, collection_name: str = "jarvis_memory"):
        self.collection_name = collection_name
        self.client = None
        self.collection = None
        self._init_chroma()

    def _init_chroma(self):
        try:
            import chromadb
            self.client = chromadb.PersistentClient(path=str(MEMORY_DB_DIR))
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
        except Exception as e:
            print(f"ChromaDB init warning: {e}")

    def add(self, texts: List[str], ids: List[str], metadatas: List[Dict] = None):
        if self.collection is None:
            return
        self.collection.add(documents=texts, ids=ids, metadatas=metadatas)

    def query(self, query_text: str, n_results: int = 5) -> List[Dict]:
        if self.collection is None:
            return []
        results = self.collection.query(query_texts=[query_text], n_results=n_results)
        return results

    def delete(self, ids: List[str]):
        if self.collection is None:
            return
        self.collection.delete(ids=ids)


class SQLiteStore:
    """Structured memory using SQLite."""

    def __init__(self, db_path: Path = SQLITE_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    source TEXT,
                    category TEXT,
                    timestamp TEXT,
                    embedding_id TEXT,
                    metadata TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    role TEXT,
                    content TEXT,
                    timestamp TEXT,
                    metadata TEXT
                )
            """)
            conn.commit()

    def insert_memory(self, record: MemoryRecord):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO memories
                   (id, content, source, category, timestamp, embedding_id, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id, record.content, record.source, record.category,
                    record.timestamp, record.embedding_id,
                    json.dumps(record.metadata) if record.metadata else "{}"
                )
            )
            conn.commit()

    def get_memories(self, category: str = None, limit: int = 100) -> List[MemoryRecord]:
        with sqlite3.connect(self.db_path) as conn:
            if category:
                rows = conn.execute(
                    "SELECT * FROM memories WHERE category = ? ORDER BY timestamp DESC LIMIT ?",
                    (category, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM memories ORDER BY timestamp DESC LIMIT ?",
                    (limit,)
                ).fetchall()

        records = []
        for row in rows:
            records.append(MemoryRecord(
                id=row[0], content=row[1], source=row[2], category=row[3],
                timestamp=row[4], embedding_id=row[5],
                metadata=json.loads(row[6]) if row[6] else {}
            ))
        return records

    def log_interaction(self, session_id: str, role: str, content: str, metadata: Dict = None):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO interactions (session_id, role, content, timestamp, metadata)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, role, content, datetime.utcnow().isoformat(),
                 json.dumps(metadata) if metadata else "{}")
            )
            conn.commit()

    def get_session_history(self, session_id: str, limit: int = 50) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT role, content, timestamp, metadata FROM interactions WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit)
            ).fetchall()
        return [
            {"role": r[0], "content": r[1], "timestamp": r[2], "metadata": json.loads(r[3])}
            for r in reversed(rows)
        ]


class UnifiedMemoryStore:
    """Combined vector + structured memory."""

    def __init__(self):
        self.vector = ChromaStore()
        self.structured = SQLiteStore()

    def store(self, content: str, source: str, category: str, metadata: Dict = None) -> str:
        record_id = f"{source}_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
        record = MemoryRecord(
            id=record_id,
            content=content,
            source=source,
            category=category,
            timestamp=datetime.utcnow().isoformat(),
            metadata=metadata or {}
        )
        self.structured.insert_memory(record)
        self.vector.add(
            texts=[content],
            ids=[record_id],
            metadatas=[{"source": source, "category": category}]
        )
        return record_id

    def retrieve(self, query: str, category: str = None, n_results: int = 5) -> List[Dict]:
        # Vector search
        vector_results = self.vector.query(query, n_results=n_results)
        # Structured fallback
        structured_results = self.structured.get_memories(category=category, limit=n_results)
        return {
            "vector": vector_results,
            "structured": [asdict(r) for r in structured_results]
        }
