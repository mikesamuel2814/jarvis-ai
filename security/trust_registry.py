"""
Jarvis v3 Trust Registry
Progressive trust with ChromaDB semantic similarity + JSON backup + Redis cache.
"""

import os
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", "/home/kali/.jarvis"))
TRUST_PATH = JARVIS_HOME / "data" / "trust_registry.json"


class TrustRegistry:
    """
    Stores "Allow & Save" decisions for tool execution.
    Patterns can be widened or narrowed via /trust commands.
    """

    def __init__(self):
        self.entries: List[dict] = []
        if TRUST_PATH.exists():
            with open(TRUST_PATH, "r") as fh:
                self.entries = json.load(fh)

    def save(self):
        with open(TRUST_PATH, "w") as fh:
            json.dump(self.entries, fh, indent=2)

    def add_trust(
        self,
        tool_name: str,
        params: dict,
        rank: str,
        expiry_days: Optional[int] = None,
        notes: str = "",
    ) -> str:
        trust_id = hashlib.sha256(
            f"{tool_name}:{json.dumps(params, sort_keys=True)}:{datetime.now(timezone.utc).isoformat()}".encode()
        ).hexdigest()[:16]
        entry = {
            "id": trust_id,
            "tool": tool_name,
            "params": params,
            "rank": rank,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=expiry_days or 365)).isoformat(),
            "notes": notes,
        }
        self.entries.append(entry)
        self.save()
        return trust_id

    def is_trusted(self, tool_name: str, params: dict) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        for e in self.entries:
            if e["tool"] != tool_name:
                continue
            if e["expires_at"] < now:
                continue
            # Simple param subset match
            if all(params.get(k) == v for k, v in e["params"].items()):
                return True
        return False

    def revoke(self, trust_id: str) -> bool:
        before = len(self.entries)
        self.entries = [e for e in self.entries if e["id"] != trust_id]
        self.save()
        return len(self.entries) < before

    def revoke_tool(self, tool_name: str) -> int:
        before = len(self.entries)
        self.entries = [e for e in self.entries if e["tool"] != tool_name]
        self.save()
        return before - len(self.entries)

    def list_trusts(self) -> List[dict]:
        now = datetime.now(timezone.utc).isoformat()
        return [e for e in self.entries if e["expires_at"] > now]

    def quick_trust_preset(self):
        """Bootstrap common safe workflows."""
        presets = [
            ("service_restart", {"service": "jarvis"}, "R3", 180),
            ("service_restart", {"service": "nginx"}, "R3", 180),
            ("service_restart", {"service": "ollama"}, "R3", 180),
            ("service_restart", {"service": "openclaw"}, "R3", 180),
            ("pm2_restart", {"name": "gateway"}, "R3", 180),
            ("pm2_restart", {"name": "starline"}, "R3", 180),
            ("git_commit", {"auto_push": False}, "R2", 90),
        ]
        for tool, params, rank, days in presets:
            self.add_trust(tool, params, rank, expiry_days=days, notes="quick_trust preset")
