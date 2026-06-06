"""
Kimi file manager — upload project directories for 256K context analysis.

Privacy enforced: never upload paths matching the privacy blocklist.
Files are automatically deleted after use to avoid Kimi storage costs.
"""

from __future__ import annotations

import os
import tarfile
import tempfile
from pathlib import Path

from kimi.client import KimiClient, _check_privacy

# Text extensions we include in project archives
TEXT_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
    ".yaml", ".yml", ".json", ".md", ".sh", ".sql",
    ".html", ".css", ".env.example", ".toml", ".txt",
}

# Always exclude
EXCLUDE_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".cache", "coverage",
}

MAX_ARCHIVE_BYTES = 40 * 1024 * 1024  # 40 MB safety limit


def _collect_files(root: Path, max_bytes: int = MAX_ARCHIVE_BYTES) -> list[Path]:
    """Collect text files under root, respecting size and exclusion rules."""
    files = []
    total = 0
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in TEXT_EXTS:
            if any(ex in p.parts for ex in EXCLUDE_DIRS):
                continue
            size = p.stat().st_size
            if total + size > max_bytes:
                break
            files.append(p)
            total += size
    return files


def upload_project(client: KimiClient, project_path: str | Path) -> str:
    """
    Pack a project directory into a .tar.gz, upload to Kimi, return file_id.
    Caller is responsible for calling delete_file(file_id) when done.

    Raises ValueError if project_path is in the privacy blocklist.
    """
    root = Path(project_path).expanduser().resolve()
    _check_privacy(str(root))

    if not root.exists():
        raise FileNotFoundError(f"Project path not found: {root}")

    files = _collect_files(root)
    if not files:
        raise ValueError(f"No text files found under {root}")

    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        with tarfile.open(tmp_path, "w:gz") as tar:
            for f in files:
                arcname = str(f.relative_to(root))
                tar.add(f, arcname=arcname)

        file_id = client.upload_file(tmp_path, purpose="assistants")
    finally:
        tmp_path.unlink(missing_ok=True)

    return file_id
