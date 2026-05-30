from __future__ import annotations

from typing import Any


MAX_RENDER_CHARS = 4000


def render_preview_text(preview: dict[str, Any], *, heading: str = "Preview", max_chars: int = MAX_RENDER_CHARS) -> str:
    """Render structured write-preview metadata for CLI and chat approval prompts."""
    summary = str(preview.get("summary") or "").strip()
    lines: list[str] = []
    if heading:
        if summary and heading == "Preview":
            lines.append(f"{heading}: {summary}")
        else:
            lines.append(heading)
            if summary:
                lines.append(summary)
    elif summary:
        lines.append(summary)

    details = preview_details(preview)
    if details:
        lines.append(details)

    for note in preview.get("risk_notes") or []:
        if note:
            lines.append(f"! {note}")

    blocks = preview.get("display_blocks")
    if isinstance(blocks, list) and blocks:
        for block in blocks:
            rendered = render_block(block)
            if rendered:
                lines.append("")
                lines.append(rendered)
    else:
        diff = str(preview.get("diff") or "").strip()
        if diff:
            lines.append("")
            lines.append(diff)

    text = "\n".join(lines).strip()
    if len(text) > max_chars:
        return text[:max_chars] + "\n[truncated]"
    return text


def preview_details(preview: dict[str, Any]) -> str:
    operation = preview.get("operation")
    paths = preview.get("paths")
    stats = preview.get("stats") if isinstance(preview.get("stats"), dict) else {}
    parts: list[str] = []
    if operation:
        parts.append(f"operation={operation}")
    if isinstance(paths, list) and paths:
        parts.append(f"paths={', '.join(str(path) for path in paths)}")
    if stats and any(stats.get(key, 0) for key in ("insertions", "deletions", "hunks")):
        parts.append(
            "stats="
            f"+{int(stats.get('insertions') or 0)} "
            f"-{int(stats.get('deletions') or 0)} "
            f"hunks={int(stats.get('hunks') or 0)}"
        )
    if preview.get("truncated"):
        parts.append("truncated=yes")
    return " | ".join(parts)


def render_block(block: Any) -> str:
    if not isinstance(block, dict):
        return ""
    title = str(block.get("title") or "Preview block").strip()
    content = str(block.get("content") or "").strip()
    if not content:
        return f"[{title}]"
    suffix = "\n[truncated]" if block.get("truncated") and "[truncated]" not in content else ""
    return f"[{title}]\n{content}{suffix}"
