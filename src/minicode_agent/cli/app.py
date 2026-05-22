import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from minicode_agent.agent import AgentLoop
from minicode_agent.config import MiniCodeConfig
from minicode_agent.harness import HarnessRunner, ablation_config_names, load_ablation_config_file, resolve_ablation_config, run_all_configs
from minicode_agent.memory import MemoryKind, MemoryStore, default_memory_db_path
from minicode_agent.models import OpenAICompatibleClient
from minicode_agent.skills import SkillError, SkillRegistry, SkillRouter
from minicode_agent.trace import TraceStore, default_trace_db_path
from minicode_agent.runtime import RuntimeContext
from minicode_agent.tools.executor import ToolExecutor
from minicode_agent.tools.registry import create_default_registry
from minicode_agent.tools.types import ToolContext

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
) -> None:
    """Start a single coding-agent run."""
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
        table = Table(title="MiniCode Eval Configs")
        table.add_column("Name", no_wrap=True)
        table.add_column("Skills")
        table.add_column("Memory")
        table.add_column("Compression")
        table.add_column("Subagents")
        table.add_column("Memory Mode")
        table.add_column("Description")
        for name in ablation_config_names():
            preset = resolve_ablation_config(name)
            table.add_row(
                preset.name,
                str(preset.enable_skills),
                str(preset.enable_memory),
                str(preset.enable_compression),
                str(preset.enable_subagents),
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
    table.add_column("Expected")
    table.add_column("Passed")
    table.add_column("Runtime")
    table.add_column("Tools")
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
def list_skills() -> None:
    """List built-in skills."""
    registry = SkillRegistry()
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
def show_skill(name: str = typer.Argument(..., help="Skill name to show.")) -> None:
    """Show a skill's metadata and content."""
    registry = SkillRegistry()
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
def route_skill(task: str = typer.Argument(..., help="Task text to route.")) -> None:
    """Show deterministic skill routing for a task."""
    result = SkillRouter().route(task)
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
        return
    console.print(table)


@memory_app.command("list")
def list_memory(
    workspace: Path = typer.Option(
        Path.cwd(),
        "--workspace",
        "-w",
        help="Workspace directory that contains the memory db.",
    ),
    kind: MemoryKind | None = typer.Option(None, "--kind", help="Memory kind to show."),
    limit: int = typer.Option(50, "--limit", help="Maximum records to show."),
    query: str | None = typer.Option(None, "--query", help="Search memory content and tags."),
    json_output: bool = typer.Option(False, "--json", help="Print records as JSON."),
) -> None:
    """List project memories."""
    store = MemoryStore(default_memory_db_path(workspace))
    records = store.search(query, limit=limit) if query else store.list(kind=kind, limit=limit)
    if json_output:
        console.print(json.dumps([record.model_dump() for record in records], ensure_ascii=False, indent=2), markup=False)
        return

    table = Table(title="MiniCode Memory")
    table.add_column("Kind")
    table.add_column("Confidence")
    table.add_column("Source")
    table.add_column("Reason")
    table.add_column("Content")
    for record in records:
        table.add_row(
            record.kind.value,
            f"{record.confidence:.2f}",
            record.source_run_id or "",
            record.reason or "",
            record.content,
        )
    console.print(f"[dim]memory_backend: {store.backend} ({store.db_path})[/dim]")
    console.print(table)


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
        )
    except ValueError as exc:
        console.print(f"[red]Memory rejected:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[dim]memory_backend: {store.backend} ({store.db_path})[/dim]")
    console.print("added" if inserted else "duplicate")
    console.print(f"id: {record.id}")


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
    old_text: str | None = typer.Option(None, "--old-text", help="Exact text to replace for edit_file."),
    new_text: str | None = typer.Option(None, "--new-text", help="Replacement text for edit_file."),
    max_files: int = typer.Option(200, "--max-files", help="Maximum files for list_files."),
    max_matches: int = typer.Option(100, "--max-matches", help="Maximum matches for search_code."),
    timeout_seconds: int | None = typer.Option(None, "--timeout-seconds", help="Command timeout in seconds."),
    replace_all: bool = typer.Option(False, "--replace-all", help="Replace all occurrences for edit_file."),
    create_parents: bool = typer.Option(False, "--create-parents", help="Create missing parent directories for write_file."),
    stat: bool = typer.Option(False, "--stat", help="Show git diff stat."),
    approved: bool = typer.Option(False, "--approved", help="Approve tools that require confirmation."),
) -> None:
    """Run a registered tool."""
    registry = create_default_registry()
    runtime = RuntimeContext.create(workspace)
    executor = ToolExecutor(registry, trace_store=runtime.trace_store, run_id=runtime.run_id)
    arguments = compact_arguments({
        "path": path,
        "pattern": pattern,
        "command": command,
        "argv": arg,
        "role": role,
        "task": task,
        "max_steps": subagent_max_steps,
        "content": content,
        "old_text": old_text,
        "new_text": new_text,
        "max_files": max_files if max_files != 200 else None,
        "max_matches": max_matches if max_matches != 100 else None,
        "timeout_seconds": timeout_seconds,
        "replace_all": replace_all if replace_all else None,
        "create_parents": create_parents if create_parents else None,
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
    console.print(f"[red]Tool failed:[/red] {observation.error}")
    raise typer.Exit(code=1)


def main() -> None:
    app()


def safe_console_text(text: str) -> str:
    return text.lstrip("\ufeff")


def compact_arguments(arguments: dict) -> dict:
    return {key: value for key, value in arguments.items() if value is not None}
