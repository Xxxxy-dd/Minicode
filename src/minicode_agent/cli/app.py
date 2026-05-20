from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from minicode_agent.config import MiniCodeConfig
from minicode_agent.tools.registry import create_default_registry
from minicode_agent.tools.types import ToolContext

app = typer.Typer(
    name="minicode",
    help="MiniCode Agent local coding runtime.",
    no_args_is_help=True,
)
console = Console()
tools_app = typer.Typer(help="Inspect and run MiniCode tools.")
app.add_typer(tools_app, name="tools")


@app.command()
def run(
    task: str = typer.Argument(..., help="Coding task to run in the workspace."),
    workspace: Path = typer.Option(
        Path.cwd(),
        "--workspace",
        "-w",
        help="Workspace directory for the agent run.",
    ),
) -> None:
    """Start a single coding-agent run."""
    config = MiniCodeConfig(workspace=workspace)
    console.print("[bold cyan]MiniCode Agent[/bold cyan]")
    console.print(f"Workspace: {config.workspace}")
    console.print(f"Task: {task}")
    console.print("[yellow]Agent loop is not implemented yet.[/yellow]")


@app.command()
def eval(taskset: Path = typer.Argument(..., help="Path to a benchmark task set.")) -> None:
    """Run the lightweight harness against a task set."""
    console.print(f"[yellow]Harness is not implemented yet:[/yellow] {taskset}")


@app.command()
def trace(run_id: str = typer.Argument(..., help="Run id to inspect.")) -> None:
    """Inspect a recorded execution trace."""
    console.print(f"[yellow]Trace inspection is not implemented yet:[/yellow] {run_id}")


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
    max_files: int = typer.Option(200, "--max-files", help="Maximum files for list_files."),
    max_matches: int = typer.Option(100, "--max-matches", help="Maximum matches for search_code."),
    stat: bool = typer.Option(False, "--stat", help="Show git diff stat."),
) -> None:
    """Run a registered read-only tool."""
    registry = create_default_registry()
    tool = registry.get(name)
    arguments = {
        "path": path,
        "pattern": pattern,
        "max_files": max_files,
        "max_matches": max_matches,
        "stat": stat,
    }
    observation = tool.run(ToolContext(workspace=workspace), arguments)
    if observation.ok:
        if observation.output:
            console.print(observation.output)
        else:
            console.print("[dim](empty output)[/dim]")
        return
    console.print(f"[red]Tool failed:[/red] {observation.error}")
    raise typer.Exit(code=1)


def main() -> None:
    app()
