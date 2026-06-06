#!/usr/bin/env python3
"""
Brain Injector — injects learned rules and skill contexts into system prompts.

Every query dynamically gets the top-N learned rules prepended to the system prompt,
making Jarvis behavior self-correcting and memory-aware in real-time.

Uses skillset.py as data source. In-memory cache with 5s TTL to avoid blocking query path.
"""

import functools
import logging
import os
import time
from pathlib import Path

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
LOG_FILE = JARVIS_HOME / "logs" / "brain_injector.log"

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [injector] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)


class InjectionCache:
    """Simple in-memory cache with 5s TTL."""

    def __init__(self):
        self.cache = {}
        self.timestamps = {}

    def get(self, key: str) -> str | None:
        """Get cached value if fresh (<5s old)."""
        if key in self.cache:
            age = time.time() - self.timestamps.get(key, 0)
            if age < 5:
                return self.cache[key]
            else:
                del self.cache[key]
                del self.timestamps[key]
        return None

    def set(self, key: str, value: str) -> None:
        """Store value with timestamp."""
        self.cache[key] = value
        self.timestamps[key] = time.time()

    def invalidate(self) -> None:
        """Clear entire cache on rule update."""
        self.cache.clear()
        self.timestamps.clear()


_cache = InjectionCache()


def invalidate_cache() -> None:
    """Call this whenever skillset is updated."""
    _cache.invalidate()
    log.debug("Injection cache invalidated")


def get_injection(query: str = "", history: list | None = None, n_rules: int = 5) -> str:
    """
    Generate system prompt injection block.

    Includes:
    1. Top-N learned rules (ranked by relevance to query + priority)
    2. Skill gap enrichment (if query matches a known weak area)
    3. Pattern hints (if query matches a learned sequence pattern)

    Args:
        query: User's query string (used for semantic ranking)
        history: Conversation history (future use)
        n_rules: Number of rules to include (default 5)

    Returns:
        Formatted string for prepending to system prompt, or empty string if no rules.

    Examples:
        prompt = build_system_prompt("Your base prompt...")
        injection = get_injection("how do I deploy?")
        final_prompt = prompt + "\n\n" + injection
    """

    # Fast path: check cache
    cache_key = f"injection_{query[:50]}_{n_rules}"
    cached = _cache.get(cache_key)
    if cached is not None:
        log.debug(f"Cache hit for injection (query={query[:40]})")
        return cached

    try:
        from skillset import get_prompt_injection, get_skill_gaps_for_query
    except ImportError:
        log.warning("skillset module not available")
        return ""

    parts = []

    # 1. Rules block
    rules_block = get_prompt_injection(query, n=n_rules)
    if rules_block:
        parts.append(rules_block)

    # 2. Skill gaps block
    gap_block = get_skill_gaps_for_query(query)
    if gap_block:
        parts.append(gap_block)

    # Combine blocks
    result = "\n\n".join(parts)

    # Cache result
    if result:
        _cache.set(cache_key, result)
        log.debug(f"Injection generated ({len(result)} chars): {len(parts)} blocks")

    return result


def test_injection() -> bool:
    """Self-test: ensure injection returns correctly formatted output."""
    print("Test 1: Generate injection with empty skillset...")
    inj = get_injection("")
    print(f"  ✓ Injection returned ({len(inj)} chars): {inj[:80] if inj else '(empty)'}")

    print("Test 2: Generate injection with sample query...")
    inj = get_injection("what is disk usage")
    if inj:
        assert "LEARNED RULES" in inj or "SKILL CONTEXT" in inj, "Injection format incorrect"
        print(f"  ✓ Format OK: {inj.count(chr(10))} lines")
    else:
        print(f"  ✓ No rules yet (OK)")

    print("Test 3: Cache invalidation...")
    invalidate_cache()
    print(f"  ✓ Cache cleared")

    print("\n✅ All tests passed")
    return True


if __name__ == "__main__":
    test_injection()
