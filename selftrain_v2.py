#!/usr/bin/env python3
"""
Jarvis 2.0 — Weekly Deep Evolution
Runs every Sunday at 2 AM via cron.

Tasks:
  1. Full ChromaDB re-index
  2. Kimi-powered codebase review (privacy-safe projects only)
  3. Generate new OpenClaw skills
  4. Evolve routing rules from accuracy data
  5. Memory compression
  6. Rebuild jarvis-system SKILL.md
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("jarvis.selftrain_v2")

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
ROUTING_FILE = JARVIS_HOME / "config" / "routing_rules.yaml"

# Projects eligible for Kimi review (NO payment codebase)
REVIEWABLE_PROJECTS = [
    "Starline-Final-web",
]


def full_reindex() -> dict:
    log.info("Step 1: Full ChromaDB re-index...")
    result = subprocess.run(
        [sys.executable, str(JARVIS_HOME / "indexer.py"),
         "--force", "--sources=claude,cursor,git,shell"],
        capture_output=True, text=True, timeout=3600,
    )
    log.info("Re-index stdout: %s", result.stdout[-500:])
    if result.returncode != 0:
        log.error("Re-index failed: %s", result.stderr[-500:])
    return {"success": result.returncode == 0, "output": result.stdout[-300:]}


def kimi_codebase_review() -> dict:
    log.info("Step 2: Kimi codebase review (privacy-safe projects only)...")
    from kimi.client import get_client
    from kimi.file_manager import upload_project

    client  = get_client()
    results = {}

    for project in REVIEWABLE_PROJECTS:
        project_path = Path.home() / "Projects" / "kalimike" / project
        if not project_path.exists():
            log.warning("Project not found: %s", project_path)
            continue

        log.info("Reviewing: %s", project)
        file_id = None
        try:
            file_id = upload_project(client, project_path)
            review, reasoning = client.complete(
                messages=[{
                    "role": "user",
                    "content": (
                        f"Review the uploaded codebase for project '{project}'.\n"
                        f"File ID: {file_id}\n\n"
                        "Identify:\n"
                        "1. Security vulnerabilities\n"
                        "2. Performance bottlenecks\n"
                        "3. Refactoring opportunities\n"
                        "4. Missing tests\n"
                        "5. Dependency risks\n\n"
                        "Return structured JSON with an 'issues' array and 'summary' string."
                    ),
                }],
                thinking=True,
            )

            # Store review as high-priority lesson
            import chromadb
            mem_client = chromadb.PersistentClient(path=str(JARVIS_HOME / "memory"))
            col = mem_client.get_or_create_collection("jarvis_memory")
            col.add(
                documents=[f"Codebase review [{project}]: {review[:1500]}"],
                metadatas=[{
                    "source": "kimi_review",
                    "priority": 5,
                    "type": "codebase_review",
                    "project": project,
                    "ts": str(int(time.time())),
                    "oc_sync_status": "pending",
                }],
                ids=[f"review_{project}_{int(time.time())}"],
            )
            results[project] = {"status": "ok", "review_len": len(review)}
        except Exception as e:
            log.error("Review failed for %s: %s", project, e)
            results[project] = {"status": "error", "error": str(e)[:200]}
        finally:
            if file_id:
                try:
                    client.delete_file(file_id)
                except Exception:
                    pass

    return results


def generate_skills() -> int:
    log.info("Step 3: Generating OpenClaw skill files...")
    from openclaw.skill_generator import generate_skill_files, write_jarvis_system_skill
    write_jarvis_system_skill()
    return generate_skill_files()


def evolve_routing_rules() -> dict:
    log.info("Step 4: Evolving routing rules from accuracy data...")
    from learning.pattern_extractor import routing_accuracy

    accuracy = routing_accuracy()
    log.info("Routing accuracy data: %s", accuracy)

    # Read current rules
    try:
        import yaml
        rules = yaml.safe_load(ROUTING_FILE.read_text()) or {}
    except Exception:
        log.warning("Could not read routing_rules.yaml; skipping evolution.")
        return {"status": "skipped"}

    routing = rules.get("routing", {})
    edge_acc  = routing.get("accuracy", {})
    changed   = False

    total = accuracy.get("total", 0)
    if total < 50:
        log.info("Not enough routing decisions (%d) to evolve rules.", total)
        return {"status": "insufficient_data", "total": total}

    # Example adaptive rule: if most queries are going to Cloud, lower cloud threshold
    cloud_pct = accuracy.get("cloud", 0) / max(total, 1)
    if cloud_pct > 0.5:
        # Too many cloud calls — tighten the threshold
        log.info("Cloud usage %.0f%% > 50%% — tightening code_char_limit.", cloud_pct * 100)
        routing.setdefault("edge", {})["code_char_limit"] = 150
        changed = True

    if changed:
        import yaml
        ROUTING_FILE.write_text(yaml.dump(rules, allow_unicode=True))
        log.info("Routing rules updated.")

    return {"status": "updated" if changed else "no_change", "cloud_pct": round(cloud_pct, 2)}


def compress_memory() -> dict:
    log.info("Step 5: Compressing memory...")
    from memory.compressor import compress
    return compress()


def sync_openclaw() -> dict:
    log.info("Step 6: Syncing memory with OpenClaw...")
    from memory.sync_engine import full_sync
    return full_sync()


def main() -> None:
    parser = argparse.ArgumentParser(description="Jarvis 2.0 Weekly Evolution")
    parser.add_argument("--full",   action="store_true", help="Full evolution (default)")
    parser.add_argument("--weekly", action="store_true", help="Weekly summary only")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("JARVIS 2.0 — WEEKLY EVOLUTION STARTING")
    log.info("=" * 60)

    results = {}
    t0 = time.time()

    results["reindex"]  = full_reindex()
    results["review"]   = kimi_codebase_review()
    results["skills"]   = generate_skills()
    results["routing"]  = evolve_routing_rules()
    results["compress"] = compress_memory()
    results["sync"]     = sync_openclaw()

    elapsed = time.time() - t0
    log.info("Evolution complete in %.0f seconds.", elapsed)
    log.info("Results: %s", json.dumps(results, indent=2))

    # Send summary to Telegram
    try:
        from openclaw.bridge import send_via_openclaw
        review_status = results.get("review", {})
        compress_data = results.get("compress", {})
        msg = (
            "🧠 *Jarvis Weekly Evolution Complete*\n\n"
            f"✅ Re-indexed memory\n"
            f"📊 Compression: {compress_data.get('archived', 0)} archived, "
            f"{compress_data.get('duplicates_removed', 0)} dupes removed\n"
            f"🔧 Skills generated: {results.get('skills', 0)}\n"
            f"📡 Routing: {results.get('routing', {}).get('status', 'unknown')}\n"
            f"⏱ Elapsed: {int(elapsed)}s"
        )
        send_via_openclaw(msg)
    except Exception as e:
        log.error("Telegram summary failed: %s", e)


if __name__ == "__main__":
    main()
