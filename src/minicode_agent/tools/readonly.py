import fnmatch
import shutil
import subprocess
from pathlib import Path
from typing import Any

from minicode_agent.tools.base import BaseTool, ToolError
from minicode_agent.tools.types import PermissionMode, RiskLevel, ToolContext, ToolSpec

DEFAULT_EXCLUDES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    ".venv",
    "venv",
    "build",
    "dist",
    "*.egg-info",
}

WINDOWS_GIT_CANDIDATES = (
    Path(r"C:\Program Files\Git\cmd\git.exe"),
    Path(r"C:\Program Files\Git\bin\git.exe"),
)


def resolve_workspace_path(workspace: Path, requested_path: str | None = None) -> Path:
    root = workspace.expanduser().resolve()
    target = root if not requested_path else (root / requested_path).expanduser().resolve()
    if target != root and root not in target.parents:
        raise ToolError(f"Path escapes workspace: {requested_path}")
    return target


def should_exclude(path: Path, patterns: set[str]) -> bool:
    return any(part in patterns or any(fnmatch.fnmatch(part, pattern) for pattern in patterns) for part in path.parts)


class ListFilesTool(BaseTool):
    spec = ToolSpec(
        name="list_files",
        description="List files under the workspace or a relative directory.",
        risk_level=RiskLevel.SAFE,
        permission=PermissionMode.ALLOW,
    )

    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        path_arg = arguments.get("path")
        max_files = int(arguments.get("max_files", 200))
        target = resolve_workspace_path(context.resolved_workspace, path_arg)
        if not target.exists():
            raise ToolError(f"Path does not exist: {path_arg or '.'}")
        if not target.is_dir():
            raise ToolError(f"Path is not a directory: {path_arg or '.'}")

        root = context.resolved_workspace
        files: list[str] = []
        truncated = False
        for child in sorted(target.rglob("*")):
            rel = child.relative_to(root)
            if should_exclude(rel, DEFAULT_EXCLUDES):
                continue
            files.append(str(rel).replace("\\", "/") + ("/" if child.is_dir() else ""))
            if len(files) >= max_files:
                truncated = True
                break

        return "\n".join(files), {"count": len(files), "truncated": truncated, "path": path_arg or "."}


class ReadFileTool(BaseTool):
    spec = ToolSpec(
        name="read_file",
        description="Read a UTF-8 text file inside the workspace.",
        risk_level=RiskLevel.SAFE,
        permission=PermissionMode.ALLOW,
    )

    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        path_arg = arguments.get("path")
        if not path_arg:
            raise ToolError("Missing required argument: path")
        target = resolve_workspace_path(context.resolved_workspace, str(path_arg))
        if not target.exists():
            raise ToolError(f"File does not exist: {path_arg}")
        if not target.is_file():
            raise ToolError(f"Path is not a file: {path_arg}")

        max_chars = int(arguments.get("max_chars", 20000))
        text = target.read_text(encoding="utf-8")
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]
        return text, {"path": path_arg, "chars": len(text), "truncated": truncated}


class SearchCodeTool(BaseTool):
    spec = ToolSpec(
        name="search_code",
        description="Search text in files under the workspace.",
        risk_level=RiskLevel.SAFE,
        permission=PermissionMode.ALLOW,
    )

    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        pattern = arguments.get("pattern")
        if not pattern:
            raise ToolError("Missing required argument: pattern")
        max_matches = int(arguments.get("max_matches", 100))
        root = context.resolved_workspace

        matches: list[str] = []
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if not path.is_file() or should_exclude(rel, DEFAULT_EXCLUDES):
                continue
            try:
                for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                    if str(pattern) in line:
                        rel_text = str(rel).replace("\\", "/")
                        matches.append(f"{rel_text}:{line_no}: {line.strip()}")
                        if len(matches) >= max_matches:
                            return "\n".join(matches), {"count": len(matches), "truncated": True}
            except UnicodeDecodeError:
                continue

        return "\n".join(matches), {"count": len(matches), "truncated": False}


class GitStatusTool(BaseTool):
    spec = ToolSpec(
        name="git_status",
        description="Return short git status for the workspace.",
        risk_level=RiskLevel.LOW,
        permission=PermissionMode.ALLOW,
    )

    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        return run_git(context.resolved_workspace, ["status", "--short"])


class GitDiffTool(BaseTool):
    spec = ToolSpec(
        name="git_diff",
        description="Return git diff for the workspace.",
        risk_level=RiskLevel.LOW,
        permission=PermissionMode.ALLOW,
    )

    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        args = ["diff"]
        if arguments.get("stat"):
            args.append("--stat")
        return run_git(context.resolved_workspace, args)


def run_git(workspace: Path, args: list[str]) -> tuple[str, dict[str, Any]]:
    git_executable = find_git_executable()
    completed = subprocess.run(
        [str(git_executable), *args],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    output = completed.stdout.strip()
    error = completed.stderr.strip()
    if completed.returncode != 0:
        raise ToolError(error or f"git {' '.join(args)} failed with exit code {completed.returncode}")
    return output, {
        "exit_code": completed.returncode,
        "command": f"git {' '.join(args)}",
        "git_executable": str(git_executable),
    }


def find_git_executable() -> Path:
    candidate = shutil.which("git")
    if candidate:
        path = Path(candidate)
        if path.is_file():
            return path

    for path in WINDOWS_GIT_CANDIDATES:
        if path.is_file():
            return path

    raise ToolError("git executable not found. Install Git or configure PATH.")
