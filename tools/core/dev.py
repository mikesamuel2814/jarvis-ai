"""
Jarvis v3 Development Tools (10 tools)
Category: dev | Rank: R0-R2 | Scope: READ, LOCAL
"""

import os
import subprocess
from pathlib import Path
from typing import Optional

from ..decorator import jarvis_tool
from ..result import ToolResult


def _validate_path_under_home(path: str) -> bool:
    try:
        target = Path(path).resolve()
        home = Path("/home/kali").resolve()
        return str(target).startswith(str(home))
    except Exception:
        return False


@jarvis_tool(
    name="git_status",
    description="Git status for a repo path",
    params={"path": {"type": "string", "required": True}},
    rank="R0", scope="READ", category="dev", tags=["git", "status", "vcs"]
)
def git_status(path: str) -> ToolResult:
    try:
        result = subprocess.run(["git", "-C", path, "status"], capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return ToolResult.fail(error=result.stderr.strip(), tool_name="git_status")
        return ToolResult.ok(output=result.stdout.strip(), tool_name="git_status")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="git_status")


@jarvis_tool(
    name="git_commit",
    description="Git commit with message (path, message)",
    params={"path": {"type": "string", "required": True}, "message": {"type": "string", "required": True}},
    rank="R2", scope="LOCAL", category="dev", tags=["git", "commit", "vcs"]
)
def git_commit(path: str, message: str) -> ToolResult:
    try:
        if not _validate_path_under_home(path):
            return ToolResult.fail(error="Repo path must be under /home/kali", tool_name="git_commit")
        add_result = subprocess.run(["git", "-C", path, "add", "-A"], capture_output=True, text=True, timeout=15)
        if add_result.returncode != 0:
            return ToolResult.fail(error=f"git add failed: {add_result.stderr.strip()}", tool_name="git_commit")
        commit_result = subprocess.run(["git", "-C", path, "commit", "-m", message], capture_output=True, text=True, timeout=15)
        if commit_result.returncode != 0:
            return ToolResult.fail(error=f"git commit failed: {commit_result.stderr.strip()}", tool_name="git_commit")
        return ToolResult.ok(output=commit_result.stdout.strip(), tool_name="git_commit")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="git_commit")


@jarvis_tool(
    name="git_push",
    description="Git push (path, remote, branch)",
    params={"path": {"type": "string", "required": True}, "remote": {"type": "string", "required": False, "default": "origin"}, "branch": {"type": "string", "required": False, "default": ""}},
    rank="R2", scope="LOCAL", category="dev", tags=["git", "push", "vcs"]
)
def git_push(path: str, remote: str = "origin", branch: str = "") -> ToolResult:
    try:
        cmd = ["git", "-C", path, "push", remote]
        if branch:
            cmd.append(branch)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return ToolResult.fail(error=result.stderr.strip(), tool_name="git_push")
        return ToolResult.ok(output=result.stdout.strip(), tool_name="git_push")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="git_push")


@jarvis_tool(
    name="git_pull",
    description="Git pull (path, remote, branch)",
    params={"path": {"type": "string", "required": True}, "remote": {"type": "string", "required": False, "default": "origin"}, "branch": {"type": "string", "required": False, "default": ""}},
    rank="R2", scope="LOCAL", category="dev", tags=["git", "pull", "vcs"]
)
def git_pull(path: str, remote: str = "origin", branch: str = "") -> ToolResult:
    try:
        cmd = ["git", "-C", path, "pull", remote]
        if branch:
            cmd.append(branch)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return ToolResult.fail(error=result.stderr.strip(), tool_name="git_pull")
        return ToolResult.ok(output=result.stdout.strip(), tool_name="git_pull")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="git_pull")


@jarvis_tool(
    name="npm_install",
    description="npm install in directory",
    params={"directory": {"type": "string", "required": True}},
    rank="R1", scope="LOCAL", category="dev", tags=["npm", "node", "install"]
)
def npm_install(directory: str) -> ToolResult:
    try:
        result = subprocess.run(["npm", "install"], cwd=directory, capture_output=True, text=True, timeout=120)
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output.strip(), tool_name="npm_install")
        return ToolResult.ok(output=output.strip(), tool_name="npm_install")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="npm_install")


@jarvis_tool(
    name="pnpm_build",
    description="pnpm build in directory",
    params={"directory": {"type": "string", "required": True}},
    rank="R1", scope="LOCAL", category="dev", tags=["pnpm", "build", "node"]
)
def pnpm_build(directory: str) -> ToolResult:
    try:
        result = subprocess.run(["pnpm", "build"], cwd=directory, capture_output=True, text=True, timeout=120)
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output.strip(), tool_name="pnpm_build")
        return ToolResult.ok(output=output.strip(), tool_name="pnpm_build")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="pnpm_build")


@jarvis_tool(
    name="docker_build",
    description="Docker build (path, tag)",
    params={"path": {"type": "string", "required": True}, "tag": {"type": "string", "required": True}},
    rank="R2", scope="LOCAL", category="dev", tags=["docker", "build", "container"]
)
def docker_build(path: str, tag: str) -> ToolResult:
    try:
        result = subprocess.run(["docker", "build", "-t", tag, path], capture_output=True, text=True, timeout=300)
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output.strip(), tool_name="docker_build")
        return ToolResult.ok(output=output.strip(), tool_name="docker_build")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="docker_build")


@jarvis_tool(
    name="docker_run",
    description="Docker run container (image, ports, env)",
    params={"image": {"type": "string", "required": True}, "ports": {"type": "string", "required": False, "default": ""}, "env": {"type": "string", "required": False, "default": ""}},
    rank="R2", scope="LOCAL", category="dev", tags=["docker", "run", "container"]
)
def docker_run(image: str, ports: str = "", env: str = "") -> ToolResult:
    try:
        cmd = ["docker", "run", "-d"]
        if ports:
            for p in ports.split(","):
                cmd.extend(["-p", p.strip()])
        if env:
            for e in env.split(","):
                cmd.extend(["-e", e.strip()])
        cmd.append(image)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output.strip(), tool_name="docker_run")
        return ToolResult.ok(output=output.strip(), data={"container_id": result.stdout.strip()}, tool_name="docker_run")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="docker_run")


@jarvis_tool(
    name="pytest_run",
    description="Run pytest in directory",
    params={"directory": {"type": "string", "required": True}},
    rank="R2", scope="LOCAL", category="dev", tags=["pytest", "test", "python"]
)
def pytest_run(directory: str) -> ToolResult:
    try:
        result = subprocess.run(["python3", "-m", "pytest"], cwd=directory, capture_output=True, text=True, timeout=120)
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output.strip(), tool_name="pytest_run")
        return ToolResult.ok(output=output.strip(), tool_name="pytest_run")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="pytest_run")


@jarvis_tool(
    name="code_lint",
    description="Run linter (eslint, flake8, etc.) in directory",
    params={"directory": {"type": "string", "required": True}, "linter": {"type": "string", "required": False, "default": "flake8"}},
    rank="R1", scope="READ", category="dev", tags=["lint", "code", "quality"]
)
def code_lint(directory: str, linter: str = "flake8") -> ToolResult:
    try:
        result = subprocess.run([linter, directory], capture_output=True, text=True, timeout=120)
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output.strip(), tool_name="code_lint")
        return ToolResult.ok(output=output.strip(), tool_name="code_lint")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="code_lint")
