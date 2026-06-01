from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rich.align import Align
from rich.box import ROUNDED
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from minicode_agent.agent import AgentLoop
from minicode_agent.cli.chat_commands import handle_chat_command
from minicode_agent.cli.preview_renderer import render_preview_text
from minicode_agent.config import MiniCodeConfig, normalize_memory_reflection_mode
from minicode_agent.intent import (
    contains_cjk,
    direct_chat_reply,
    ensure_response_language,
    is_direct_chat_query,
    model_direct_chat_reply,
    response_language_from_preferences,
)
from minicode_agent.memory import MemoryKind, MemoryStore, default_memory_db_path
from minicode_agent.models import OpenAICompatibleClient
from minicode_agent.runtime import RuntimeContext
from minicode_agent.tools.registry import create_default_registry


PALETTE = {
    ".": None,
    "o": "#87513c",
    "a": "#f08f5a",
    "b": "#ffb36b",
    "c": "#ffd39a",
    "w": "#fff2dc",
    "e": "#2b211c",
    "p": "#ff9f91",
    "s": "#9ad8ff",
    "t": "#6ebdf7",
}

GLYPHS = {
    ".": "  ",
    "o": "  ",
    "a": "  ",
    "b": "  ",
    "c": "  ",
    "w": "  ",
    "e": "  ",
    "p": "  ",
    "s": "  ",
    "t": "  ",
}

AVATAR_ROWS = [
    ".....oo....oo.....",
    "....oaaaooaaao....",
    "...oaabaaaaaabo...",
    "..oaabbcccccbbbo..",
    ".oabbcwwwwwwwcbbo.",
    ".oabcwwewwwewwcbo.",
    ".oabcwwwwpwwwwcbo.",
    "..oabcwwwwwwwcbo..",
    "...oabccccccbao...",
    "....ooabbbbao.....",
    ".....oossssoo.....",
    "....oosttttssoo...",
    ".....oosttssoo....",
    "......oo..oo......",
]

RECENT_ACTIVITY_LINES = 5


@dataclass
class ChatTurn:
    prompt: str
    run_id: str
    final_phase: str
    tool_calls: int
    selected_skills: list[str] = field(default_factory=list)
    summary: str = ""
    failure_reason: str | None = None
    trace_backend: str = ""
    trace_path: Path | None = None
    memory_mode: str = "deterministic"
    phase: str = ""
    last_tool: str | None = None
    last_tool_ok: bool | None = None
    last_tool_result: str | None = None
    diff_preview: dict | None = None


@dataclass
class ChatRunSnapshot:
    phase: str = "init"
    tool: str | None = None
    tool_ok: bool | None = None
    tool_result: str | None = None
    diff_preview: dict | None = None
    trace_run_id: str | None = None
    trace_path: Path | None = None
    trace_backend: str = ""


@dataclass
class ChatSession:
    workspace: Path
    model_name: str | None
    model_base_url: str
    no_model: bool
    llm_rerank: bool
    memory_reflection_mode: str
    interactive_approval: bool = True
    notices: list[str] = field(default_factory=list)
    turns: list[ChatTurn] = field(default_factory=list)
    snapshot: ChatRunSnapshot = field(default_factory=ChatRunSnapshot)
    preferred_language: str | None = None
    user_preferences: list[str] = field(default_factory=list)

    def record_preview(self, preview: dict) -> None:
        self.snapshot.diff_preview = preview


def run_chat_session(
    task: str | None,
    workspace: Path,
    model: str | None = None,
    model_base_url: str | None = None,
    no_model: bool = False,
    llm_rerank: bool = False,
    memory_reflection_mode: str = "deterministic",
    preview: bool = False,
) -> None:
    config = MiniCodeConfig.from_env(workspace)
    if model or model_base_url:
        config = config.model_copy(
            update={
                "model_name": model or config.model_name,
                "model_base_url": model_base_url or config.model_base_url,
            }
        )
    if no_model:
        config = config.model_copy(update={"model_name": None})
    memory_reflection_mode = normalize_memory_reflection_mode(memory_reflection_mode)
    session = ChatSession(
        workspace=config.workspace,
        model_name=config.model_name,
        model_base_url=config.model_base_url,
        no_model=no_model,
        llm_rerank=llm_rerank,
        memory_reflection_mode=memory_reflection_mode,
        interactive_approval=not preview,
        preferred_language=load_preferred_language(config.workspace),
        user_preferences=load_user_preferences(config.workspace),
    )
    model_client = build_model_client(config)
    initial_task = task.strip() if task and task.strip() else None
    if initial_task and initial_task.startswith("/"):
        if handle_chat_command(initial_task, session):
            return
    elif initial_task:
        session.turns.append(run_turn(session, initial_task, config, model_client))
    if preview:
        render_chat_screen(session)
        return
    render_chat_intro(session)
    if session.notices:
        render_latest_notice(session)
    if session.turns:
        render_latest_turn(session)
    while True:
        command, input_panel_height = input_bar()
        if not command:
            clear_previous_terminal_lines(Console(), input_panel_height)
            continue
        if command.startswith("/"):
            clear_previous_terminal_lines(Console(), input_panel_height)
            if handle_chat_command(command, session):
                Console().clear()
                return
            refresh_recent_activity(session)
            render_latest_notice(session)
            continue
        session.turns.append(run_turn(session, command, config, model_client, input_panel_height=input_panel_height))
        refresh_recent_activity(session)
        render_latest_turn(session)


def build_model_client(config: MiniCodeConfig):
    if not config.model_name:
        return None
    return OpenAICompatibleClient(
        model=config.model_name,
        api_key=config.model_api_key,
        base_url=config.model_base_url,
    )


def run_turn(
    session: ChatSession,
    task: str,
    config: MiniCodeConfig,
    model_client,
    input_panel_height: int = 0,
) -> ChatTurn:
    remember_session_preferences(session, task)
    if is_direct_chat_query(task):
        console = Console()
        if input_panel_height:
            clear_previous_terminal_lines(console, input_panel_height)
        if model_client is not None:
            status = console.status("[bold #f5c48c]MiniCode is thinking...[/bold #f5c48c]", spinner="dots")
            with status:
                turn = build_direct_chat_turn(session, task, model_client=model_client)
        else:
            turn = build_direct_chat_turn(session, task, model_client=model_client)
        return turn
    runtime = RuntimeContext.create(config.workspace, run_kind="chat")
    seed_session_preferences(runtime.memory_store, session)
    seed_recent_chat_context(runtime.memory_store, session)
    console = Console()
    if input_panel_height:
        clear_previous_terminal_lines(console, input_panel_height)
    status = console.status("[bold #f5c48c]MiniCode is thinking...[/bold #f5c48c]", spinner="dots")
    with status:
        result = AgentLoop(
            runtime,
            task,
            max_steps=config.max_agent_steps,
            max_failed_tool_attempts=config.max_failed_tool_attempts,
            model_client=model_client,
            aux_model_client=model_client,
            enable_skill_rerank=session.llm_rerank,
            memory_reflection_mode=session.memory_reflection_mode,
            event_callback=build_chat_event_callback(session, None),
            approval_callback=build_approval_callback(console, status, on_preview=session.record_preview, clear_after=True) if session.interactive_approval else None,
        ).run()
    summary = localize_summary_language(
        task,
        summarize_turn(result) or result.state.task_state.history_summary or "No summary available.",
        session=session,
        model_client=model_client,
    )
    failure_reason = result.state.task_state.failed_attempts[-1] if result.state.current_phase.value == "failed" and result.state.task_state.failed_attempts else None
    turn = ChatTurn(
        prompt=task,
        run_id=runtime.run_id,
        final_phase=result.state.current_phase.value,
        tool_calls=result.state.metrics.tool_calls,
        selected_skills=list(result.state.selected_skills),
        summary=summary,
        failure_reason=failure_reason,
        trace_backend=runtime.trace_store.backend,
        trace_path=runtime.trace_store.storage_path,
        memory_mode=session.memory_reflection_mode,
        phase=result.state.current_phase.value,
        last_tool=session.snapshot.tool,
        last_tool_ok=session.snapshot.tool_ok,
        last_tool_result=session.snapshot.tool_result,
        diff_preview=session.snapshot.diff_preview,
    )
    session.snapshot.trace_run_id = runtime.run_id
    session.snapshot.trace_path = runtime.trace_store.storage_path
    session.snapshot.trace_backend = runtime.trace_store.backend
    return turn


def build_direct_chat_turn(session: ChatSession, task: str, model_client=None) -> ChatTurn:
    prompt = task.strip()
    recent_user_messages = [turn.prompt for turn in session.turns[-6:]]
    if model_client is not None:
        summary = model_direct_chat_reply(
            prompt,
            model_client,
            workspace=session.workspace,
            tool_registry=create_default_registry(),
            model_name=display_model_name(session),
            preferred_language=session.preferred_language,
            recent_user_messages=recent_user_messages,
            user_preferences=session.user_preferences,
        )
        trace_backend = "model"
    else:
        summary = direct_chat_reply(
            prompt,
            create_default_registry(),
            model_name=display_model_name(session),
            preferred_language=session.preferred_language,
            recent_user_messages=recent_user_messages,
            user_preferences=session.user_preferences,
        )
        trace_backend = "direct_fallback"
    return ChatTurn(
        prompt=prompt,
        run_id="chat_direct",
        final_phase="done",
        tool_calls=0,
        selected_skills=[],
        summary=summary,
        failure_reason=None,
        trace_backend=trace_backend,
        trace_path=None,
        memory_mode=session.memory_reflection_mode,
        phase="done",
    )

class ChatRunStream:
    def __init__(self, console: Console) -> None:
        self.console = console

    def handle(self, event_type: str, payload: dict) -> None:
        line = format_stream_event(event_type, payload)
        if line:
            self.console.print(line)


def build_approval_callback(console: Console, status=None, on_preview=None, clear_after: bool = False):
    def approve(tool: str, arguments: dict, reason: str, preview: dict | None = None) -> bool:
        pause_status(status)
        printed_lines = 0
        try:
            if preview:
                if on_preview is not None:
                    on_preview(preview)
                preview_text = (
                    render_compact_approval_preview(tool, preview)
                    if clear_after
                    else render_preview_text(preview, heading=f"Pending write: {tool}")
                )
                printed_lines += len(preview_text.splitlines()) + 1
                console.print(Text(preview_text, style="#cfc7b9"))
            prompt = "是否批准？[y/N] "
            answer = console.input(Text(prompt, style="bold #9ad8ff")).strip().lower()
            printed_lines += 1
            approved = answer in {"y", "yes"}
            if not approved:
                console.print(Text("未应用变更。", style="bold #ffb3b3"))
                printed_lines += 1
            return approved
        finally:
            if clear_after and printed_lines:
                clear_previous_terminal_lines(console, printed_lines)
            resume_status(status)

    return approve


def render_compact_approval_preview(tool: str, preview: dict) -> str:
    summary = str(preview.get("summary") or f"Pending write: {tool}").strip()
    operation = str(preview.get("operation") or "").strip()
    paths = ", ".join(str(path) for path in preview.get("paths", []) if str(path).strip())
    stats = preview.get("stats") if isinstance(preview.get("stats"), dict) else {}
    stats_text = ""
    if stats:
        stats_text = f" +{stats.get('insertions', 0)} -{stats.get('deletions', 0)} hunks={stats.get('hunks', 0)}"
    detail = " | ".join(part for part in (operation, paths, stats_text.strip()) if part)
    return f"Pending write: {tool}\n{summary}" + (f"\n{detail}" if detail else "") + "\nFull preview is available with /diff after this turn."


def pause_status(status) -> None:
    if status is not None and hasattr(status, "stop"):
        status.stop()


def resume_status(status) -> None:
    if status is not None and hasattr(status, "start"):
        status.start()


def format_stream_event(event_type: str, payload: dict) -> Text | None:
    if event_type == "phase_changed":
        return None
    if event_type == "agent_planned":
        text = Text("plan  ", style="#77706a")
        text.append(str(payload.get("description") or ""), style="#efe8dd")
        tool = payload.get("tool")
        if tool:
            text.append(f"  -> {tool}", style="#f5c48c")
        return text
    if event_type == "action_result":
        ok = "ok" if payload.get("ok") else "failed"
        style = "#9ad8ff" if payload.get("ok") else "#ffb3b3"
        text = Text("tool  ", style="#77706a")
        text.append(str(payload.get("tool") or ""), style="bold #f5c48c")
        text.append(f"  {ok}", style=style)
        result = str(payload.get("result") or "").strip().replace("\n", " ")
        if result:
            text.append(f"  {result[:96]}", style="#cfc7b9")
        return text
    if event_type == "planning_failed":
        text = Text("model failed  ", style="bold #ffb3b3")
        text.append(str(payload.get("reason") or ""), style="#ffb3b3")
        return text
    if event_type == "verification":
        text = Text("verify ", style="#77706a")
        text.append("passed" if payload.get("verified") else "failed", style="#9ad8ff" if payload.get("verified") else "#ffb3b3")
        return text
    return None


def render_chat_screen(session: ChatSession) -> None:
    from rich.console import Console

    console = Console()
    console.clear()
    top = render_top_panel(session)
    conversation = render_conversation_area(session)
    bottom = render_bottom_panel(session)
    console.print(top)
    console.print()
    console.print(conversation)
    console.print()
    console.print(bottom)


def render_chat_frame(session: ChatSession, console: Console | None = None) -> None:
    console = console or Console()
    console.clear()
    console.print(render_top_panel(session))
    console.print()
    console.print(render_conversation_area(session))
    console.print()


def render_chat_intro(session: ChatSession, console: Console | None = None) -> None:
    console = console or Console()
    console.clear()
    console.print(render_top_panel(session))
    console.print()


def refresh_chat_intro(session: ChatSession, console: Console | None = None) -> None:
    console = console or Console()
    if not console.is_terminal:
        return
    console.file.write("\x1b[s\x1b[H")
    top_panel = render_top_panel(session)
    top_height = len(console.render_lines(top_panel, console.options)) + 1
    clear_forward_terminal_lines(console, top_height)
    console.print(top_panel)
    console.print()
    console.file.write("\x1b[u")
    console.file.flush()


def refresh_recent_activity(session: ChatSession, console: Console | None = None) -> None:
    console = console or Console()
    if not console.is_terminal:
        return
    row, column = recent_activity_position(session, console)
    width = max(24, console.width - column - 2)
    lines = recent_activity_lines(session)
    console.file.write("\x1b[s")
    for index in range(RECENT_ACTIVITY_LINES):
        line = lines[index] if index < len(lines) else Text()
        line = clipped_activity_line(line, width)
        console.file.write(f"\x1b[{row + index};{column}H")
        console.print(line, end="")
    console.file.write("\x1b[u")
    console.file.flush()


def render_latest_notice(session: ChatSession, console: Console | None = None) -> None:
    if not session.notices:
        return
    console = console or Console()
    console.print(render_system_notice(session.notices[-1]))
    console.print()


def render_latest_turn(session: ChatSession, console: Console | None = None) -> None:
    if not session.turns:
        return
    console = console or Console()
    for block in render_turn_dialogue(session.turns[-1]):
        console.print(block)
    console.print()


def render_top_panel(session: ChatSession) -> Panel:
    left = render_left_column(session)
    right = render_right_column(session, include_commands=False)
    body = Table.grid(expand=True)
    body.add_column(ratio=2)
    body.add_column(ratio=3)
    body.add_row(left, right)
    return Panel(body, box=ROUNDED, border_style="#f08f5a", padding=(1, 2), title="[bold #f5c48c]MiniCode Agent[/bold #f5c48c]")


def render_left_column(session: ChatSession) -> RenderableType:
    title = Text("MiniCode", style="bold #f5c48c")
    subtitle = Text("local coding agent runtime", style="#cfc7b9")
    model = Text(display_model_name(session), style="#9ad8ff")
    return Group(Align.center(title), Align.center(subtitle), Align.center(model), Text(), render_avatar())


def render_right_column(session: ChatSession, include_commands: bool = True) -> RenderableType:
    recent = Text()
    for line in recent_activity_lines(session):
        recent.append_text(line)
        recent.append("\n")
    if not include_commands:
        return recent
    tips = Text()
    tips.append("\nCommands\n", style="bold #f5c48c")
    tips.append("/help    show shortcuts\n", style="#9ad8ff")
    tips.append("/status  show latest run\n", style="#9ad8ff")
    tips.append("/memory  show recent memory\n", style="#9ad8ff")
    tips.append("/skills  show route summary\n", style="#9ad8ff")
    tips.append("/trace   show recent trace\n", style="#9ad8ff")
    tips.append("/diff    show latest diff\n", style="#9ad8ff")
    tips.append("/tools   show tool registry\n", style="#9ad8ff")
    tips.append("/config  show session config\n", style="#9ad8ff")
    tips.append("/last    show latest turn summary\n", style="#9ad8ff")
    tips.append("/clear   clear this session\n", style="#9ad8ff")
    tips.append("/exit    leave MiniCode\n", style="#9ad8ff")
    return Group(recent, tips)


def recent_activity_lines(session: ChatSession) -> list[Text]:
    lines = [Text("Recent Activity", style="bold #f5c48c")]
    if not session.turns:
        lines.append(Text("No recent activity", style="#cfc7b9"))
        return lines
    for turn in session.turns[-4:]:
        status = "ok" if turn.final_phase == "done" else turn.final_phase
        line = Text()
        line.append(f"- {status}  ", style="#9ad8ff")
        line.append(turn.prompt[:52], style="#efe8dd")
        if len(turn.prompt) > 52:
            line.append("...", style="#efe8dd")
        line.append(f"  ({turn.tool_calls} tools)", style="#b8aa73")
        lines.append(line)
    return lines


def clipped_activity_line(line: Text, width: int) -> Text:
    clipped = line.copy()
    clipped.truncate(width, overflow="ellipsis")
    padding = max(0, width - len(clipped.plain))
    if padding:
        clipped.append(" " * padding)
    return clipped


def recent_activity_column(session: ChatSession, console: Console) -> int:
    return recent_activity_position(session, console)[1]


def recent_activity_position(session: ChatSession, console: Console) -> tuple[int, int]:
    probe_lines = console.render_lines(render_top_panel(session), console.options)
    for row, line in enumerate(probe_lines, start=1):
        text = "".join(segment.text for segment in line)
        index = text.find("Recent Activity")
        if index >= 0:
            return row, index + 1
    return 4, max(38, int(console.width * 0.53))


def render_conversation_area(session: ChatSession) -> RenderableType:
    blocks: list[RenderableType] = []
    if session.notices:
        blocks.extend(render_system_notice(notice) for notice in session.notices[-1:])
        blocks.append(Text())
    if not session.turns:
        blocks.append(Align.center(Text("No messages yet. Type below to start.", style="#77706a")))
        return Group(*blocks)
    for turn in session.turns[-3:]:
        blocks.extend(render_turn_dialogue(turn))
        blocks.append(Text())
    return Group(*blocks)


def render_turn_dialogue(turn: ChatTurn) -> list[RenderableType]:
    return [
        render_rule("USER", "#f08f5a"),
        wrap_text(turn.prompt, "#efe8dd"),
        render_rule("", "#3d3d3d"),
        render_rule("MINICODE", "#9ad8ff"),
        render_turn_summary(turn),
        render_rule("", "#3d3d3d"),
    ]


def render_system_notice(message: str) -> Panel:
    body = Text()
    body.append("SYSTEM\n", style="bold #9ad8ff")
    body.append(message, style="#efe8dd")
    return Panel(body, box=ROUNDED, border_style="#9ad8ff", padding=(0, 1))


def render_rule(label: str, color: str) -> Text:
    width = 76
    if label:
        label_text = f" {label} "
        left = max(2, (width - len(label_text)) // 2)
        right = max(2, width - left - len(label_text))
        value = "-" * left + label_text + "-" * right
    else:
        value = "-" * width
    return Text(value, style=color)


def render_turn_summary(turn: ChatTurn) -> Text:
    text = Text()
    text.append(turn.summary, style="#efe8dd")
    if turn.failure_reason:
        text.append(f"\nfailure: {turn.failure_reason}", style="bold #ffb3b3")
    return text


def wrap_text(value: str, style: str, width: int = 76) -> Text:
    text = Text(style=style)
    for index, line in enumerate(value.splitlines() or [""]):
        if index:
            text.append("\n")
        text.append(line)
    text.no_wrap = False
    text.overflow = "fold"
    return text


def render_bottom_panel(session: ChatSession | None = None) -> Panel:
    prompt_hint = Text()
    prompt_hint.append("Enter a task, or ", style="#cfc7b9")
    prompt_hint.append("/help", style="bold #9ad8ff")
    prompt_hint.append(" / ", style="#cfc7b9")
    prompt_hint.append("/clear", style="bold #9ad8ff")
    prompt_hint.append(" / ", style="#cfc7b9")
    prompt_hint.append("/exit", style="bold #9ad8ff")
    prompt_hint.append(" to leave. ", style="#cfc7b9")
    prompt_hint.append("Use /help for status, memory, skills, trace, diff, tools, config, last.", style="#77706a")
    return Panel(
        prompt_hint,
        box=ROUNDED,
        border_style="#f08f5a",
        padding=(0, 2),
        title="[bold #f5c48c]Command[/bold #f5c48c]",
    )


def input_bar() -> tuple[str, int]:
    console = Console()
    bottom_panel = render_bottom_panel(None)
    panel_height = len(console.render_lines(bottom_panel, console.options))
    console.print(bottom_panel)
    value = console.input("> ").strip()
    return value, panel_height + 1


def clear_previous_terminal_lines(console: Console, count: int) -> None:
    if console.is_terminal:
        console.file.write("\x1b[1A\x1b[2K" * count)
        console.file.flush()


def clear_forward_terminal_lines(console: Console, count: int) -> None:
    if console.is_terminal:
        console.file.write("\x1b[2K")
        console.file.write("\x1b[1B\x1b[2K" * max(0, count - 1))
        console.file.write(f"\x1b[{max(0, count - 1)}A")
        console.file.flush()


def render_avatar() -> Group:
    lines: list[Text] = []
    for row in AVATAR_ROWS:
        line = Text()
        for token in row:
            color = PALETTE[token]
            glyph = GLYPHS[token]
            if color is None:
                line.append(glyph)
            else:
                line.append(glyph, style=f"on {color}")
        lines.append(Align.center(line))
    return Group(*lines)


def display_model_name(session: ChatSession) -> str:
    return session.model_name or "no-model"


def load_user_preferences(workspace: Path) -> list[str]:
    store = MemoryStore(default_memory_db_path(workspace))
    records = store.search("user preference language", limit=5, kind=MemoryKind.USER, tags=["preference"])
    return [record.content for record in records]


def load_preferred_language(workspace: Path) -> str | None:
    preferences = load_user_preferences(workspace)
    for preference in preferences:
        if "中文" in preference or "Chinese" in preference:
            return "zh"
        if "English" in preference or "英文" in preference:
            return "en"
    return None


def remember_session_preferences(session: ChatSession, task: str) -> None:
    language = language_preference_from_text(task)
    if language:
        session.preferred_language = language
        content = "User prefers Chinese replies." if language == "zh" else "User prefers English replies."
        if content not in session.user_preferences:
            session.user_preferences.insert(0, content)
        persist_user_preference(session.workspace, content, tags=["preference", "language"])


def language_preference_from_text(text: str) -> str | None:
    normalized = text.strip().lower()
    if any(phrase in text for phrase in ("说中文", "用中文", "中文回答", "请用中文", "一直说中文")):
        return "zh"
    if any(phrase in normalized for phrase in ("speak english", "reply in english", "use english")) or "英文回答" in text:
        return "en"
    return None


def persist_user_preference(workspace: Path, content: str, tags: list[str]) -> None:
    store = MemoryStore(default_memory_db_path(workspace))
    store.add(
        MemoryKind.USER,
        content,
        confidence=0.9,
        source_run_id="chat_session",
        tags=tags,
        reason="explicit chat preference",
        metadata={"source": "chat_direct"},
        admission_reason="explicit user preference",
    )


def seed_session_preferences(store: MemoryStore, session: ChatSession) -> None:
    for preference in session.user_preferences[:5]:
        store.add(
            MemoryKind.USER,
            preference,
            confidence=0.9,
            source_run_id="chat_session",
            tags=["preference", "session"],
            reason="active chat session preference",
            metadata={"source": "chat_session"},
            admission_reason="active session preference",
        )


def seed_recent_chat_context(store: MemoryStore, session: ChatSession) -> None:
    recent_turns = session.turns[-4:]
    if not recent_turns:
        return
    lines = []
    for turn in recent_turns:
        lines.append(f"User: {turn.prompt}")
        if turn.summary:
            lines.append(f"MiniCode: {turn.summary}")
    store.add(
        MemoryKind.USER,
        "Recent chat context:\n" + "\n".join(lines),
        confidence=0.85,
        source_run_id="chat_session",
        tags=["session", "conversation"],
        reason="active chat session context",
        metadata={"source": "chat_session", "ephemeral": True},
        admission_reason="active session context",
    )


def summarize_turn(result) -> str:
    for decision in result.state.task_state.decisions:
        if decision != "Keep the first loop minimal and traceable.":
            return str(decision)
    for event in reversed(result.transcript):
        payload = event.get("payload", {})
        if event.get("event") == "agent_observed":
            value = payload.get("result") or payload.get("output") or payload.get("error")
            if value:
                return str(value)[:160]
        if event.get("event") == "agent_planned":
            value = payload.get("description") or payload.get("tool")
            if value:
                return str(value)[:160]
    return result.state.task_state.decisions[-1] if result.state.task_state.decisions else "No summary available."


def localize_summary_language(task: str, summary: str, session: ChatSession | None = None, model_client=None) -> str:
    response_language = (
        response_language_from_preferences(session.user_preferences, session.preferred_language, task)
        if session is not None
        else ("Chinese" if contains_cjk(task) else "English")
    )
    if response_language == "Chinese" and summary.strip() and not contains_cjk(summary):
        rewritten = ensure_response_language(summary, response_language, model_client, user_message=task)
        if contains_cjk(rewritten):
            return rewritten
        return "模型返回的最终说明语言与当前请求不一致；变更已完成，请用 /trace 或 /diff 查看本轮细节。"
    return summary


def build_chat_event_callback(session: ChatSession, stream: ChatRunStream | None):
    def callback(event_type: str, payload: dict) -> None:
        if event_type == "phase_changed":
            session.snapshot.phase = str(payload.get("phase") or session.snapshot.phase)
        elif event_type == "action_result":
            session.snapshot.tool = str(payload.get("tool") or "") or None
            session.snapshot.tool_ok = bool(payload.get("ok"))
            session.snapshot.tool_result = str(payload.get("result") or payload.get("error") or "") or None
            preview = payload.get("metadata", {}).get("preview") if isinstance(payload.get("metadata"), dict) else None
            if isinstance(preview, dict):
                session.record_preview(preview)
        elif event_type == "write_preview":
            preview = payload.get("preview")
            if isinstance(preview, dict):
                session.record_preview(preview)
        if stream is not None:
            stream.handle(event_type, payload)

    return callback

def horizontal_rule(width: int) -> Text:
    rule_width = max(20, width - 1)
    return Text("─" * rule_width, style="#8f8b84")
