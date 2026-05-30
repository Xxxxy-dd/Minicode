from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from minicode_agent.permissions.policy import is_sensitive_path
from minicode_agent.tools.base import ToolError
from minicode_agent.tools.patch import extract_patch_paths
from minicode_agent.tools.readonly import resolve_workspace_path
from minicode_agent.tools.write import format_append, read_utf8


MAX_PREVIEW_CHARS = 4000
MAX_PATCH_LINES = 80
APPEND_CONTEXT_LINES = 4
SNIPPET_PREVIEW_LINES = 40


@dataclass
class PreviewBlock:
    kind: str
    title: str
    content: str
    path: str | None = None
    language: str | None = None
    truncated: bool = False

    def metadata(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "content": self.content,
            "path": self.path,
            "language": self.language,
            "truncated": self.truncated,
        }


@dataclass
class WritePreview:
    tool: str
    operation: str
    paths: list[str]
    summary: str
    diff: str
    created: list[str]
    deleted: list[str]
    insertions: int = 0
    deletions: int = 0
    hunks: int = 0
    hunks_truncated: bool = False
    display_blocks: list[PreviewBlock] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        truncated = text_is_truncated(self.diff) or self.hunks_truncated or any(block.truncated for block in self.display_blocks)
        return {
            "tool": self.tool,
            "operation": self.operation,
            "paths": self.paths,
            "summary": self.summary,
            "diff": self.diff,
            "created": self.created,
            "deleted": self.deleted,
            "diff_chars": len(self.diff),
            "truncated": truncated,
            "full_preview_available": not truncated,
            "insertions": self.insertions,
            "deletions": self.deletions,
            "hunks": self.hunks,
            "hunks_truncated": self.hunks_truncated,
            "stats": {
                "insertions": self.insertions,
                "deletions": self.deletions,
                "hunks": self.hunks,
            },
            "display_blocks": [block.metadata() for block in self.display_blocks],
            "risk_notes": self.risk_notes,
        }


def build_write_preview(tool: str, workspace: Path, arguments: dict[str, Any]) -> WritePreview | None:
    if tool == "write_file":
        return preview_write_file(tool, workspace, arguments)
    if tool == "append_file":
        return preview_append_file(tool, workspace, arguments)
    if tool == "create_file":
        return preview_create_file(tool, workspace, arguments)
    if tool == "delete_file":
        return preview_delete_file(tool, workspace, arguments)
    if tool == "edit_file":
        return preview_edit_file(tool, workspace, arguments)
    if tool == "apply_patch":
        return preview_apply_patch(tool, workspace, arguments)
    return None


def preview_write_file(tool: str, workspace: Path, arguments: dict[str, Any]) -> WritePreview:
    path_arg = required_text(arguments, "path")
    content = required_text(arguments, "content")
    overwrite = bool(arguments.get("overwrite", True))
    target = resolve_workspace_path(workspace, path_arg)
    existed = target.exists()
    if existed and not target.is_file():
        raise ToolError(f"Path is not a file: {path_arg}")
    if existed and not overwrite:
        raise ToolError("File already exists; set overwrite=true or use append_file")
    before = read_utf8(target, path_arg) if existed else ""
    return text_preview(tool, path_arg, before, content, created=not existed, operation="overwrite" if existed else "create")


def preview_append_file(tool: str, workspace: Path, arguments: dict[str, Any]) -> WritePreview:
    path_arg = required_text(arguments, "path")
    content = required_text(arguments, "content")
    target = resolve_workspace_path(workspace, path_arg)
    existed = target.exists()
    if existed and not target.is_file():
        raise ToolError(f"Path is not a file: {path_arg}")
    before = read_utf8(target, path_arg) if existed else ""
    after = format_append(
        before,
        content,
        path_arg=path_arg,
        append_format=str(arguments.get("append_format") or "auto").lower(),
        append_strategy=str(arguments.get("append_strategy") or "auto").lower(),
        separator=str(arguments["separator"]) if arguments.get("separator") is not None else None,
    )
    return append_preview(tool, path_arg, before, after, content, created=not existed)


def preview_create_file(tool: str, workspace: Path, arguments: dict[str, Any]) -> WritePreview:
    path_arg = required_text(arguments, "path")
    content = str(arguments.get("content", ""))
    target = resolve_workspace_path(workspace, path_arg)
    if target.exists():
        raise ToolError(f"File already exists: {path_arg}")
    return text_preview(tool, path_arg, "", content, created=True)


def preview_delete_file(tool: str, workspace: Path, arguments: dict[str, Any]) -> WritePreview:
    path_arg = required_text(arguments, "path")
    target = resolve_workspace_path(workspace, path_arg)
    if not target.exists():
        if arguments.get("missing_ok", False):
            return WritePreview(
                tool=tool,
                operation="delete",
                paths=[path_arg],
                summary=f"No change; {path_arg} is already missing.",
                diff="",
                created=[],
                deleted=[],
                display_blocks=[
                    PreviewBlock(
                        kind="notice",
                        title="Delete preview",
                        content=f"{path_arg} is already missing; no file will change.",
                        path=path_arg,
                    )
                ],
            )
        raise ToolError(f"File does not exist: {path_arg}")
    if not target.is_file():
        raise ToolError(f"Path is not a file: {path_arg}")
    before = read_utf8(target, path_arg)
    return text_preview(tool, path_arg, before, "", deleted=True, operation="delete")


def preview_edit_file(tool: str, workspace: Path, arguments: dict[str, Any]) -> WritePreview:
    path_arg = required_text(arguments, "path")
    old_text = required_text(arguments, "old_text")
    new_text = required_text(arguments, "new_text")
    target = resolve_workspace_path(workspace, path_arg)
    if not target.exists():
        raise ToolError(f"File does not exist: {path_arg}")
    if not target.is_file():
        raise ToolError(f"Path is not a file: {path_arg}")
    before = read_utf8(target, path_arg)
    occurrences = before.count(old_text)
    if occurrences == 0:
        raise ToolError("old_text was not found in file")
    if occurrences > 1 and not arguments.get("replace_all", False):
        raise ToolError("old_text appears multiple times; set replace_all=true to replace all occurrences")
    after = before.replace(old_text, new_text) if arguments.get("replace_all", False) else before.replace(old_text, new_text, 1)
    return text_preview(tool, path_arg, before, after, operation="edit")


def preview_apply_patch(tool: str, workspace: Path, arguments: dict[str, Any]) -> WritePreview:
    patch = arguments.get("patch") or arguments.get("content")
    if not patch:
        raise ToolError("Missing required argument: patch")
    patch_text = str(patch)
    paths = extract_patch_paths(patch_text)
    for path in paths:
        resolve_workspace_path(workspace, path)
        if is_sensitive_path(path):
            raise ToolError(f"Sensitive path is blocked by policy: {path}")
    stats = patch_stats(patch_text)
    diff, hunks_truncated = truncate_patch(patch_text)
    blocks = [
        PreviewBlock(
            kind="patch",
            title="Patch preview",
            content=diff,
            truncated=hunks_truncated or text_is_truncated(diff),
        )
    ]
    return WritePreview(
        tool=tool,
        operation="patch",
        paths=paths,
        summary=(
            f"Patch touches {len(paths)} path(s), "
            f"+{stats['insertions']} -{stats['deletions']}: {', '.join(paths) or '(none)'}"
        ),
        diff=diff,
        created=[],
        deleted=[],
        insertions=stats["insertions"],
        deletions=stats["deletions"],
        hunks=stats["hunks"],
        hunks_truncated=hunks_truncated,
        display_blocks=blocks,
        risk_notes=["Patch modifies multiple paths; review each touched file."] if len(paths) > 1 else [],
    )


def text_preview(
    tool: str,
    path_arg: str,
    before: str,
    after: str,
    *,
    created: bool = False,
    deleted: bool = False,
    operation: str | None = None,
) -> WritePreview:
    operation = operation or ("create" if created else "delete" if deleted else "edit")
    diff = unified_diff(path_arg, before, after)
    summary = summarize_text_change(path_arg, before, after, created=created, deleted=deleted)
    display_blocks = text_display_blocks(operation, path_arg, before, after, diff)
    return WritePreview(
        tool=tool,
        operation=operation,
        paths=[path_arg],
        summary=summary,
        diff=diff,
        created=[path_arg] if created else [],
        deleted=[path_arg] if deleted else [],
        insertions=sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")),
        deletions=sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")),
        hunks=sum(1 for line in diff.splitlines() if line.startswith("@@")),
        display_blocks=display_blocks,
        risk_notes=text_risk_notes(operation),
    )


def append_preview(
    tool: str,
    path_arg: str,
    before: str,
    after: str,
    appended_text: str,
    *,
    created: bool,
) -> WritePreview:
    added_text = appended_region(before, after, appended_text)
    diff = append_preview_text(path_arg, before, added_text, created=created)
    summary = (
        f"Create {path_arg}: +{len(added_text.splitlines())} lines."
        if created
        else f"Append to {path_arg}: +{len(added_text.splitlines())} lines."
    )
    return WritePreview(
        tool=tool,
        operation="append",
        paths=[path_arg],
        summary=summary,
        diff=diff,
        created=[path_arg] if created else [],
        deleted=[],
        insertions=sum(1 for line in added_text.splitlines() if line.strip()),
        deletions=0,
        hunks=1 if added_text else 0,
        display_blocks=[
            PreviewBlock(
                kind="append",
                title="Append preview",
                content=diff,
                path=path_arg,
                language=language_for_path(path_arg),
                truncated=text_is_truncated(diff),
            )
        ],
        risk_notes=["Target file does not exist; append will create it."] if created else [],
    )


def text_display_blocks(operation: str, path_arg: str, before: str, after: str, diff: str) -> list[PreviewBlock]:
    language = language_for_path(path_arg)
    if operation == "create":
        content, truncated = content_sample(after, prefix="+")
        return [
            PreviewBlock(
                kind="create",
                title="New file preview",
                content=f"+++ b/{path_arg}\n@@ new file @@\n{content}",
                path=path_arg,
                language=language,
                truncated=truncated,
            )
        ]
    if operation == "delete":
        content, truncated = content_sample(before, prefix="-")
        return [
            PreviewBlock(
                kind="delete",
                title="Deleted file sample",
                content=f"--- a/{path_arg}\n@@ deleted file head @@\n{content}",
                path=path_arg,
                language=language,
                truncated=truncated,
            )
        ]
    return [
        PreviewBlock(
            kind=operation,
            title="Change preview" if operation == "edit" else "Overwrite preview",
            content=diff,
            path=path_arg,
            language=language,
            truncated=text_is_truncated(diff),
        )
    ]


def content_sample(value: str, *, prefix: str) -> tuple[str, bool]:
    lines = value.splitlines()
    visible = lines[:SNIPPET_PREVIEW_LINES]
    content = "\n".join(f"{prefix}{line}" for line in visible)
    truncated = len(lines) > SNIPPET_PREVIEW_LINES
    if truncated:
        content += "\n[truncated]"
    if content:
        content += "\n"
    return truncate_text(content, MAX_PREVIEW_CHARS), truncated or text_is_truncated(content)


def text_risk_notes(operation: str) -> list[str]:
    if operation == "delete":
        return ["Deletes an existing file."]
    if operation == "overwrite":
        return ["Replaces the existing file content."]
    return []


def appended_region(before: str, after: str, requested_append: str) -> str:
    if before and after.startswith(before):
        return after[len(before):]
    return requested_append


def append_preview_text(path_arg: str, before: str, added_text: str, *, created: bool) -> str:
    lines = [f"--- a/{path_arg}", f"+++ b/{path_arg}"]
    if created:
        lines.append("@@ new file @@")
    else:
        lines.append("@@ append after end of file @@")
        context = before.rstrip("\n").splitlines()[-APPEND_CONTEXT_LINES:]
        for line in context:
            lines.append(f" {line}")
    if added_text and not added_text.startswith("\n") and before and not before.endswith("\n"):
        lines.append("")
    for line in added_text.splitlines():
        lines.append(f"+{line}")
    if added_text.endswith("\n") and not added_text.splitlines():
        lines.append("+")
    return truncate_text("\n".join(lines) + "\n", MAX_PREVIEW_CHARS)


def unified_diff(path_arg: str, before: str, after: str) -> str:
    lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{path_arg}",
            tofile=f"b/{path_arg}",
            lineterm="",
        )
    )
    text = "\n".join(lines)
    if text:
        text += "\n"
    return truncate_text(text, MAX_PREVIEW_CHARS)


def summarize_text_change(path_arg: str, before: str, after: str, *, created: bool, deleted: bool) -> str:
    before_lines = len(before.splitlines())
    after_lines = len(after.splitlines())
    if created:
        return f"Create {path_arg}: +{after_lines} lines."
    if deleted:
        return f"Delete {path_arg}: -{before_lines} lines."
    return f"Update {path_arg}: {before_lines} -> {after_lines} lines."


def truncate_patch(patch_text: str) -> tuple[str, bool]:
    lines = patch_text.splitlines(keepends=True)
    text = "".join(lines[:MAX_PATCH_LINES])
    truncated = len(lines) > MAX_PATCH_LINES
    if truncated:
        text += "[truncated]\n"
    text = truncate_text(text, MAX_PREVIEW_CHARS)
    return text, truncated or text.endswith("\n[truncated]")


def patch_stats(patch_text: str) -> dict[str, int]:
    insertions = 0
    deletions = 0
    hunks = 0
    for line in patch_text.splitlines():
        if line.startswith("@@"):
            hunks += 1
        elif line.startswith("+") and not line.startswith("+++"):
            insertions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return {"insertions": insertions, "deletions": deletions, "hunks": hunks}


def language_for_path(path_arg: str) -> str | None:
    suffix = Path(path_arg).suffix.lower()
    return {
        ".css": "css",
        ".csv": "csv",
        ".html": "html",
        ".js": "javascript",
        ".json": "json",
        ".md": "markdown",
        ".py": "python",
        ".toml": "toml",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".yaml": "yaml",
        ".yml": "yaml",
    }.get(suffix)


def text_is_truncated(value: str) -> bool:
    return value.endswith("\n[truncated]") or value.endswith("[truncated]\n") or len(value) > MAX_PREVIEW_CHARS


def truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n[truncated]"


def required_text(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if value is None or value == "":
        raise ToolError(f"Missing required argument: {key}")
    return str(value)
