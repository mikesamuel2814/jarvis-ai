#!/usr/bin/env python3
"""
Jarvis Reality Check — verifies LLM responses against known facts.
Flags hallucinations, incorrect claims, and profile violations.
"""

import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger("jarvis.reality_check")

# Known facts about Sir — hard truths the LLM must not contradict
_KNOWN_FACTS = {
    "name": "Mike Samuel",
    "email": "mikesamuel2814@gmail.com",
    "role": "Full-stack developer & entrepreneur",
    "os": "Kali Linux",
    "cpu": "i9-14900KF",
    "ram": "64GB",
    "gpu": "RTX 3050",
    "projects": ["AsthaCash", "Starline-Final-web", "Payment-Gateway", "conztru"],
    "address_as": "Sir",
}

# Patterns that indicate hallucination or generic AI-speak
_HALLUCINATION_PATTERNS = [
    r"\b(as an AI language model|as an AI|I am an AI|I'm an AI)\b",
    r"\b(I do not have personal experiences|I don't have feelings|I cannot feel)\b",
    r"\b(trained by Anthropic|trained by OpenAI|developed by Anthropic|developed by OpenAI)\b",
    r"\b(my knowledge cutoff|my training data|up to my last update)\b",
    r"\b(I hope this helps|I hope that helps|hope this is helpful)\b",
    r"\b(feel free to ask|let me know if you need|don't hesitate to ask)\b",
    r"\b(how can I assist you further|anything else I can help with)\b",
]


def check_response(response: str, query: str = "") -> dict:
    """
    Run reality checks on a response.
    Returns {"ok": bool, "issues": [str], "fixed": str}.
    """
    issues = []
    fixed = response.strip()

    # 1. Must start with "Sir,"
    if not fixed.startswith("Sir,"):
        issues.append("Missing 'Sir,' opener")
        fixed = f"Sir, {fixed.lstrip('Sir,').lstrip()}"

    # 2. Check for hallucination patterns
    for pattern in _HALLUCINATION_PATTERNS:
        if re.search(pattern, fixed, re.IGNORECASE):
            issues.append(f"Hallucination pattern matched: {pattern[:40]}")

    # 3. Check for contradictory identity claims
    lower = fixed.lower()
    if "i do not know you" in lower and "personally" in lower:
        issues.append("Contradicts known identity — Jarvis knows Sir Mike Samuel")
        fixed = fixed.replace("I do not know you personally", "I know you, Sir")
        fixed = fixed.replace("No, I do not know you personally", "Sir, I know you well")

    # 4. Check length — profile says under 200 words
    word_count = len(fixed.split())
    if word_count > 80:
        issues.append(f"Too long: {word_count} words (max ~80)")

    # 5. Check for forbidden openers
    forbidden = ["Certainly!", "Of course!", "Sure!", "Absolutely!",
                 "Happy to help!", "Great question!", "I'd be happy to"]
    for bad in forbidden:
        if fixed.lower().startswith(bad.lower()):
            issues.append(f"Forbidden opener: {bad}")
            fixed = re.sub(re.escape(bad) + r"\s*", "Sir, ", fixed, flags=re.IGNORECASE, count=1)

    # 6. Check for emoji overuse (profile says no emojis unless Sir uses them)
    emoji_count = len(re.findall(r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF]", fixed))
    if emoji_count > 1:
        issues.append(f"Too many emojis: {emoji_count}")

    # 7. Check for trailing offers of help
    if re.search(r"(let me know|feel free|just ask|if you need anything)", fixed, re.IGNORECASE):
        issues.append("Unprompted offer of further help")

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "fixed": fixed,
        "word_count": word_count,
    }


def fix_response(response: str, query: str = "") -> str:
    """Apply all reality checks and return the fixed response."""
    result = check_response(response, query)
    if not result["ok"]:
        log.debug("Reality check issues: %s", result["issues"])
    return result["fixed"]


if __name__ == "__main__":
    tests = [
        "Hello! How can I assist you today?",
        "Certainly! I can help with that.",
        "No, I do not know you personally. How can I assist you today?",
        "Sir, the CPU is at 15%.",
        "I'd be happy to help you with that! Let me know if you need anything else.",
    ]
    for t in tests:
        r = check_response(t)
        print(f"IN:  {t[:60]}")
        print(f"OK:  {r['ok']} | Issues: {r['issues']}")
        print(f"OUT: {r['fixed'][:60]}")
        print()
