import subprocess

from typer.testing import CliRunner

from minicode_agent.cli.app import app
from minicode_agent.tools.registry import create_default_registry
from minicode_agent.tools.types import ToolContext


def run_tool(name: str, workspace, arguments: dict | None = None):
    registry = create_default_registry()
    return registry.get(name).run(ToolContext(workspace=workspace), arguments or {})


def test_registry_lists_default_tools() -> None:
    registry = create_default_registry()
    names = {tool.spec.name for tool in registry.list()}

    assert {"list_files", "read_file", "search_code", "git_status", "git_diff"} <= names


def test_list_files_reads_workspace_tree(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")

    observation = run_tool("list_files", tmp_path)

    assert observation.ok
    assert "src/" in observation.output
    assert "src/app.py" in observation.output


def test_read_file_returns_text(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Hello\n", encoding="utf-8")

    observation = run_tool("read_file", tmp_path, {"path": "README.md"})

    assert observation.ok
    assert observation.output == "# Hello\n"


def test_read_file_blocks_workspace_escape(tmp_path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    observation = run_tool("read_file", tmp_path, {"path": "../outside.txt"})

    assert not observation.ok
    assert "escapes workspace" in observation.error


def test_search_code_finds_pattern(tmp_path) -> None:
    (tmp_path / "app.py").write_text("def target_function():\n    pass\n", encoding="utf-8")

    observation = run_tool("search_code", tmp_path, {"pattern": "target_function"})

    assert observation.ok
    assert "app.py:1" in observation.output


def test_git_tools_return_status_and_diff(tmp_path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "tracked.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "tracked.txt").write_text("hello again\n", encoding="utf-8")

    status = run_tool("git_status", tmp_path)
    diff = run_tool("git_diff", tmp_path)

    assert status.ok
    assert "M tracked.txt" in status.output
    assert diff.ok
    assert "hello again" in diff.output


def test_cli_tools_list() -> None:
    result = CliRunner().invoke(app, ["tools", "list"])

    assert result.exit_code == 0
    assert "read_file" in result.output


def test_cli_read_file(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# CLI\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["tools", "run", "read_file", "--workspace", str(tmp_path), "--path", "README.md"],
    )

    assert result.exit_code == 0
    assert "# CLI" in result.output
