import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from minicode_agent.agent import AgentLoop
from minicode_agent.cli.preview_renderer import render_preview_text
from minicode_agent.cli.renderers import render_memory_record
from minicode_agent.config import MiniCodeConfig, normalize_memory_reflection_mode
from minicode_agent.harness import HarnessRunner, ablation_config_names, load_ablation_config_file, resolve_ablation_config, run_all_configs
from minicode_agent.memory import MemoryKind, MemoryStatus, MemoryStore, default_memory_db_path
from minicode_agent.models import OpenAICompatibleClient
from minicode_agent.skills import SkillError, SkillRegistry, SkillRouter, default_skill_registry
from minicode_agent.trace import TraceStore, default_trace_db_path
from minicode_agent.runtime import RuntimeContext
from minicode_agent.tools.executor import ToolExecutor
from minicode_agent.tools.registry import create_default_registry
from minicode_agent.tools.types import ToolContext
from minicode_agent.cli.live_ui import run_chat_session

app = typer.Typer(
    name="minicode",
    help="MiniCode Agent local coding runtime.",
    no_args_is_help=True,
)
console = Console()
tools_app = typer.Typer(help="Inspect and run MiniCode tools.")
skills_app = typer.Typer(help="Inspect MiniCode skills.")
memory_app = typer.Typer(help="Inspect and update MiniCode memory.")
app.add_typer(tools_app, name="tools")
app.add_typer(skills_app, name="skills")
app.add_typer(memory_app, name="memory")


@app.command()
def run(
    task: str = typer.Argument(..., help="Coding task to run in the workspace."),
    workspace: Path = typer.Option(
        Path.cwd(),
        "--workspace",
        "-w",
        help="Workspace directory for the agent run.",
    ),
    model: str | None = typer.Option(None, "--model", help="OpenAI-compatible model name to use."),
    model_base_url: str | None = typer.Option(None, "--model-base-url", help="OpenAI-compatible API base URL."),
    no_model: bool = typer.Option(False, "--no-model", help="Use the deterministic rule planner even if model env vars are set."),
    llm_rerank: bool = typer.Option(False, "--llm-rerank", help="Use the auxiliary model to rerank skill candidates."),
    memory_reflection_mode: str = typer.Option(
        "deterministic",
        "--memory-reflection-mode",
        help="Memory reflection mode: deterministic or llm.",
    ),
) -> None:
    """Start a single coding-agent run."""
    try:
        memory_reflection_mode = normalize_memory_reflection_mode(memory_reflection_mode)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

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
    runtime = RuntimeContext.create(config.workspace, run_kind="agent")
    model_client = None
    if config.model_name:
        model_client = OpenAICompatibleClient(
            model=config.model_name,
            api_key=config.model_api_key,
            base_url=config.model_base_url,
        )
    result = AgentLoop(
        runtime,
        task,
        max_steps=config.max_agent_steps,
        max_failed_tool_attempts=config.max_failed_tool_attempts,
        model_client=model_client,
        aux_model_client=model_client,
        enable_skill_rerank=llm_rerank,
        memory_reflection_mode=memory_reflection_mode,
    ).run()
    console.print("[bold cyan]MiniCode Agent[/bold cyan]")
    console.print(f"run_id: {runtime.run_id}")
    console.print(f"trace_backend: {runtime.trace_store.backend} ({runtime.trace_store.storage_path})")
    console.print(f"Workspace: {config.workspace}")
    console.print(f"Task: {task}")
    console.print(f"Planner: {'model' if model_client else 'rules'}")
    console.print(f"Final phase: {result.state.current_phase.value}")
    console.print(f"Selected skills: {', '.join(result.state.selected_skills) or '(none)'}")
    console.print(f"Tool calls: {result.state.metrics.tool_calls}")


@app.command()
def chat(
    task: str | None = typer.Argument(None, help="Optional initial task to run in the session."),
    workspace: Path = typer.Option(
        Path.cwd(),
        "--workspace",
        "-w",
        help="Workspace directory for the chat session.",
    ),
    model: str | None = typer.Option(None, "--model", help="OpenAI-compatible model name to use."),
    model_base_url: str | None = typer.Option(None, "--model-base-url", help="OpenAI-compatible API base URL."),
    no_model: bool = typer.Option(False, "--no-model", help="Use the deterministic rule planner even if model env vars are set."),
    llm_rerank: bool = typer.Option(False, "--llm-rerank", help="Use the auxiliary model to rerank skill candidates."),
    memory_reflection_mode: str = typer.Option(
        "deterministic",
        "--memory-reflection-mode",
        help="Memory reflection mode: deterministic or llm.",
    ),
    preview: bool = typer.Option(False, "--preview", help="Render one screen and exit without entering interactive mode."),
) -> None:
    """Open the Claude-like interactive CLI layout."""
    run_chat_session(
        task,
        workspace=workspace,
        model=model,
        model_base_url=model_base_url,
        no_model=no_model,
        llm_rerank=llm_rerank,
        memory_reflection_mode=memory_reflection_mode,
        preview=preview,
    )


@app.command()
def eval(
    taskset: Path = typer.Argument(..., help="Path to a benchmark task JSON file or directory."),
    workspace: Path = typer.Option(
        Path.cwd(),
        "--workspace",
        "-w",
        help="Root directory for resolving task paths and writing reports.",
    ),
    config: str = typer.Option("default", "--config", help="Evaluation config name, or 'all' for Day 16 ablations."),
    config_file: Path | None = typer.Option(None, "--config-file", help="Path to a custom ablation config JSON file."),
    list_configs: bool = typer.Option(False, "--list-configs", help="List built-in eval configs and exit."),
) -> None:
    """Run the lightweight harness against a task set."""
    if list_configs:
        table = Table(title="MiniCode Eval Configs", expand=False)
        table.add_column("Name", no_wrap=True)
        table.add_column("Features")
        table.add_column("Memory Mode", no_wrap=True)
        table.add_column("Description")
        for name in ablation_config_names():
            preset = resolve_ablation_config(name)
            features = []
            if preset.enable_skills:
                features.append("skills")
            if preset.enable_skill_rerank:
                features.append("rerank")
            if preset.enable_memory:
                features.append("memory")
            if preset.enable_compression:
                features.append("compression")
            if preset.enable_subagents:
                features.append("subagents")
            table.add_row(
                preset.name,
                ", ".join(features) or "none",
                preset.memory_reflection_mode,
                preset.description,
            )
        console.print(table)
        return
    if config_file:
        custom_config = load_ablation_config_file(config_file)
        runner = HarnessRunner(workspace, ablation_config=custom_config)
        results, report_path = runner.run(taskset)
    elif config == "all":
        results, report_path = run_all_configs(workspace, taskset)
    else:
        runner = HarnessRunner(workspace, config=config)
        results, report_path = runner.run(taskset)
    table = Table(title="MiniCode Eval")
    table.add_column("Config", no_wrap=True)
    table.add_column("Task", no_wrap=True)
    table.add_column("Expected", no_wrap=True)
    table.add_column("Passed", no_wrap=True)
    table.add_column("Runtime", no_wrap=True)
    table.add_column("Tools", no_wrap=True)
    for result in results:
        table.add_row(
            result.config,
            result.task_id,
            result.expected.value,
            "yes" if result.passed else "no",
            f"{result.runtime_seconds:.3f}s",
            str(result.metrics.get("tool_calls", 0)),
        )
    console.print(table)
    console.print(f"[dim]report: {report_path}[/dim]")


@app.command()
def trace(
    run_id: str | None = typer.Argument(None, help="Run id to inspect. Omit to show recent events."),
    workspace: Path = typer.Option(
        Path.cwd(),
        "--workspace",
        "-w",
        help="Workspace directory that contains the trace db.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print full trace events as JSON."),
) -> None:
    """Inspect a recorded execution trace."""
    store = TraceStore(default_trace_db_path(workspace))
    events = store.list_events(run_id)
    if json_output:
        console.print(
            json.dumps([event.model_dump() for event in events], ensure_ascii=False, indent=2),
            markup=False,
        )
        return

    table = Table(title="MiniCode Trace")
    table.add_column("Time")
    table.add_column("Run")
    table.add_column("Event")
    table.add_column("Tool")
    table.add_column("OK")
    table.add_column("Reason")
    for event in events[-50:]:
        tool = event.payload.get("tool") or event.payload.get("metadata", {}).get("tool", "")
        ok = str(event.payload.get("ok", ""))
        reason = event.payload.get("reason") or event.payload.get("error") or event.payload.get("metadata", {}).get("permission_reason", "")
        if event.event_type == "context_compressed":
            tool = "context"
            ok = f"ratio={event.payload.get('ratio')}"
            reason = f"fb={event.payload.get('fallback_used')}"
        table.add_row(
            event.timestamp,
            event.run_id,
            event.event_type,
            str(tool),
            ok,
            str(reason),
        )
    console.print(f"[dim]trace_backend: {store.backend} ({store.storage_path})[/dim]")
    console.print(table)


@tools_app.command("list")
def list_tools() -> None:
    """List registered tools."""
    registry = create_default_registry()
    table = Table(title="MiniCode Tools")
    table.add_column("Name")
    table.add_column("Risk")
    table.add_column("Permission")
    table.add_column("Description")
    for tool in registry.list():
        table.add_row(
            tool.spec.name,
            tool.spec.risk_level.value,
            tool.spec.permission.value,
            tool.spec.description,
        )
    console.print(table)


@skills_app.command("list")
def list_skills(
    workspace: Path = typer.Option(Path.cwd(), "--workspace", "-w", help="Workspace directory for local .minicode skills."),
) -> None:
    """List available skills."""
    registry = default_skill_registry(workspace)
    table = Table(title="MiniCode Skills")
    table.add_column("Name")
    table.add_column("Tags")
    table.add_column("Description")
    for skill in registry.list():
        table.add_row(
            skill.name,
            ", ".join(skill.metadata.tags),
            skill.metadata.description,
        )
    console.print(table)


@skills_app.command("show")
def show_skill(
    name: str = typer.Argument(..., help="Skill name to show."),
    workspace: Path = typer.Option(Path.cwd(), "--workspace", "-w", help="Workspace directory for local .minicode skills."),
) -> None:
    """Show a skill's metadata and content."""
    registry = default_skill_registry(workspace)
    try:
        skill = registry.get(name)
    except SkillError as exc:
        console.print(f"[red]Skill failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[bold cyan]{skill.name}[/bold cyan]")
    console.print(f"Description: {skill.metadata.description}")
    console.print(f"Tags: {', '.join(skill.metadata.tags)}")
    console.print(f"Applies to: {', '.join(skill.metadata.applies_to)}")
    console.print()
    console.print(skill.content, markup=False)


@skills_app.command("route")
def route_skill(
    task: str = typer.Argument(..., help="Task text to route."),
    workspace: Path = typer.Option(Path.cwd(), "--workspace", "-w", help="Workspace directory for local .minicode skills."),
    llm_rerank: bool = typer.Option(False, "--llm-rerank", help="Use the configured model to rerank candidate skills."),
) -> None:
    """Show deterministic skill routing for a task."""
    model_client = build_cli_model_client(workspace) if llm_rerank else None
    result = SkillRouter(default_skill_registry(workspace), model_client=model_client, enable_llm_rerank=llm_rerank).route(task)
    table = Table(title="MiniCode Skill Route")
    table.add_column("Selected")
    table.add_column("Skill")
    table.add_column("Score")
    table.add_column("Reasons")
    for candidate in result.candidates:
        table.add_row(
            "*" if candidate.name in result.selected else "",
            candidate.name,
            str(candidate.score),
            "; ".join(candidate.reasons),
        )
    if not result.candidates:
        console.print("[dim]No matching skills.[/dim]")
        console.print(f"[dim]{result.debug_summary}[/dim]")
        return
    console.print(table)
    console.print(f"[dim]{result.debug_summary}[/dim]")
    if result.rerank_used:
        fallback = " fallback" if result.rerank_fallback else ""
        console.print(f"[dim]rerank{fallback}: {result.rerank_reason or 'completed'}[/dim]")
    elif result.rerank_skipped_reason:
        console.print(f"[dim]rerank skipped: {result.rerank_skipped_reason}[/dim]")


def build_cli_model_client(workspace: Path):
    config = MiniCodeConfig.from_env(workspace)
    if not config.model_name:
        return None
    return OpenAICompatibleClient(
        model=config.model_name,
        api_key=config.model_api_key,
        base_url=config.model_base_url,
    )


@memory_app.command("list")
def list_memory(
    workspace: Path = typer.Option(
        Path.cwd(),
        "--workspace",
        "-w",
        help="Workspace directory that contains the memory db.",
    ),
    kind: MemoryKind | None = typer.Option(None, "--kind", help="Memory kind to show."),
    status: MemoryStatus | None = typer.Option(None, "--status", help="Memory status to show."),
    tag: list[str] | None = typer.Option(None, "--tag", help="Filter by tag. Repeat for multiple tags."),
    include_stale: bool = typer.Option(False, "--include-stale", help="Include stale records in search/list output."),
    limit: int = typer.Option(50, "--limit", help="Maximum records to show."),
    query: str | None = typer.Option(None, "--query", help="Search memory content and tags."),
    json_output: bool = typer.Option(False, "--json", help="Print records as JSON."),
) -> None:
    """List project memories."""
    store = MemoryStore(default_memory_db_path(workspace))
    records = (
        store.search(query, limit=limit, kind=kind, status=status, tags=tag, include_stale=include_stale)
        if query
        else store.list(kind=kind, status=status, include_stale=include_stale, limit=limit)
    )
    if tag and not query:
        wanted = {item.lower() for item in tag}
        records = [record for record in records if wanted <= {item.lower() for item in record.tags}]
    if json_output:
        console.print(json.dumps([record.model_dump() for record in records], ensure_ascii=False, indent=2), markup=False)
        return

    console.print(f"[dim]memory_backend: {store.backend} ({store.db_path})[/dim]")
    if not records:
        console.print("[dim](no memories)[/dim]")
        return
    for record in records:
        console.print(format_memory_record(record), markup=False)


@memory_app.command("add")
def add_memory(
    content: str = typer.Argument(..., help="Memory content to store."),
    workspace: Path = typer.Option(
        Path.cwd(),
        "--workspace",
        "-w",
        help="Workspace directory that contains the memory db.",
    ),
    kind: MemoryKind = typer.Option(MemoryKind.PROJECT, "--kind", help="Memory kind."),
    confidence: float = typer.Option(0.8, "--confidence", help="Confidence from 0 to 1."),
    source_run_id: str | None = typer.Option(None, "--source-run-id", help="Run id that produced this memory."),
    tag: list[str] | None = typer.Option(None, "--tag", help="Tag for the memory. Repeat for multiple tags."),
    reason: str | None = typer.Option(None, "--reason", help="Admission/source reason for the memory."),
) -> None:
    """Add a memory record if it is admissible and not a duplicate."""
    store = MemoryStore(default_memory_db_path(workspace))
    try:
        record, inserted = store.add(
            kind,
            content,
            confidence=confidence,
            source_run_id=source_run_id,
            tags=tag or [],
            reason=reason or "manual memory add",
            admission_reason="manual add",
        )
    except ValueError as exc:
        console.print(f"[red]Memory rejected:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[dim]memory_backend: {store.backend} ({store.db_path})[/dim]")
    console.print("added" if inserted else "duplicate")
    console.print(f"id: {record.id}")


@memory_app.command("stale")
def stale_memory(
    memory_id: str = typer.Argument(..., help="Memory id to mark stale."),
    workspace: Path = typer.Option(
        Path.cwd(),
        "--workspace",
        "-w",
        help="Workspace directory that contains the memory db.",
    ),
    reason: str | None = typer.Option(None, "--reason", help="Reason this memory is stale."),
) -> None:
    """Mark a memory record as stale without deleting it."""
    store = MemoryStore(default_memory_db_path(workspace))
    if not store.mark_status(memory_id, MemoryStatus.STALE, reason=reason or "manual stale mark"):
        console.print(f"[red]Memory not found:[/red] {memory_id}")
        raise typer.Exit(code=1)
    console.print(f"[dim]memory_backend: {store.backend} ({store.db_path})[/dim]")
    console.print(f"stale: {memory_id}")


@memory_app.command("delete")
def delete_memory(
    memory_id: str = typer.Argument(..., help="Memory id to delete."),
    workspace: Path = typer.Option(
        Path.cwd(),
        "--workspace",
        "-w",
        help="Workspace directory that contains the memory db.",
    ),
) -> None:
    """Delete a memory record by id."""
    store = MemoryStore(default_memory_db_path(workspace))
    deleted = store.delete(memory_id)
    if not deleted:
        console.print(f"[red]Memory not found:[/red] {memory_id}")
        raise typer.Exit(code=1)
    console.print(f"[dim]memory_backend: {store.backend} ({store.db_path})[/dim]")
    console.print(f"deleted: {memory_id}")


@tools_app.command("run")
def run_tool(
    name: str = typer.Argument(..., help="Tool name to run."),
    workspace: Path = typer.Option(
        Path.cwd(),
        "--workspace",
        "-w",
        help="Workspace directory for the tool run.",
    ),
    path: str | None = typer.Option(None, "--path", help="Relative path argument for file tools."),
    pattern: str | None = typer.Option(None, "--pattern", help="Search pattern for search_code."),
    command: str | None = typer.Option(None, "--command", help="Command for run_shell or run_tests."),
    arg: list[str] | None = typer.Option(None, "--arg", help="Command argv item. Repeat for each argument."),
    role: str | None = typer.Option(None, "--role", help="Role for spawn_subagent."),
    task: str | None = typer.Option(None, "--task", help="Task for spawn_subagent."),
    subagent_max_steps: int | None = typer.Option(None, "--subagent-max-steps", help="Max steps for spawn_subagent."),
    content: str | None = typer.Option(None, "--content", help="Content for write_file."),
    patch: str | None = typer.Option(None, "--patch", help="Unified diff content for apply_patch."),
    patch_file: Path | None = typer.Option(None, "--patch-file", help="Read unified diff content for apply_patch from a file."),
    old_text: str | None = typer.Option(None, "--old-text", help="Exact text to replace for edit_file."),
    new_text: str | None = typer.Option(None, "--new-text", help="Replacement text for edit_file."),
    append_format: str | None = typer.Option(None, "--append-format", help="Append format: auto, raw, text, markdown, code, json, csv, toml, or yaml."),
    append_strategy: str | None = typer.Option(None, "--append-strategy", help="Append strategy: auto, text, line, paragraph, or raw."),
    separator: str | None = typer.Option(None, "--separator", help="Explicit separator for append_file."),
    overwrite: bool = typer.Option(True, "--overwrite/--no-overwrite", help="Allow write_file to replace existing content."),
    max_files: int = typer.Option(200, "--max-files", help="Maximum files for list_files."),
    max_matches: int = typer.Option(100, "--max-matches", help="Maximum matches for search_code."),
    timeout_seconds: int | None = typer.Option(None, "--timeout-seconds", help="Command timeout in seconds."),
    replace_all: bool = typer.Option(False, "--replace-all", help="Replace all occurrences for edit_file."),
    create_parents: bool = typer.Option(False, "--create-parents", help="Create missing parent directories for write_file."),
    missing_ok: bool = typer.Option(False, "--missing-ok", help="Do not fail when delete_file target is already missing."),
    stat: bool = typer.Option(False, "--stat", help="Show git diff stat."),
    approved: bool = typer.Option(False, "--approved", help="Approve tools that require confirmation."),
) -> None:
    """Run a registered tool."""
    registry = create_default_registry()
    runtime = RuntimeContext.create(workspace)
    executor = ToolExecutor(registry, trace_store=runtime.trace_store, run_id=runtime.run_id)
    patch_content = read_patch_file(patch_file) if patch_file else patch
    arguments = compact_arguments({
        "path": path,
        "pattern": pattern,
        "command": command,
        "argv": arg,
        "role": role,
        "task": task,
        "max_steps": subagent_max_steps,
        "content": content,
        "patch": patch_content,
        "old_text": old_text,
        "new_text": new_text,
        "append_format": append_format,
        "append_strategy": append_strategy,
        "separator": separator,
        "overwrite": overwrite if not overwrite else None,
        "max_files": max_files if max_files != 200 else None,
        "max_matches": max_matches if max_matches != 100 else None,
        "timeout_seconds": timeout_seconds,
        "replace_all": replace_all if replace_all else None,
        "create_parents": create_parents if create_parents else None,
        "missing_ok": missing_ok if missing_ok else None,
        "stat": stat if stat else None,
    })
    observation = executor.execute(name, ToolContext(workspace=workspace), arguments, approved=approved)
    if observation.ok:
        console.print(f"[dim]run_id: {runtime.run_id}[/dim]")
        console.print(f"[dim]trace_backend: {runtime.trace_store.backend} ({runtime.trace_store.storage_path})[/dim]")
        if observation.output:
            console.print(safe_console_text(observation.output), markup=False)
        else:
            console.print("[dim](empty output)[/dim]")
        return
    preview = observation.metadata.get("preview")
    if isinstance(preview, dict):
        console.print(safe_console_text(render_preview_text(preview)), markup=False)
    console.print(f"[red]Tool failed:[/red] {observation.error}")
    raise typer.Exit(code=1)


def main() -> None:
    app()


def safe_console_text(text: str) -> str:
    return text.lstrip("\ufeff")


def summarize_metadata(metadata: dict) -> str:
    if not metadata:
        return ""
    pieces = []
    for key in ("rule", "path", "status_reason", "conflict_notes"):
        value = metadata.get(key)
        if value:
            pieces.append(f"{key}={value}")
    if not pieces:
        pieces = [f"{key}={value}" for key, value in list(metadata.items())[:2]]
    text = "; ".join(str(piece) for piece in pieces)
    return text[:120]


def format_memory_record(record) -> str:
    return render_memory_record(record)


def read_patch_file(path: Path) -> str:
    try:
        return path.expanduser().read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise typer.BadParameter(f"Could not read patch file: {exc}") from exc


def compact_arguments(arguments: dict) -> dict:
    return {key: value for key, value in arguments.items() if value is not None}
