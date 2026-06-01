from __future__ import annotations

from typing import Callable, Protocol

from minicode_agent.cli.renderers import (
    render_diff_summary,
    render_memory_summary,
    render_skill_route_summary,
    render_tool_summary,
    render_trace_summary,
)
from minicode_agent.memory import MemoryStore, default_memory_db_path
from minicode_agent.skills import SkillRouter, default_skill_registry
from minicode_agent.tools.registry import create_default_registry
from minicode_agent.trace import TraceStore, default_trace_db_path


class ChatCommandSession(Protocol):
    workspace: object
    model_name: str | None
    model_base_url: str
    no_model: bool
    llm_rerank: bool
    memory_reflection_mode: str
    interactive_approval: bool
    notices: list[str]
    turns: list
    snapshot: object


def handle_chat_command(command: str, session: ChatCommandSession) -> bool:
    lowered = command.strip().lower()
    if lowered in {"/exit", "/quit"}:
        return True
    if lowered == "/clear":
        session.turns.clear()
        session.notices = ["Session cleared."]
        return False
    if lowered == "/help":
        session.notices = [help_notice()]
        return False
    handler = CHAT_COMMANDS.get(lowered)
    if handler is not None:
        session.notices = [handler(session)]
        return False
    return False


ChatCommandHandler = Callable[[ChatCommandSession], str]


CHAT_COMMANDS: dict[str, ChatCommandHandler] = {
    "/status": lambda session: status_notice(session),
    "/memory": lambda session: memory_notice(session),
    "/skills": lambda session: skills_notice(session),
    "/trace": lambda session: trace_notice(session),
    "/diff": lambda session: render_diff_summary(session.snapshot.diff_preview),
    "/tools": lambda session: tools_notice(),
    "/config": lambda session: config_notice(session),
    "/last": lambda session: last_notice(session),
}


def help_notice() -> str:
    commands = ["/help", *CHAT_COMMANDS.keys(), "/clear", "/exit"]
    return "Shortcuts: " + ", ".join(commands) + "."


def status_notice(session: ChatCommandSession) -> str:
    latest = session.turns[-1] if session.turns else None
    snapshot = session.snapshot
    lines = [
        f"workspace: {session.workspace}",
        f"model: {session.model_name or 'rules'}",
        f"phase: {snapshot.phase}",
        f"last_tool: {render_tool_summary(snapshot.tool, snapshot.tool_ok, snapshot.tool_result)}",
        f"trace_id: {snapshot.trace_run_id or (latest.run_id if latest else '(none)')}",
        f"selected_skills: {', '.join(latest.selected_skills) if latest and latest.selected_skills else '(none)'}",
    ]
    return "\n".join(lines)


def memory_notice(session: ChatCommandSession) -> str:
    store = MemoryStore(default_memory_db_path(session.workspace))
    records = store.list(limit=5, include_stale=True)
    return render_memory_summary(records, backend=store.backend, path=store.db_path)


def skills_notice(session: ChatCommandSession) -> str:
    if not session.turns:
        return "(no task to route yet)"
    latest_prompt = session.turns[-1].prompt
    router = SkillRouter(default_skill_registry(session.workspace))
    return render_skill_route_summary(router.route(latest_prompt))


def trace_notice(session: ChatCommandSession) -> str:
    if not session.snapshot.trace_run_id:
        return "(no run trace yet)"
    store = TraceStore(default_trace_db_path(session.workspace))
    events = store.list_events(session.snapshot.trace_run_id)
    return render_trace_summary(events, backend=store.backend, path=store.storage_path)


def tools_notice() -> str:
    lines = ["tools:"]
    for tool in create_default_registry().list():
        spec = tool.spec
        lines.append(f"- {spec.name} risk={spec.risk_level.value} permission={spec.permission.value}")
    return "\n".join(lines)


def config_notice(session: ChatCommandSession) -> str:
    return "\n".join(
        [
            f"workspace: {session.workspace}",
            f"model: {session.model_name or 'rules'}",
            f"model_base_url: {session.model_base_url}",
            f"no_model: {session.no_model}",
            f"llm_rerank: {session.llm_rerank}",
            f"memory_reflection_mode: {session.memory_reflection_mode}",
            f"interactive_approval: {session.interactive_approval}",
        ]
    )


def last_notice(session: ChatCommandSession) -> str:
    if not session.turns:
        return "(no completed turn yet)"
    latest = session.turns[-1]
    return "\n".join(
        [
            f"prompt: {latest.prompt}",
            f"run_id: {latest.run_id}",
            f"phase: {latest.phase or latest.final_phase}",
            f"tool_calls: {latest.tool_calls}",
            f"last_tool: {render_tool_summary(latest.last_tool, latest.last_tool_ok, latest.last_tool_result)}",
            f"selected_skills: {', '.join(latest.selected_skills) or '(none)'}",
            f"summary: {latest.summary}",
        ]
    )
