import hashlib
from pathlib import Path
from typing import Any

from minicode_agent.tools.base import BaseTool, ToolError
from minicode_agent.tools.readonly import resolve_workspace_path
from minicode_agent.tools.types import PermissionMode, RiskLevel, ToolContext, ToolSpec


class WriteFileTool(BaseTool):
    spec = ToolSpec(
        name="write_file",
        description="Write UTF-8 text to a file inside the workspace. Requires approval.",
        risk_level=RiskLevel.MEDIUM,
        permission=PermissionMode.ASK,
    )

    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        path_arg = arguments.get("path")
        content = arguments.get("content")
        if not path_arg:
            raise ToolError("Missing required argument: path")
        if content is None:
            raise ToolError("Missing required argument: content")

        target = resolve_workspace_path(context.resolved_workspace, str(path_arg))
        existed = target.exists()
        if existed and not target.is_file():
            raise ToolError(f"Path is not a file: {path_arg}")
        if not target.parent.exists():
            if not arguments.get("create_parents", False):
                raise ToolError("Parent directory does not exist; set create_parents=true to create it")
            target.parent.mkdir(parents=True, exist_ok=True)

        before_text = read_utf8(target, str(path_arg)) if existed else ""
        target.write_text(str(content), encoding="utf-8")
        after_text = str(content)
        after_chars = len(str(content))

        action = "updated" if existed else "created"
        return (
            f"{action} {path_arg}",
            {
                "path": str(path_arg),
                "created": not existed,
                "before_chars": len(before_text),
                "after_chars": after_chars,
                "before_hash": text_hash(before_text) if existed else None,
                "after_hash": text_hash(after_text),
            },
        )


class EditFileTool(BaseTool):
    spec = ToolSpec(
        name="edit_file",
        description="Replace exact text in a UTF-8 file inside the workspace. Requires approval.",
        risk_level=RiskLevel.MEDIUM,
        permission=PermissionMode.ASK,
    )

    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        path_arg = arguments.get("path")
        old_text = arguments.get("old_text")
        new_text = arguments.get("new_text")
        if not path_arg:
            raise ToolError("Missing required argument: path")
        if old_text is None:
            raise ToolError("Missing required argument: old_text")
        if new_text is None:
            raise ToolError("Missing required argument: new_text")

        target = resolve_workspace_path(context.resolved_workspace, str(path_arg))
        if not target.exists():
            raise ToolError(f"File does not exist: {path_arg}")
        if not target.is_file():
            raise ToolError(f"Path is not a file: {path_arg}")

        original = read_utf8(target, str(path_arg))
        occurrences = original.count(str(old_text))
        if occurrences == 0:
            raise ToolError("old_text was not found in file")
        if occurrences > 1 and not arguments.get("replace_all", False):
            raise ToolError("old_text appears multiple times; set replace_all=true to replace all occurrences")

        if arguments.get("replace_all", False):
            updated = original.replace(str(old_text), str(new_text))
            replacements = occurrences
        else:
            updated = original.replace(str(old_text), str(new_text), 1)
            replacements = 1

        target.write_text(updated, encoding="utf-8")
        return (
            f"edited {path_arg}",
            {
                "path": str(path_arg),
                "replacements": replacements,
                "before_chars": len(original),
                "after_chars": len(updated),
                "before_hash": text_hash(original),
                "after_hash": text_hash(updated),
            },
        )


def read_utf8(path: Path, display_path: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError(f"File is not valid UTF-8 text: {display_path}") from exc


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
