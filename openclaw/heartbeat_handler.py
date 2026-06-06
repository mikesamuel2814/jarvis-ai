"""
Process heartbeat events from OpenClaw gateway.

OpenClaw calls /webhook/openclaw with type="heartbeat" every 30 minutes.
This module handles the system-check tasks assigned to OpenClaw
(distinct from monitor.py which owns local hardware checks).

OpenClaw owns: git status checks, VPS PM2, learning queue depth, skill sync.
monitor.py owns: CPU/RAM/GPU/disk/service health.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger("jarvis.openclaw.heartbeat")

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
VPS_HOST    = os.environ.get("JARVIS_VPS_HOST", "38.47.35.16")
QUEUE       = JARVIS_HOME / "data" / "learning_queue.jsonl"
GOLDEN      = JARVIS_HOME / "data" / "golden_examples.jsonl"


def run_heartbeat_checks() -> dict:
    """
    Run all OpenClaw-owned heartbeat checks.
    Returns a status dict for the /webhook/openclaw response.
    """
    results: dict = {
        "ts": int(time.time()),
        "checks": {},
        "alerts": [],
    }

    results["checks"]["learning_queue"] = _check_learning_queue()
    results["checks"]["git_status"]     = _check_git_repos()
    results["checks"]["vps_pm2"]        = _check_vps_pm2()
    results["checks"]["skill_sync"]     = _sync_skills()

    return results


def _check_learning_queue() -> dict:
    """Alert if learning queue has > 10 pending items."""
    if not QUEUE.exists():
        return {"status": "empty", "count": 0}
    count = sum(1 for line in QUEUE.read_text().splitlines() if line.strip())
    status = "ok" if count < 10 else "trigger_training"
    if status == "trigger_training":
        log.warning("Learning queue has %d items — triggering training.", count)
        _trigger_training()
    return {"status": status, "count": count}


def _check_git_repos() -> dict:
    """Check for uncommitted changes in Projects > 3 hours old."""
    projects_root = Path.home() / "Projects"
    dirty = []
    if not projects_root.exists():
        return {"status": "no_projects"}
    for repo_dir in projects_root.rglob(".git"):
        repo = repo_dir.parent
        try:
            result = subprocess.run(
                ["git", "-C", str(repo), "status", "--porcelain"],
                capture_output=True, text=True, timeout=10,
            )
            if result.stdout.strip():
                # Check age of oldest modified file
                stat_result = subprocess.run(
                    ["git", "-C", str(repo), "diff", "--name-only"],
                    capture_output=True, text=True, timeout=10,
                )
                dirty.append(repo.name)
        except Exception as e:
            log.error("Git check failed for %s: %s", repo, e)
    return {"status": "dirty" if dirty else "clean", "dirty_repos": dirty}


def _check_vps_pm2() -> dict:
    """SSH to VPS and check PM2 process status."""
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
             f"kali@{VPS_HOST}", "pm2 jlist"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {"status": "ssh_failed", "error": result.stderr[:200]}
        processes = json.loads(result.stdout)
        stopped = [p["name"] for p in processes if p.get("pm2_env", {}).get("status") != "online"]
        if stopped:
            log.warning("VPS PM2 stopped processes: %s", stopped)
        return {"status": "ok" if not stopped else "stopped_processes", "stopped": stopped}
    except Exception as e:
        log.error("VPS PM2 check failed: %s", e)
        return {"status": "error", "error": str(e)[:200]}


def _sync_skills() -> dict:
    """Push pending ChromaDB chunks to OpenClaw workspace."""
    try:
        from openclaw.bridge import push_memory_to_openclaw, pull_skills_from_openclaw
        pushed = push_memory_to_openclaw(limit=50)
        pulled = pull_skills_from_openclaw()
        return {"status": "ok", "pushed": pushed, "pulled": pulled}
    except Exception as e:
        log.error("Skill sync failed: %s", e)
        return {"status": "error", "error": str(e)[:200]}


def _trigger_training() -> None:
    """Trigger the 6-hour training pipeline immediately."""
    try:
        subprocess.Popen(
            ["bash", str(JARVIS_HOME / "train_v2.sh")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        log.info("Training pipeline triggered.")
    except Exception as e:
        log.error("Failed to trigger training: %s", e)
