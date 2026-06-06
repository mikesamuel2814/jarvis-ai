"""
Jarvis v3 File Tools (12 tools)
Category: file | Ranks: R0/R2 | Scope: READ/LOCAL
"""

import hashlib
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Optional

from ..decorator import jarvis_tool
from ..result import ToolResult

_ALLOWED_ROOTS = [Path("/home/kali").resolve(), Path("/tmp").resolve()]


def _validate_path(path_str: str, must_exist: bool = False, allow_dir: bool = True) -> Path:
    p = Path(path_str).expanduser()
    try:
        p = p.resolve()
    except (FileNotFoundError, OSError):
        p = p.absolute()
    for base in _ALLOWED_ROOTS:
        try:
            p.relative_to(base)
            break
        except ValueError:
            continue
    else:
        raise PermissionError(f"Path {p} is outside allowed directories (/home/kali, /tmp)")
    if must_exist and not p.exists():
        raise FileNotFoundError(f"Path does not exist: {p}")
    if not allow_dir and p.is_dir():
        raise IsADirectoryError(f"Expected a file, got directory: {p}")
    return p


@jarvis_tool(
    name="ls_dir",
    description="List directory contents",
    params={"path": {"type": "string", "required": False, "default": "."}},
    rank="R0", scope="READ", category="file", tags=["file", "list", "directory"]
)
def ls_dir(path: str = ".") -> ToolResult:
    try:
        target = _validate_path(path, must_exist=True, allow_dir=True)
        entries = []
        for entry in target.iterdir():
            entries.append(f"{'d' if entry.is_dir() else 'f'} {entry.name}")
        output = "\n".join(sorted(entries))
        return ToolResult.ok(output=output, data={"path": str(target), "count": len(entries)}, tool_name="ls_dir")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="ls_dir")


@jarvis_tool(
    name="file_read",
    description="Read a file (path required)",
    params={
        "path": {"type": "string", "required": True},
        "limit": {"type": "integer", "required": False, "default": 0}
    },
    rank="R0", scope="READ", category="file", tags=["file", "read"]
)
def file_read(path: str, limit: int = 0) -> ToolResult:
    try:
        target = _validate_path(path, must_exist=True, allow_dir=False)
        content = target.read_text(encoding="utf-8", errors="replace")
        if limit > 0:
            lines = content.splitlines()
            content = "\n".join(lines[:limit])
        return ToolResult.ok(output=content, data={"path": str(target), "size": target.stat().st_size}, tool_name="file_read")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="file_read")


@jarvis_tool(
    name="file_write",
    description="Write content to a file",
    params={
        "path": {"type": "string", "required": True},
        "content": {"type": "string", "required": True}
    },
    rank="R2", scope="LOCAL", category="file", tags=["file", "write"]
)
def file_write(path: str, content: str) -> ToolResult:
    try:
        target = _validate_path(path, must_exist=False, allow_dir=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult.ok(output=f"Written {len(content)} chars to {target}", data={"path": str(target)}, tool_name="file_write")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="file_write")


@jarvis_tool(
    name="file_append",
    description="Append content to a file",
    params={
        "path": {"type": "string", "required": True},
        "content": {"type": "string", "required": True}
    },
    rank="R2", scope="LOCAL", category="file", tags=["file", "append"]
)
def file_append(path: str, content: str) -> ToolResult:
    try:
        target = _validate_path(path, must_exist=False, allow_dir=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as f:
            f.write(content)
        return ToolResult.ok(output=f"Appended {len(content)} chars to {target}", data={"path": str(target)}, tool_name="file_append")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="file_append")


@jarvis_tool(
    name="file_delete",
    description="Delete a file",
    params={"path": {"type": "string", "required": True}},
    rank="R2", scope="LOCAL", category="file", tags=["file", "delete"]
)
def file_delete(path: str) -> ToolResult:
    try:
        target = _validate_path(path, must_exist=True, allow_dir=False)
        target.unlink()
        return ToolResult.ok(output=f"Deleted {target}", data={"path": str(target)}, tool_name="file_delete")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="file_delete")


@jarvis_tool(
    name="file_copy",
    description="Copy a file",
    params={
        "src": {"type": "string", "required": True},
        "dst": {"type": "string", "required": True}
    },
    rank="R2", scope="LOCAL", category="file", tags=["file", "copy"]
)
def file_copy(src: str, dst: str) -> ToolResult:
    try:
        src_path = _validate_path(src, must_exist=True, allow_dir=False)
        dst_path = _validate_path(dst, must_exist=False, allow_dir=False)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src_path), str(dst_path))
        return ToolResult.ok(
            output=f"Copied {src_path} -> {dst_path}",
            data={"src": str(src_path), "dst": str(dst_path)},
            tool_name="file_copy"
        )
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="file_copy")


@jarvis_tool(
    name="file_move",
    description="Move/rename a file",
    params={
        "src": {"type": "string", "required": True},
        "dst": {"type": "string", "required": True}
    },
    rank="R2", scope="LOCAL", category="file", tags=["file", "move"]
)
def file_move(src: str, dst: str) -> ToolResult:
    try:
        src_path = _validate_path(src, must_exist=True, allow_dir=False)
        dst_path = _validate_path(dst, must_exist=False, allow_dir=False)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_path), str(dst_path))
        return ToolResult.ok(
            output=f"Moved {src_path} -> {dst_path}",
            data={"src": str(src_path), "dst": str(dst_path)},
            tool_name="file_move"
        )
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="file_move")


@jarvis_tool(
    name="find_files",
    description="Find files by name pattern",
    params={
        "pattern": {"type": "string", "required": True},
        "path": {"type": "string", "required": False, "default": "."}
    },
    rank="R0", scope="READ", category="file", tags=["file", "find", "search"]
)
def find_files(pattern: str, path: str = ".") -> ToolResult:
    try:
        base = _validate_path(path, must_exist=True, allow_dir=True)
        matches = list(base.rglob(pattern))
        lines = [str(m.relative_to(base)) for m in matches]
        output = "\n".join(lines)
        return ToolResult.ok(
            output=output,
            data={"matches": [str(m) for m in matches], "count": len(matches)},
            tool_name="find_files"
        )
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="find_files")


@jarvis_tool(
    name="grep_search",
    description="Search file contents with grep",
    params={
        "pattern": {"type": "string", "required": True},
        "path": {"type": "string", "required": True},
        "recursive": {"type": "boolean", "required": False, "default": False}
    },
    rank="R0", scope="READ", category="file", tags=["file", "grep", "search"]
)
def grep_search(pattern: str, path: str, recursive: bool = False) -> ToolResult:
    try:
        target = _validate_path(path, must_exist=True, allow_dir=True)
        cmd = ["grep", "-n", "-H"]
        if recursive:
            cmd.append("-r")
        cmd.append(pattern)
        cmd.append(str(target))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = result.stdout if result.stdout else "No matches."
        return ToolResult.ok(output=output, tool_name="grep_search")
    except FileNotFoundError:
        return ToolResult.fail(error="grep not found", tool_name="grep_search")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="grep_search")


@jarvis_tool(
    name="chmod_chown",
    description="Change file permissions and owner",
    params={
        "path": {"type": "string", "required": True},
        "mode": {"type": "string", "required": False},
        "owner": {"type": "string", "required": False}
    },
    rank="R2", scope="LOCAL", category="file", tags=["file", "permissions", "chmod", "chown"]
)
def chmod_chown(path: str, mode: Optional[str] = None, owner: Optional[str] = None) -> ToolResult:
    try:
        target = _validate_path(path, must_exist=True)
        msgs = []
        if mode:
            m = int(mode, 8)
            target.chmod(m)
            msgs.append(f"chmod {mode}")
        if owner:
            result = subprocess.run(
                ["sudo", "chown", owner, str(target)],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                return ToolResult.fail(error=result.stderr or "chown failed", tool_name="chmod_chown")
            msgs.append(f"chown {owner}")
        return ToolResult.ok(
            output=f"Updated {target}: {', '.join(msgs) if msgs else 'no changes'}",
            data={"path": str(target)},
            tool_name="chmod_chown"
        )
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="chmod_chown")


@jarvis_tool(
    name="tar_compress",
    description="Create tar archive",
    params={
        "output": {"type": "string", "required": True},
        "sources": {"type": "array", "required": True}
    },
    rank="R2", scope="LOCAL", category="file", tags=["file", "archive", "tar"]
)
def tar_compress(output: str, sources: list) -> ToolResult:
    try:
        out_path = _validate_path(output, must_exist=False, allow_dir=False)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        src_paths = [_validate_path(str(s), must_exist=True) for s in sources]
        with tarfile.open(str(out_path), "w") as tar:
            for sp in src_paths:
                tar.add(str(sp), arcname=sp.name)
        return ToolResult.ok(
            output=f"Created archive {out_path} with {len(src_paths)} item(s).",
            data={"archive": str(out_path)},
            tool_name="tar_compress"
        )
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="tar_compress")


@jarvis_tool(
    name="hash_verify",
    description="Calculate file hash (md5, sha256)",
    params={
        "path": {"type": "string", "required": True},
        "algorithm": {"type": "string", "required": False, "default": "sha256"}
    },
    rank="R0", scope="READ", category="file", tags=["file", "hash", "integrity"]
)
def hash_verify(path: str, algorithm: str = "sha256") -> ToolResult:
    try:
        target = _validate_path(path, must_exist=True, allow_dir=False)
        h = hashlib.new(algorithm)
        with target.open("rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                h.update(chunk)
        digest = h.hexdigest()
        return ToolResult.ok(
            output=f"{algorithm.upper()}({target}) = {digest}",
            data={"algorithm": algorithm, "hash": digest, "path": str(target)},
            tool_name="hash_verify"
        )
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="hash_verify")
