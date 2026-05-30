import hashlib
import csv
import io
import json
import shutil
import tomllib
from pathlib import Path
from typing import Any

from minicode_agent.tools.base import BaseTool, ToolError
from minicode_agent.tools.readonly import resolve_workspace_path
from minicode_agent.tools.types import DuplicatePolicy, PermissionMode, RiskLevel, ToolContext, ToolIntent, ToolSpec, ToolStateEffect


class WriteFileTool(BaseTool):
    spec = ToolSpec(
        name="write_file",
        description="Overwrite UTF-8 text in a workspace file, creating the file if it does not exist. Requires approval.",
        risk_level=RiskLevel.MEDIUM,
        permission=PermissionMode.ASK,
        duplicate_policy=DuplicatePolicy.BLOCK_IDENTICAL_SUCCESS,
        state_effects=(ToolStateEffect.MARKS_MODIFIED_FILE,),
        intents=(ToolIntent.FILE_OVERWRITE, ToolIntent.FILE_CREATE),
        path_arg_names=("path",),
        capability="file_write",
    )

    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        path_arg = arguments.get("path")
        content = arguments.get("content")
        overwrite = bool(arguments.get("overwrite", True))
        if not path_arg:
            raise ToolError("Missing required argument: path")
        if content is None:
            raise ToolError("Missing required argument: content")

        target = resolve_workspace_path(context.resolved_workspace, str(path_arg))
        existed = target.exists()
        if existed and not target.is_file():
            raise ToolError(f"Path is not a file: {path_arg}")
        if existed and not overwrite:
            raise ToolError("File already exists; set overwrite=true or use append_file")
        ensure_parent(target, arguments)

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


class AppendFileTool(BaseTool):
    spec = ToolSpec(
        name="append_file",
        description="Append UTF-8 text to a workspace file with format-aware spacing for text, Markdown, code, JSON, CSV, TOML, and YAML. Creates the file if it does not exist. Requires approval.",
        risk_level=RiskLevel.MEDIUM,
        permission=PermissionMode.ASK,
        duplicate_policy=DuplicatePolicy.BLOCK_IDENTICAL_SUCCESS,
        state_effects=(ToolStateEffect.MARKS_MODIFIED_FILE,),
        intents=(ToolIntent.FILE_APPEND, ToolIntent.FILE_CREATE),
        path_arg_names=("path",),
        capability="file_append",
    )

    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        path_arg = arguments.get("path")
        content = arguments.get("content")
        append_strategy = str(arguments.get("append_strategy") or "auto").lower()
        if not path_arg:
            raise ToolError("Missing required argument: path")
        if content is None:
            raise ToolError("Missing required argument: content")

        target = resolve_workspace_path(context.resolved_workspace, str(path_arg))
        existed = target.exists()
        if existed and not target.is_file():
            raise ToolError(f"Path is not a file: {path_arg}")
        ensure_parent(target, arguments)

        before_text = read_utf8(target, str(path_arg)) if existed else ""
        appended_text = str(content)
        append_format = str(arguments.get("append_format") or "auto").lower()
        separator = arguments.get("separator")
        after_text = format_append(
            before_text,
            appended_text,
            path_arg=str(path_arg),
            append_format=append_format,
            append_strategy=append_strategy,
            separator=str(separator) if separator is not None else None,
        )
        target.write_text(after_text, encoding="utf-8")
        return (
            f"appended {path_arg}",
            {
                "path": str(path_arg),
                "created": not existed,
                "append_format": resolved_append_format(str(path_arg), before_text, appended_text, append_format),
                "before_chars": len(before_text),
                "appended_chars": len(appended_text),
                "after_chars": len(after_text),
                "before_hash": text_hash(before_text) if existed else None,
                "after_hash": text_hash(after_text),
            },
        )


class CreateFileTool(BaseTool):
    spec = ToolSpec(
        name="create_file",
        description="Create a new UTF-8 workspace file and fail if it already exists. Requires approval.",
        risk_level=RiskLevel.MEDIUM,
        permission=PermissionMode.ASK,
        duplicate_policy=DuplicatePolicy.BLOCK_IDENTICAL_SUCCESS,
        state_effects=(ToolStateEffect.MARKS_MODIFIED_FILE,),
        intents=(ToolIntent.FILE_CREATE,),
        path_arg_names=("path",),
        capability="file_create",
    )

    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        path_arg = arguments.get("path")
        content = arguments.get("content", "")
        if not path_arg:
            raise ToolError("Missing required argument: path")

        target = resolve_workspace_path(context.resolved_workspace, str(path_arg))
        if target.exists():
            raise ToolError(f"File already exists: {path_arg}")
        ensure_parent(target, arguments)

        text = str(content)
        target.write_text(text, encoding="utf-8")
        return (
            f"created {path_arg}",
            {
                "path": str(path_arg),
                "created": True,
                "before_chars": 0,
                "after_chars": len(text),
                "before_hash": None,
                "after_hash": text_hash(text),
            },
        )


class DeleteFileTool(BaseTool):
    spec = ToolSpec(
        name="delete_file",
        description="Delete a file inside the workspace. Requires approval.",
        risk_level=RiskLevel.MEDIUM,
        permission=PermissionMode.ASK,
        duplicate_policy=DuplicatePolicy.BLOCK_IDENTICAL_SUCCESS,
        state_effects=(ToolStateEffect.MARKS_MODIFIED_FILE,),
        intents=(ToolIntent.FILE_DELETE,),
        path_arg_names=("path",),
        capability="file_delete",
    )

    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        path_arg = arguments.get("path")
        if not path_arg:
            raise ToolError("Missing required argument: path")

        target = resolve_workspace_path(context.resolved_workspace, str(path_arg))
        missing_ok = bool(arguments.get("missing_ok", False))
        if not target.exists():
            if missing_ok:
                return f"missing {path_arg}", {"path": str(path_arg), "deleted": False, "missing_ok": True}
            raise ToolError(f"File does not exist: {path_arg}")
        if not target.is_file():
            raise ToolError(f"Path is not a file: {path_arg}")

        before_text = read_utf8(target, str(path_arg))
        target.unlink()
        return (
            f"deleted {path_arg}",
            {
                "path": str(path_arg),
                "deleted": True,
                "before_chars": len(before_text),
                "after_chars": 0,
                "before_hash": text_hash(before_text),
                "after_hash": None,
            },
        )


class EditFileTool(BaseTool):
    spec = ToolSpec(
        name="edit_file",
        description="Replace exact text in a UTF-8 file inside the workspace. Requires approval.",
        risk_level=RiskLevel.MEDIUM,
        permission=PermissionMode.ASK,
        duplicate_policy=DuplicatePolicy.BLOCK_IDENTICAL_SUCCESS,
        state_effects=(ToolStateEffect.MARKS_MODIFIED_FILE,),
        intents=(ToolIntent.FILE_EDIT,),
        path_arg_names=("path",),
        capability="file_edit",
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


def ensure_parent(path: Path, arguments: dict[str, Any]) -> None:
    if path.parent.exists():
        return
    if not arguments.get("create_parents", False):
        raise ToolError("Parent directory does not exist; set create_parents=true to create it")
    path.parent.mkdir(parents=True, exist_ok=True)


def format_append(
    before_text: str,
    appended_text: str,
    *,
    path_arg: str,
    append_format: str,
    append_strategy: str = "auto",
    separator: str | None = None,
) -> str:
    resolved = resolved_append_format(path_arg, before_text, appended_text, append_format)
    if append_strategy not in {"auto", "text", "line", "paragraph", "raw"}:
        raise ToolError("append_strategy must be one of: auto, text, line, paragraph, raw")
    if append_strategy == "line":
        separator = separator or "\n"
    elif append_strategy == "paragraph":
        separator = separator or "\n\n"
    elif append_strategy == "raw":
        return before_text + appended_text
    if separator is not None:
        return append_with_separator(before_text, appended_text, separator)
    if not before_text:
        return normalize_new_file_append(appended_text, resolved)
    if resolved == "raw":
        return before_text + appended_text
    if resolved == "json":
        return append_json(before_text, appended_text, path_arg)
    if resolved == "csv":
        return append_csv(before_text, appended_text, path_arg)
    if resolved == "toml":
        return append_toml(before_text, appended_text, path_arg)
    if resolved == "yaml":
        return append_with_separator(before_text.rstrip(), appended_text.lstrip(), "\n\n", trailing_newline=True)
    if resolved in {"text", "markdown"}:
        return append_with_separator(before_text.rstrip(), appended_text.lstrip(), "\n\n", trailing_newline=True)
    if resolved == "code":
        return append_with_separator(
            before_text.rstrip(),
            appended_text.lstrip("\n"),
            code_append_separator(path_arg, before_text, appended_text),
            trailing_newline=True,
        )
    return append_with_separator(before_text, appended_text, "\n", trailing_newline=True)


def resolved_append_format(path_arg: str, before_text: str, appended_text: str, append_format: str) -> str:
    allowed = {"auto", "raw", "text", "markdown", "code", "json", "csv", "toml", "yaml"}
    if append_format not in allowed:
        raise ToolError(f"append_format must be one of: {', '.join(sorted(allowed))}")
    if append_format != "auto":
        return append_format
    suffix = Path(path_arg).suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix in {".csv"}:
        return "csv"
    if suffix in {".toml"}:
        return "toml"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    if suffix in {".md", ".markdown", ".txt", ".rst"}:
        return "markdown" if suffix in {".md", ".markdown"} else "text"
    if suffix in {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".cs", ".rb", ".php", ".sh", ".ps1", ".css", ".html"}:
        return "code"
    stripped = (before_text or appended_text).lstrip()
    if stripped.startswith(("{", "[")):
        return "json"
    return "text"


def normalize_new_file_append(appended_text: str, append_format: str) -> str:
    if append_format == "json":
        return json_dumps_pretty(parse_json_text(appended_text, "content"))
    if append_format == "csv":
        return normalize_csv_new_file(appended_text)
    if append_format == "toml":
        return normalize_toml_new_file(appended_text)
    if append_format in {"text", "markdown", "code"} and appended_text and not appended_text.endswith("\n"):
        return appended_text + "\n"
    if append_format == "yaml" and appended_text and not appended_text.endswith("\n"):
        return appended_text + "\n"
    return appended_text


def append_with_separator(before_text: str, appended_text: str, separator: str, trailing_newline: bool = False) -> str:
    if not before_text:
        result = appended_text
    else:
        result = before_text + missing_separator_suffix(before_text, separator) + appended_text.lstrip(separator)
    if trailing_newline and result and not result.endswith("\n"):
        result += "\n"
    return result


def code_append_separator(path_arg: str, before_text: str, appended_text: str) -> str:
    suffix = Path(path_arg).suffix.lower()
    incoming = appended_text.lstrip()
    if not before_text.strip() or not incoming:
        return "\n"
    if suffix == ".py" and starts_python_top_level_block(incoming):
        return "\n\n"
    if suffix in {".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".cs", ".rb", ".php"} and starts_common_top_level_block(incoming):
        return "\n\n"
    if suffix in {".css", ".html"} and before_text.rstrip():
        return "\n\n"
    return "\n"


def starts_python_top_level_block(value: str) -> bool:
    first = first_nonempty_line(value)
    return first.startswith(("def ", "async def ", "class ", "@"))


def starts_common_top_level_block(value: str) -> bool:
    first = first_nonempty_line(value)
    prefixes = ("function ", "class ", "export ", "import ", "const ", "let ", "var ", "public ", "private ", "func ", "fn ")
    return first.startswith(prefixes)


def first_nonempty_line(value: str) -> str:
    for line in value.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def missing_separator_suffix(before_text: str, separator: str) -> str:
    if not separator or before_text.endswith(separator):
        return ""
    max_overlap = min(len(before_text), len(separator))
    for size in range(max_overlap, 0, -1):
        if before_text.endswith(separator[:size]):
            return separator[size:]
    return separator


def append_json(before_text: str, appended_text: str, path_arg: str) -> str:
    existing = parse_json_text(before_text, path_arg)
    incoming = parse_json_text(appended_text, "content")
    if isinstance(existing, list):
        combined = [*existing, *incoming] if isinstance(incoming, list) else [*existing, incoming]
        return json_dumps_pretty(combined)
    if isinstance(existing, dict) and isinstance(incoming, dict):
        return json_dumps_pretty({**existing, **incoming})
    raise ToolError("JSON append requires an existing array, or an existing object with object content")


def append_csv(before_text: str, appended_text: str, path_arg: str) -> str:
    rows = parse_csv_rows(before_text, path_arg)
    incoming_rows = parse_csv_rows(appended_text, "content")
    if rows and incoming_rows and len(rows[0]) != len(incoming_rows[0]):
        raise ToolError("CSV append requires matching column counts")
    rows.extend(incoming_rows)
    return rows_to_csv(rows)


def append_toml(before_text: str, appended_text: str, path_arg: str) -> str:
    base = before_text.rstrip() + ("\n\n" if before_text.strip() else "")
    candidate = base + appended_text.strip()
    validate_toml(candidate, path_arg)
    if not candidate.endswith("\n"):
        candidate += "\n"
    return candidate


def parse_csv_rows(value: str, display_path: str) -> list[list[str]]:
    if not value.strip():
        return []
    try:
        reader = csv.reader(io.StringIO(value))
        return [row for row in reader]
    except csv.Error as exc:
        raise ToolError(f"Invalid CSV for append_file: {display_path}") from exc


def rows_to_csv(rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def normalize_csv_new_file(appended_text: str) -> str:
    rows = parse_csv_rows(appended_text, "content")
    if not rows:
        return ""
    return rows_to_csv(rows)


def normalize_toml_new_file(appended_text: str) -> str:
    text = appended_text.strip()
    validate_toml(text, "content")
    return text + ("\n" if text and not text.endswith("\n") else "")


def validate_toml(value: str, display_path: str) -> None:
    try:
        tomllib.loads(value)
    except tomllib.TOMLDecodeError as exc:
        raise ToolError(f"Invalid TOML for append_file: {display_path}") from exc


def parse_json_text(value: str, display_path: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ToolError(f"Invalid JSON for append_file: {display_path}") from exc


def json_dumps_pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def read_utf8(path: Path, display_path: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError(f"File is not valid UTF-8 text: {display_path}") from exc


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
