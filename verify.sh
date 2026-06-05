#!/usr/bin/env python3
"""Phase 13 automated check — reads api_port from config/jarvis.yaml."""
import shutil
import subprocess
import sys
from pathlib import Path

import requests
import yaml

checks = []


def chk(name: str, ok: bool, note: str = "") -> None:
    checks.append((name, ok, note))
    icon = "✅" if ok else "❌"
    print(f"{icon} {name:<35} {note}")


def model_names() -> list[str]:
    import ollama

    listed = ollama.list()
    if hasattr(listed, "models"):
        return [getattr(m, "model", str(m)) for m in listed.models]
    if isinstance(listed, dict):
        return [m.get("name", m.get("model", "")) for m in listed.get("models", [])]
    return []


def main() -> int:
    cfg_path = Path.home() / ".jarvis/config/jarvis.yaml"
    cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
    port = cfg.get("interfaces", {}).get("api_port", 8181)

    print("=" * 65)
    print("  JARVIS COMPLETE SYSTEM CHECK")
    print("=" * 65)

    try:
        models = model_names()
        chk("Ollama service", True, f"{len(models)} models")
        joined = " ".join(models).lower()
        chk("deepseek-r1:7b", "deepseek" in joined, "")
        chk("mxbai-embed-large", "mxbai" in joined, "")
        chk("qwen2.5-coder:7b", "qwen" in joined, "ollama pull qwen2.5-coder:7b")
    except Exception as e:
        chk("Ollama", False, str(e)[:40])

    # Use Jarvis API (avoids chromadb import segfault in verify-only shells)
    try:
        r = requests.get(f"http://localhost:{port}/health", timeout=5)
        data = r.json() if r.ok else {}
        count = int(data.get("memory_chunks") or 0)
        mem_ok = data.get("memory") == "ok"
        chk("ChromaDB memory", mem_ok and count > 0, f"{count} chunks indexed")
        chk("Memory has data", count > 0, "run jindexnow if 0")
    except Exception as e:
        chk("ChromaDB", False, str(e)[:40])

    for f in (
        "indexer.py",
        "jarvis_cli.py",
        "api.py",
        "telegram_bot.py",
        "cursor_monitor.py",
        "user_facts.py",
    ):
        p = Path.home() / ".jarvis" / f
        chk(f"Script: {f}", p.exists() and p.stat().st_size > 100, "")

    try:
        r = requests.get(f"http://localhost:{port}/health", timeout=5)
        detail = r.json().get("status", "") if r.ok else r.status_code
        chk(f"Jarvis API :{port}", r.status_code == 200, str(detail))
    except Exception as e:
        chk(f"Jarvis API :{port}", False, str(e)[:40])

    for svc in ("ollama", "jarvis", "jarvis-telegram"):
        r = subprocess.run(
            ["systemctl", "is-active", svc],
            capture_output=True,
            text=True,
        )
        active = r.stdout.strip() == "active"
        chk(f"systemd: {svc}", active, r.stdout.strip())

    r = subprocess.run(
        ["zsh", "-c", "source ~/.zshrc && alias jarvis"],
        capture_output=True,
        text=True,
    )
    chk("jarvis alias", "jarvis_cli" in r.stdout, "")

    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    chk("Weekly cron job", "train.sh" in r.stdout, "Sundays 3am")
    chk("Healer cron (10min)", "healer.py" in r.stdout, "add: */10 * * * * .../healer.py")

    healer = Path.home() / ".jarvis" / "healer.py"
    chk("Script: healer.py", healer.exists() and healer.stat().st_size > 100, "")

    doc = Path.home() / "jarvis-complete-setup-v2.docx"
    chk("Setup guide on disk", doc.exists(), str(doc))

    if shutil.which("docker"):
        r = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
        )
        names = r.stdout or ""
        chk("Docker: jarvis-api", "jarvis-api" in names, "bash ~/.jarvis/docker/up.sh")
        chk("Docker: jarvis-telegram", "jarvis-telegram" in names, "")
    else:
        chk("Docker installed", False, "bash ~/.jarvis/docker/install-docker.sh")

    print("=" * 65)
    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    print(f"  RESULT: {passed}/{total} checks passed")
    if passed >= total - 2:
        print("  STATUS: JARVIS IS READY ✅")
    else:
        print("  STATUS: FIX FAILED CHECKS ABOVE ❌")
    print("=" * 65)
    return 0 if passed >= total - 2 else 1


if __name__ == "__main__":
    sys.exit(main())
