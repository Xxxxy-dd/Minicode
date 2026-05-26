import subprocess
from pathlib import Path
from typing import Any

from minicode_agent.tools.base import BaseTool, ToolError
from minicode_agent.tools.readonly import find_git_executable, resolve_workspace_path
from minicode_agent.tools.types import DuplicatePolicy, PermissionMode, RiskLevel, ToolContext, ToolIntent, ToolSpec, ToolStateEffect


class ApplyPatchTool(BaseTool):
    spec = ToolSpec(
        name="apply_patch",
        description="Apply a unified diff patch inside the workspace. Requires approval.",
        input_schema={
            "type": "object",
            "properties": {
                "patch": {"type": "string", "description": "Unified diff content to apply."},
                "content": {"type": "string", "description": "Alias for patch."},
            },
            "anyOf": [{"required": ["patch"]}, {"required": ["content"]}],
        },
        risk_level=RiskLevel.MEDIUM,
        permission=PermissionMode.ASK,
        duplicate_policy=DuplicatePolicy.BLOCK_IDENTICAL_SUCCESS,
        state_effects=(ToolStateEffect.MARKS_MODIFIED_FILE,),
        intents=(ToolIntent.FILE_EDIT,),
        capability="patch_apply",
        timeout_seconds=30,
    )

    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        patch = arguments.get("patch") or arguments.get("content")
        if not patch:
            raise ToolError("Missing required argument: patch")
        patch_text = str(patch)
        changed_paths = extract_patch_paths(patch_text)
        for path in changed_paths:
            resolve_workspace_path(context.resolved_workspace, path)

        git_executable = find_git_executable()
        check_result = run_git_apply(git_executable, context.resolved_workspace, patch_text, check_only=True, timeout_seconds=self.spec.timeout_seconds)
        if check_result.returncode != 0:
            raise ToolError((check_result.stderr or check_result.stdout or "git apply --check failed").strip())
        completed = run_git_apply(git_executable, context.resolved_workspace, patch_text, check_only=False, timeout_seconds=self.spec.timeout_seconds)
        if completed.returncode != 0:
            raise ToolError((completed.stderr or completed.stdout or "git apply failed").strip())
        return (
            f"applied patch touching {len(changed_paths)} path(s)",
            {
                "paths": changed_paths,
                "path_count": len(changed_paths),
                "patch_chars": len(patch_text),
                "check_command": "git apply --check --whitespace=nowarn -",
                "check_exit_code": check_result.returncode,
                "apply_command": "git apply --whitespace=nowarn -",
                "exit_code": completed.returncode,
            },
        )


def run_git_apply(
    git_executable: Path,
    workspace: Path,
    patch_text: str,
    *,
    check_only: bool,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    args = [str(git_executable), "apply"]
    if check_only:
        args.append("--check")
    args.extend(["--whitespace=nowarn", "-"])
    return subprocess.run(
        args,
        input=patch_text,
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )


def extract_patch_paths(patch_text: str) -> list[str]:
    paths: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith(("--- ", "+++ ")):
            raw = line[4:].strip().split("\t", 1)[0]
            path = normalize_patch_path(raw)
            if path and path not in paths:
                paths.append(path)
    return paths


def normalize_patch_path(raw: str) -> str | None:
    if raw == "/dev/null":
        return None
    path = raw
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    return str(Path(path).as_posix())
