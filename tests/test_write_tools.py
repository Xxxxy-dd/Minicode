from typer.testing import CliRunner

from minicode_agent.cli.app import app
from minicode_agent.tools.executor import ToolExecutor
from minicode_agent.tools.registry import create_default_registry
from minicode_agent.tools.types import PermissionMode, ToolContext


def execute(name: str, workspace, arguments: dict, approved: bool = False):
    return ToolExecutor(create_default_registry()).execute(
        name,
        ToolContext(workspace=workspace),
        arguments,
        approved=approved,
    )


def test_write_file_requires_approval(tmp_path) -> None:
    observation = execute("write_file", tmp_path, {"path": "notes.txt", "content": "hello"})

    assert not observation.ok
    assert observation.metadata["permission"] == PermissionMode.ASK.value
    assert not (tmp_path / "notes.txt").exists()


def test_write_file_creates_file_after_approval(tmp_path) -> None:
    observation = execute("write_file", tmp_path, {"path": "notes.txt", "content": "hello\n"}, approved=True)

    assert observation.ok
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello\n"
    assert observation.metadata["approved"] is True
    assert observation.metadata["before_hash"] is None
    assert observation.metadata["after_hash"]


def test_write_file_requires_create_parents_for_new_directories(tmp_path) -> None:
    observation = execute(
        "write_file",
        tmp_path,
        {"path": "nested/notes.txt", "content": "hello"},
        approved=True,
    )

    assert not observation.ok
    assert "Parent directory does not exist" in observation.error
    assert not (tmp_path / "nested").exists()


def test_write_file_can_create_parents_when_explicit(tmp_path) -> None:
    observation = execute(
        "write_file",
        tmp_path,
        {"path": "nested/notes.txt", "content": "hello", "create_parents": True},
        approved=True,
    )

    assert observation.ok
    assert (tmp_path / "nested" / "notes.txt").read_text(encoding="utf-8") == "hello"


def test_write_file_blocks_workspace_escape_even_when_approved(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}_outside.txt"

    observation = execute("write_file", tmp_path, {"path": f"../{outside.name}", "content": "secret"}, approved=True)

    assert not observation.ok
    assert "escapes workspace" in observation.error
    assert not outside.exists()


def test_edit_file_replaces_exact_text_after_approval(tmp_path) -> None:
    path = tmp_path / "app.py"
    path.write_text("name = 'old'\n", encoding="utf-8")

    observation = execute(
        "edit_file",
        tmp_path,
        {"path": "app.py", "old_text": "'old'", "new_text": "'new'"},
        approved=True,
    )

    assert observation.ok
    assert path.read_text(encoding="utf-8") == "name = 'new'\n"
    assert observation.metadata["replacements"] == 1
    assert observation.metadata["before_hash"] != observation.metadata["after_hash"]


def test_edit_file_rejects_missing_old_text(tmp_path) -> None:
    path = tmp_path / "app.py"
    path.write_text("name = 'old'\n", encoding="utf-8")

    observation = execute(
        "edit_file",
        tmp_path,
        {"path": "app.py", "old_text": "'missing'", "new_text": "'new'"},
        approved=True,
    )

    assert not observation.ok
    assert "not found" in observation.error
    assert path.read_text(encoding="utf-8") == "name = 'old'\n"


def test_edit_file_rejects_ambiguous_replacement_without_replace_all(tmp_path) -> None:
    path = tmp_path / "app.py"
    path.write_text("value = 1\nvalue = 1\n", encoding="utf-8")

    observation = execute(
        "edit_file",
        tmp_path,
        {"path": "app.py", "old_text": "value = 1", "new_text": "value = 2"},
        approved=True,
    )

    assert not observation.ok
    assert "multiple times" in observation.error
    assert path.read_text(encoding="utf-8") == "value = 1\nvalue = 1\n"


def test_edit_file_replace_all(tmp_path) -> None:
    path = tmp_path / "app.py"
    path.write_text("value = 1\nvalue = 1\n", encoding="utf-8")

    observation = execute(
        "edit_file",
        tmp_path,
        {"path": "app.py", "old_text": "value = 1", "new_text": "value = 2", "replace_all": True},
        approved=True,
    )

    assert observation.ok
    assert observation.metadata["replacements"] == 2
    assert path.read_text(encoding="utf-8") == "value = 2\nvalue = 2\n"


def test_cli_write_file_requires_approved_flag(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        ["tools", "run", "write_file", "--workspace", str(tmp_path), "--path", "notes.txt", "--content", "hello"],
    )

    assert result.exit_code == 1
    assert "requires approval" in result.output
    assert not (tmp_path / "notes.txt").exists()


def test_cli_write_file_with_approved_flag(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "tools",
            "run",
            "write_file",
            "--workspace",
            str(tmp_path),
            "--path",
            "notes.txt",
            "--content",
            "hello",
            "--approved",
        ],
    )

    assert result.exit_code == 0
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello"


def test_cli_write_file_create_parents(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "tools",
            "run",
            "write_file",
            "--workspace",
            str(tmp_path),
            "--path",
            "nested/notes.txt",
            "--content",
            "hello",
            "--create-parents",
            "--approved",
        ],
    )

    assert result.exit_code == 0
    assert (tmp_path / "nested" / "notes.txt").read_text(encoding="utf-8") == "hello"
