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
from minicode_agent.config import MiniCodeConfig, normalize_memory_reflection_mode
from minicode_agent.models import OpenAICompatibleClient
from minicode_agent.runtime import RuntimeContext


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


@dataclass
class ChatSession:
    workspace: Path
    model_name: str | None
    model_base_url: str
    no_model: bool
    llm_rerank: bool
    memory_reflection_mode: str
    notices: list[str] = field(default_factory=list)
    turns: list[ChatTurn] = field(default_factory=list)


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
    while True:
        render_chat_screen(session)
        command = input_bar()
        if not command:
            continue
        if command.startswith("/"):
            if handle_chat_command(command, session):
                from rich.console import Console

                Console().clear()
                return
            continue
        session.turns.append(run_turn(session, command, config, model_client))


def build_model_client(config: MiniCodeConfig):
    if not config.model_name:
        return None
    return OpenAICompatibleClient(
        model=config.model_name,
        api_key=config.model_api_key,
        base_url=config.model_base_url,
    )


def run_turn(session: ChatSession, task: str, config: MiniCodeConfig, model_client) -> ChatTurn:
    runtime = RuntimeContext.create(config.workspace, run_kind="chat")
    console = Console()
    stream = ChatRunStream(console)
    with console.status("[bold #f5c48c]MiniCode is thinking...[/bold #f5c48c]", spinner="dots"):
        result = AgentLoop(
            runtime,
            task,
            max_steps=config.max_agent_steps,
            max_failed_tool_attempts=config.max_failed_tool_attempts,
            model_client=model_client,
            aux_model_client=model_client,
            enable_skill_rerank=session.llm_rerank,
            memory_reflection_mode=session.memory_reflection_mode,
            event_callback=stream.handle,
        ).run()
    summary = result.state.task_state.history_summary or summarize_turn(result)
    failure_reason = result.state.task_state.failed_attempts[-1] if result.state.current_phase.value == "failed" and result.state.task_state.failed_attempts else None
    return ChatTurn(
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
    )


class ChatRunStream:
    def __init__(self, console: Console) -> None:
        self.console = console

    def handle(self, event_type: str, payload: dict) -> None:
        line = format_stream_event(event_type, payload)
        if line:
            self.console.print(line)


def format_stream_event(event_type: str, payload: dict) -> Text | None:
    if event_type == "phase_changed":
        phase = payload.get("phase", "")
        reason = payload.get("reason", "")
        text = Text("phase ", style="#77706a")
        text.append(str(phase), style="bold #9ad8ff")
        if reason:
            text.append(f"  {reason}", style="#cfc7b9")
        return text
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


def handle_chat_command(command: str, session: ChatSession) -> bool:
    lowered = command.strip().lower()
    if lowered in {"/exit", "/quit"}:
        return True
    if lowered == "/clear":
        session.turns.clear()
        session.notices = ["Session cleared."]
        return False
    if lowered == "/status":
        return False
    if lowered == "/help":
        session.notices = ["Shortcuts: /help shows this guide, /clear clears the session, /exit leaves MiniCode."]
        return False
    return False


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


def render_top_panel(session: ChatSession) -> Panel:
    left = render_left_column(session)
    right = render_right_column(session)
    body = Table.grid(expand=True)
    body.add_column(ratio=2)
    body.add_column(ratio=3)
    body.add_row(left, right)
    return Panel(body, box=ROUNDED, border_style="#f08f5a", padding=(1, 2), title="[bold #f5c48c]MiniCode Agent[/bold #f5c48c]")


def render_left_column(session: ChatSession) -> RenderableType:
    title = Text("MiniCode", style="bold #f5c48c")
    subtitle = Text("local coding agent runtime", style="#cfc7b9")
    return Group(Align.center(title), Align.center(subtitle), Text(), render_avatar())


def render_right_column(session: ChatSession) -> RenderableType:
    recent = Text()
    recent.append("Recent Activity\n", style="bold #f5c48c")
    if not session.turns:
        recent.append("No recent activity\n", style="#cfc7b9")
    for turn in session.turns[-4:]:
        status = "ok" if turn.final_phase == "done" else turn.final_phase
        recent.append(f"- {status}  ", style="#9ad8ff")
        recent.append(turn.prompt[:52], style="#efe8dd")
        if len(turn.prompt) > 52:
            recent.append("...", style="#efe8dd")
        recent.append(f"  ({turn.tool_calls} tools)\n", style="#b8aa73")
    tips = Text()
    tips.append("\nCommands\n", style="bold #f5c48c")
    tips.append("/help    show shortcuts\n", style="#9ad8ff")
    tips.append("/clear   clear this session\n", style="#9ad8ff")
    tips.append("/exit    leave MiniCode\n", style="#9ad8ff")
    return Group(recent, tips)


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
        Text(turn.prompt, style="#efe8dd"),
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
    text.append(f"phase: {turn.final_phase}   tools: {turn.tool_calls}   ", style="#efe8dd")
    text.append(f"skills: {', '.join(turn.selected_skills) or '(none)'}\n", style="#cfc7b9")
    text.append(turn.summary, style="#efe8dd")
    if turn.failure_reason:
        text.append(f"\nfailure: {turn.failure_reason}", style="bold #ffb3b3")
    return text


def render_bottom_panel(session: ChatSession) -> Panel:
    prompt_hint = Text()
    prompt_hint.append("Enter a task, or ", style="#cfc7b9")
    prompt_hint.append("/help", style="bold #9ad8ff")
    prompt_hint.append(" / ", style="#cfc7b9")
    prompt_hint.append("/clear", style="bold #9ad8ff")
    prompt_hint.append(" / ", style="#cfc7b9")
    prompt_hint.append("/exit", style="bold #9ad8ff")
    prompt_hint.append(" to leave.", style="#cfc7b9")
    return Panel(
        prompt_hint,
        box=ROUNDED,
        border_style="#f08f5a",
        padding=(0, 2),
        title="[bold #f5c48c]Command[/bold #f5c48c]",
    )


def input_bar() -> str:
    from rich.console import Console

    console = Console()
    rule = horizontal_rule(console.size.width)
    if console.is_terminal:
        console.print(rule)
        console.print(Text("> ", style="#f08f5a"), end="")
        console.file.write("\n")
        console.file.flush()
        console.print(rule)
        console.file.write("\x1b[2A\r\x1b[2C")
        console.file.flush()
        return read_tty_line(console)

    console.print(rule)
    value = console.input("> ").strip()
    console.print(rule)
    return value


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


def summarize_turn(result) -> str:
    for event in reversed(result.transcript):
        if event.get("event") == "agent_planned":
            payload = event.get("payload", {})
            if payload.get("stop") and payload.get("description") == "Model returned a direct answer.":
                for decision in result.state.task_state.decisions:
                    if decision != "Keep the first loop minimal and traceable.":
                        return str(decision)[:240]
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
    return result.state.task_state.decisions[-1][:160] if result.state.task_state.decisions else "No summary available."


def horizontal_rule(width: int) -> Text:
    rule_width = max(20, width - 1)
    return Text("─" * rule_width, style="#8f8b84")


def read_tty_line(console) -> str:
    import msvcrt

    buffer: list[str] = []
    while True:
        char = msvcrt.getwch()
        if char in {"\r", "\n"}:
            console.file.write("\n")
            console.file.flush()
            return "".join(buffer).strip()
        if char == "\x03":
            raise KeyboardInterrupt
        if char in {"\b", "\x7f"}:
            if buffer:
                buffer.pop()
                console.file.write("\b \b")
                console.file.flush()
            continue
        buffer.append(char)
        console.file.write(char)
        console.file.flush()
