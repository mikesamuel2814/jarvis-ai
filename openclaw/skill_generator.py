"""
Generate SKILL.md files for ClawHub from Jarvis patterns.
Writes skills to ~/.openclaw/workspace/skills/jarvis-system/
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("jarvis.openclaw.skills")

JARVIS_HOME   = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
OPENCLAW_WS   = Path(os.environ.get("OPENCLAW_WORKSPACE",
                                     Path.home() / ".openclaw" / "workspace"))
SKILLS_OUTPUT = JARVIS_HOME / "data" / "extracted_skills.jsonl"

JARVIS_SKILL_TEMPLATE = """---
name: {slug}
description: "{description}"
version: "1.0.0"
emoji: 🤖
os: ["linux"]
metadata:
  openclaw:
    requires:
      env:
        - JARVIS_API_KEY
        - JARVIS_API_URL
      bins:
        - curl
    primaryEnv: JARVIS_API_KEY
    envVars:
      JARVIS_API_KEY:
        description: "Jarvis local API authentication key"
        required: true
      JARVIS_API_URL:
        description: "Jarvis API base URL (default: http://127.0.0.1:8181)"
        required: false
---

# {name}

{description}

## Trigger Phrases

{trigger_phrases}

## What This Skill Does

{steps}

## Required Jarvis Actions

{required_tools}

## Usage

To invoke this skill, say one of the trigger phrases in your Jarvis Telegram chat.
Jarvis will route the request to the appropriate tier and return results.

## Notes

- This skill was auto-generated from a recurring pattern in Jarvis interactions.
- Requires Jarvis API running at `$JARVIS_API_URL` (default: http://127.0.0.1:8181).
- Authentication via `X-API-Key: $JARVIS_API_KEY` header.
"""


def generate_skill_files() -> int:
    """Convert extracted_skills.jsonl → SKILL.md files in OpenClaw workspace."""
    if not SKILLS_OUTPUT.exists():
        log.info("No extracted skills to generate.")
        return 0

    skills_base = OPENCLAW_WS / "skills" / "jarvis-system"
    skills_base.mkdir(parents=True, exist_ok=True)

    generated = 0
    for line in SKILLS_OUTPUT.read_text().splitlines():
        try:
            skill = json.loads(line)
            slug  = skill.get("name", "").replace(" ", "-").lower()[:40]
            if not slug:
                continue

            skill_dir = skills_base / slug
            skill_dir.mkdir(exist_ok=True)

            triggers = "\n".join(f"- {t}" for t in skill.get("trigger_phrases", []))
            steps    = "\n".join(f"{i+1}. {s}" for i, s in enumerate(skill.get("steps", [])))
            tools    = "\n".join(f"- `{t}`" for t in skill.get("required_tools", []))

            content = JARVIS_SKILL_TEMPLATE.format(
                slug=slug,
                name=skill.get("name", slug),
                description=skill.get("description", "Auto-generated Jarvis skill"),
                trigger_phrases=triggers or "- (see description)",
                steps=steps or "1. Route request to Jarvis API\n2. Return result to user",
                required_tools=tools or "- `/query` endpoint",
            )
            (skill_dir / "SKILL.md").write_text(content)
            generated += 1
        except Exception as e:
            log.error("Skill generation failed: %s", e)

    log.info("Generated %d SKILL.md files.", generated)
    return generated


def write_jarvis_system_skill() -> None:
    """Write the core jarvis-system SKILL.md that OpenClaw loads on startup."""
    skill_dir = OPENCLAW_WS / "skills" / "jarvis-system"
    skill_dir.mkdir(parents=True, exist_ok=True)

    content = '''---
name: jarvis-system
description: "Core Jarvis local system control — query AI brain, execute actions, check health"
version: "2.0.0"
emoji: 🤖
os: ["linux"]
metadata:
  openclaw:
    requires:
      env:
        - JARVIS_API_KEY
        - JARVIS_API_URL
      bins:
        - curl
    primaryEnv: JARVIS_API_KEY
    envVars:
      JARVIS_API_KEY:
        description: "Jarvis API authentication key"
        required: true
      JARVIS_API_URL:
        description: "Jarvis API URL"
        required: false
---

# Jarvis System Control

You have full access to the Jarvis 2.0 local AI system. Use these API endpoints:

## Query the Brain (3-tier routing)

```bash
curl -s -X POST "$JARVIS_API_URL/query" \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: $JARVIS_API_KEY" \\
  -d \'{"query": "USER_QUERY", "user_id": "telegram_user"}\' | jq .response
```

Force a tier: add `"force_tier": "edge"` | `"hybrid"` | `"cloud"` to the body.

## System Info (instant, no LLM)

```bash
curl -s "$JARVIS_API_URL/sysinfo" -H "X-API-Key: $JARVIS_API_KEY" | jq .
```

## Execute an Action

```bash
curl -s -X POST "$JARVIS_API_URL/action" \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: $JARVIS_API_KEY" \\
  -d \'{"action": "ACTION_NAME", "arg": "OPTIONAL_ARG"}\' | jq .
```

Available AUTO actions (no approval): ps, disk, memory, uptime, gpu, ports, logs_jarvis,
docker_ps, pm2_status, git_status_all, file_read, nginx_status, and more.

## Health Check

```bash
curl -s "$JARVIS_API_URL/health" | jq .
```

## Memory Stats

```bash
curl -s "$JARVIS_API_URL/stats" -H "X-API-Key: $JARVIS_API_KEY" | jq .
```

## Learning Stats

```bash
curl -s "$JARVIS_API_URL/learning-stats" -H "X-API-Key: $JARVIS_API_KEY" | jq .
```

## Sync Memory with OpenClaw

```bash
curl -s -X POST "$JARVIS_API_URL/memory/sync" \\
  -H "X-API-Key: $JARVIS_API_KEY" | jq .
```

## Send Feedback

```bash
# Thumbs up
curl -s -X POST "$JARVIS_API_URL/feedback" \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: $JARVIS_API_KEY" \\
  -d \'{"interaction_id": "ID", "rating": "thumbs_up"}\' | jq .

# Correction
curl -s -X POST "$JARVIS_API_URL/correct" \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: $JARVIS_API_KEY" \\
  -d \'{"interaction_id": "ID", "correction": "CORRECT_TEXT"}\' | jq .
```

## Notes

- JARVIS_API_URL defaults to http://127.0.0.1:8181 if not set
- All action endpoints require X-API-Key header
- Webhook endpoints require X-Openclaw-Signature HMAC header
- Actions with CONFIRM/APPROVE tier will return pending_approval status
'''
    (skill_dir / "SKILL.md").write_text(content)
    log.info("Wrote jarvis-system SKILL.md to %s", skill_dir)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    write_jarvis_system_skill()
    n = generate_skill_files()
    print(f"Generated {n} skill files.")
