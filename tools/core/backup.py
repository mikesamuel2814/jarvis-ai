"""
Jarvis v3 Backup & Recovery Tools (6 tools)
Category: backup | Rank: R2-R3 | Scope: READ/LOCAL
"""

import os
import subprocess
from pathlib import Path
from typing import Optional

from ..decorator import jarvis_tool
from ..result import ToolResult


@jarvis_tool(
    name="rsync_backup",
    description="Rsync backup from source to destination with optional exclude patterns",
    params={
        "source": {"type": "string", "required": True},
        "dest": {"type": "string", "required": True},
        "exclude": {"type": "string", "required": False, "default": ""},
    },
    rank="R2",
    scope="LOCAL",
    category="backup",
    tags=["rsync", "backup", "sync"],
)
def rsync_backup(source: str, dest: str, exclude: str = "") -> ToolResult:
    try:
        cmd = ["rsync", "-avh", "--progress", source, dest]
        if exclude:
            cmd.extend(["--exclude", exclude])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output, tool_name="rsync_backup")
        return ToolResult.ok(output=output, tool_name="rsync_backup")
    except FileNotFoundError:
        return ToolResult.fail(error="rsync not found", tool_name="rsync_backup")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="rsync_backup")


@jarvis_tool(
    name="tar_archive",
    description="Create a tar archive from a source path to an output archive file",
    params={
        "source": {"type": "string", "required": True},
        "output": {"type": "string", "required": True},
    },
    rank="R2",
    scope="LOCAL",
    category="backup",
    tags=["tar", "archive", "compression"],
)
def tar_archive(source: str, output: str) -> ToolResult:
    try:
        cmd = ["tar", "-czf", output, "-C", str(Path(source).parent), Path(source).name]
        # If source is a single file or dir with absolute path, better to pass it directly
        cmd = ["tar", "-czf", output, source]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        output_text = result.stdout
        if result.stderr:
            output_text += "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output_text, tool_name="tar_archive")
        return ToolResult.ok(output=f"Archive created: {output}\n{output_text}", tool_name="tar_archive")
    except FileNotFoundError:
        return ToolResult.fail(error="tar not found", tool_name="tar_archive")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="tar_archive")


@jarvis_tool(
    name="borg_backup",
    description="Create a Borg backup archive in a repository",
    params={
        "repo": {"type": "string", "required": True},
        "path": {"type": "string", "required": True},
        "archive_name": {"type": "string", "required": True},
    },
    rank="R2",
    scope="LOCAL",
    category="backup",
    tags=["borg", "backup", "deduplication"],
)
def borg_backup(repo: str, path: str, archive_name: str) -> ToolResult:
    try:
        archive_path = f"{repo}::{archive_name}"
        env = os.environ.copy()
        env["BORG_REPO"] = repo
        cmd = ["borg", "create", "--stats", archive_path, path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output, tool_name="borg_backup")
        return ToolResult.ok(output=output, tool_name="borg_backup")
    except FileNotFoundError:
        return ToolResult.fail(error="borg not found", tool_name="borg_backup")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="borg_backup")


@jarvis_tool(
    name="dd_clone",
    description="Clone a disk or partition with dd (source, destination, block size)",
    params={
        "source": {"type": "string", "required": True},
        "dest": {"type": "string", "required": True},
        "bs": {"type": "string", "required": False, "default": "4M"},
    },
    rank="R3",
    scope="PRIVILEGED",
    category="backup",
    tags=["dd", "clone", "disk", "forensics"],
)
def dd_clone(source: str, dest: str, bs: str = "4M") -> ToolResult:
    try:
        cmd = ["dd", f"if={source}", f"of={dest}", f"bs={bs}", "status=progress"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output, tool_name="dd_clone")
        return ToolResult.ok(output=output, tool_name="dd_clone")
    except FileNotFoundError:
        return ToolResult.fail(error="dd not found", tool_name="dd_clone")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="dd_clone")


@jarvis_tool(
    name="testdisk_recover",
    description="Run TestDisk partition recovery on a device",
    params={
        "device": {"type": "string", "required": True},
    },
    rank="R2",
    scope="LOCAL",
    category="backup",
    tags=["testdisk", "recovery", "partition"],
)
def testdisk_recover(device: str) -> ToolResult:
    try:
        cmd = ["testdisk", "/list", device]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output, tool_name="testdisk_recover")
        return ToolResult.ok(output=output, tool_name="testdisk_recover")
    except FileNotFoundError:
        return ToolResult.fail(error="testdisk not found", tool_name="testdisk_recover")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="testdisk_recover")


@jarvis_tool(
    name="photorec",
    description="Run PhotoRec file recovery from a source to an output directory",
    params={
        "source": {"type": "string", "required": True},
        "output_dir": {"type": "string", "required": True},
    },
    rank="R2",
    scope="LOCAL",
    category="backup",
    tags=["photorec", "recovery", "files", "forensics"],
)
def photorec(source: str, output_dir: str) -> ToolResult:
    try:
        # PhotoRec in batch mode
        cmd = ["photorec", "/d", output_dir, "/cmd", source, "search", "free", "quit"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output, tool_name="photorec")
        return ToolResult.ok(output=output, tool_name="photorec")
    except FileNotFoundError:
        return ToolResult.fail(error="photorec not found", tool_name="photorec")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="photorec")
