"""
Jarvis v3 Brain Router
7-tier routing with embedding-based intent classification.
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import chromadb
import yaml

from models.tier import BrainTier

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", "/home/kali/.jarvis"))
CONFIG_FILE = JARVIS_HOME / "config" / "jarvis_v3.yaml"
ROUTING_LOG = JARVIS_HOME / "data" / "routing_decisions_v3.jsonl"
log = logging.getLogger("jarvis.brain_router")


class BrainRouter:
    """
    Routes user requests to the appropriate AI tier.
    Uses keyword + embedding-based classification.
    """

    def __init__(self):
        self._load_config()
        self._init_chroma()

    def _load_config(self):
        self.config = {}
        if CONFIG_FILE.exists():
            try:
                self.config = yaml.safe_load(CONFIG_FILE.read_text()) or {}
            except Exception as exc:
                log.warning("Could not load v3 config: %s", exc)

    def _init_chroma(self):
        try:
            self._chroma = chromadb.PersistentClient(path=str(JARVIS_HOME / "memory"))
            self._collection = self._chroma.get_or_create_collection("jarvis_routing_intents")
            self._seed_intents()
        except Exception as exc:
            log.warning("ChromaDB routing index unavailable: %s", exc)
            self._collection = None

    def _seed_intents(self):
        """Seed the intent collection with routing examples."""
        existing = self._collection.get()
        if existing and len(existing["ids"]) > 0:
            return

        examples = [
            ("what is the cpu usage", BrainTier.EDGE),
            ("how much ram is free", BrainTier.EDGE),
            ("show disk space", BrainTier.EDGE),
            ("restart the nginx service", BrainTier.EDGE),
            ("fix the bug in PaymentGateway auth", BrainTier.CURSOR),
            ("refactor the user controller", BrainTier.CURSOR),
            ("review this pull request", BrainTier.CURSOR),
            ("deploy the starline frontend", BrainTier.CURSOR),
            ("why is the gateway failing intermittently", BrainTier.CLOUD),
            ("analyze the architecture tradeoffs", BrainTier.CLOUD),
            ("security audit my server", BrainTier.SWARM),
            ("is this ssh config secure", BrainTier.SWARM),
            ("build a new tool for docker log aggregation", BrainTier.KIMI),
            ("write a python script to monitor ports", BrainTier.KIMI),
            ("hello jarvis good morning", BrainTier.NANO),
            ("what time is it", BrainTier.NANO),
            ("ping the vps", BrainTier.EDGE),
            ("scan for open ports on localhost", BrainTier.EDGE),
        ]
        ids = []
        docs = []
        metas = []
        for i, (text, tier) in enumerate(examples):
            ids.append(f"intent_{i}")
            docs.append(text)
            metas.append({"tier": tier.value})
        self._collection.add(ids=ids, documents=docs, metadatas=metas)

    def classify(self, query: str) -> Tuple[BrainTier, float]:
        """
        Classify query into a tier. Returns (tier, confidence).
        Confidence is 0.0-1.0 based on embedding distance.
        """
        ql = query.lower().strip()

        # 1. Explicit prefix overrides
        if ql.startswith(("!nano", "!fast", "!local")):
            return BrainTier.NANO, 1.0
        if ql.startswith(("!edge", "!quick")):
            return BrainTier.EDGE, 1.0
        if ql.startswith(("!cursor", "!code")):
            return BrainTier.CURSOR, 1.0
        if ql.startswith(("!hybrid")):
            return BrainTier.HYBRID, 1.0
        if ql.startswith(("!kimi", "!build")):
            return BrainTier.KIMI, 1.0
        if ql.startswith(("!cloud", "!claude", "!deep")):
            return BrainTier.CLOUD, 1.0
        if ql.startswith(("!swarm", "!audit", "!security")):
            return BrainTier.SWARM, 1.0

        # 2. Security-critical fast-path → SWARM
        security_patterns = [
            r"\bsecurity audit\b", r"\bpenetration test\b", r"\brootkit\b",
            r"\bbackdoor\b", r"\bintrusion\b", r"\bcompliance\b",
            r"\bshould i (allow|permit|grant)\b",
        ]
        if any(re.search(p, ql) for p in security_patterns):
            return BrainTier.SWARM, 0.95

        # 3. Tool building → KIMI
        build_patterns = [
            r"\bbuild a tool\b", r"\bcreate a (script|tool|bot)\b",
            r"\bwrite (python|bash|js) (for|that|to)\b", r"\bgenerate code\b",
            r"\bscaffold\b", r"\bimplement a\b",
        ]
        if any(re.search(p, ql) for p in build_patterns):
            return BrainTier.KIMI, 0.9

        # 4. Cursor code patterns
        code_patterns = [
            r"\b(fix|refactor|debug|review|optimize|lint|test|deploy|build)\b.*\b(code|file|function|class|component|module|api|endpoint)\b",
            r"\b(git (pull|push|commit|merge|rebase))\b",
            r"\b(docker (build|run|compose))\b",
            r"\b(pnpm|npm|pip|pytest|eslint)\b",
            r"\b(Payment-Gateway|Starline|AsthaCash|conztru)\b",
        ]
        if any(re.search(p, ql) for p in code_patterns):
            return BrainTier.CURSOR, 0.85

        # 5. Cloud reasoning patterns
        reasoning_patterns = [
            r"\b(why|how (does|do|come|would|should)|analyze|analyse|explain|compare|evaluate|assess|diagnose)\b",
            r"\b(root cause|architecture|trade-?offs?|best practice|should i)\b",
            r"\b(entire codebase|full project|system design)\b",
        ]
        if any(re.search(p, ql) for p in reasoning_patterns):
            return BrainTier.CLOUD, 0.85

        # 6. Edge sysinfo patterns
        sysinfo_patterns = [
            r"\b(cpu|ram|memory|disk|gpu|vram|uptime|temp|temperature|load|process|service)\b",
            r"\b(check|show|what|how much|how many|status|usage|free|list)\b",
            r"\b(restart|stop|start)\b.*\b(service|jarvis|nginx|ollama)\b",
            r"\b(ping|traceroute|netstat|ss|nmap|curl)\b",
        ]
        if any(re.search(p, ql) for p in sysinfo_patterns):
            return BrainTier.EDGE, 0.8

        # 7. Nano trivial patterns
        nano_patterns = [
            r"^\s*(hi|hey|hello|yo|sup|howdy)\b",
            r"\b(good (morning|afternoon|evening|night))\b",
            r"\b(how are you|what time|what's up|ping)\b",
            r"\b(thank you|thanks|ty)\b",
        ]
        if any(re.search(p, ql) for p in nano_patterns):
            return BrainTier.NANO, 0.9

        # 8. Embedding fallback
        if self._collection is not None:
            try:
                results = self._collection.query(
                    query_texts=[query],
                    n_results=3,
                    include=["metadatas", "distances"],
                )
                if results["distances"][0]:
                    best_dist = results["distances"][0][0]
                    best_tier = results["metadatas"][0][0]["tier"]
                    confidence = max(0.0, 1.0 - best_dist)
                    if confidence > 0.5:
                        return BrainTier(best_tier), confidence
            except Exception as exc:
                log.debug("Embedding classification failed: %s", exc)

        # 9. Default
        return BrainTier.HYBRID, 0.5

    def route(self, query: str, history: Optional[List[dict]] = None) -> BrainTier:
        """Determine the best tier for a query."""
        tier, confidence = self.classify(query)

        # History depth → escalate to cloud
        if history and len(history) > 20 and tier in (BrainTier.EDGE, BrainTier.NANO):
            tier = BrainTier.CLOUD
            confidence = 0.7

        self._log_decision(query, tier, confidence)
        return tier

    def _log_decision(self, query: str, tier: BrainTier, confidence: float):
        entry = {
            "ts": time.time(),
            "tier": tier.value,
            "confidence": round(confidence, 3),
            "query_snippet": query[:80],
        }
        try:
            ROUTING_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(ROUTING_LOG, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass
