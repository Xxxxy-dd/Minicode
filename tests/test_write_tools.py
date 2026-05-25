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


def test_write_file_overwrites_existing_file(tmp_path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("old\n", encoding="utf-8")

    observation = execute("write_file", tmp_path, {"path": "notes.txt", "content": "new\n"}, approved=True)

    assert observation.ok
    assert path.read_text(encoding="utf-8") == "new\n"
    assert observation.metadata["created"] is False


def test_append_file_adds_content_without_overwriting(tmp_path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("first\n", encoding="utf-8")

    observation = execute("append_file", tmp_path, {"path": "notes.txt", "content": "second\n"}, approved=True)

    assert observation.ok
    assert path.read_text(encoding="utf-8") == "first\n\nsecond\n"
    assert observation.metadata["appended_chars"] == len("second\n")


def test_append_file_inserts_text_paragraph_spacing(tmp_path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("first paragraph", encoding="utf-8")

    observation = execute("append_file", tmp_path, {"path": "notes.txt", "content": "second paragraph"}, approved=True)

    assert observation.ok
    assert path.read_text(encoding="utf-8") == "first paragraph\n\nsecond paragraph\n"
    assert observation.metadata["append_format"] == "text"


def test_append_file_inserts_code_newline(tmp_path) -> None:
    path = tmp_path / "app.py"
    path.write_text("def one():\n    return 1", encoding="utf-8")

    observation = execute("append_file", tmp_path, {"path": "app.py", "content": "def two():\n    return 2"}, approved=True)

    assert observation.ok
    assert path.read_text(encoding="utf-8") == "def one():\n    return 1\ndef two():\n    return 2\n"
    assert observation.metadata["append_format"] == "code"


def test_append_file_merges_json_arrays(tmp_path) -> None:
    path = tmp_path / "data.json"
    path.write_text('[{"a": 1}]\n', encoding="utf-8")

    observation = execute("append_file", tmp_path, {"path": "data.json", "content": '{"b": 2}'}, approved=True)

    assert observation.ok
    assert path.read_text(encoding="utf-8") == '[\n  {\n    "a": 1\n  },\n  {\n    "b": 2\n  }\n]\n'
    assert observation.metadata["append_format"] == "json"


def test_append_file_merges_json_objects(tmp_path) -> None:
    path = tmp_path / "data.json"
    path.write_text('{"a": 1}\n', encoding="utf-8")

    observation = execute("append_file", tmp_path, {"path": "data.json", "content": '{"b": 2}'}, approved=True)

    assert observation.ok
    assert path.read_text(encoding="utf-8") == '{\n  "a": 1,\n  "b": 2\n}\n'


def test_append_file_rejects_invalid_json_without_writing(tmp_path) -> None:
    path = tmp_path / "data.json"
    path.write_text('{"a": 1}\n', encoding="utf-8")

    observation = execute("append_file", tmp_path, {"path": "data.json", "content": "not json"}, approved=True)

    assert not observation.ok
    assert "Invalid JSON" in observation.error
    assert path.read_text(encoding="utf-8") == '{"a": 1}\n'


def test_append_file_appends_csv_rows(tmp_path) -> None:
    path = tmp_path / "data.csv"
    path.write_text("name,count\nalpha,1\n", encoding="utf-8")

    observation = execute("append_file", tmp_path, {"path": "data.csv", "content": "beta,2\n"}, approved=True)

    assert observation.ok
    assert path.read_text(encoding="utf-8") == "name,count\nalpha,1\nbeta,2\n"
    assert observation.metadata["append_format"] == "csv"


def test_append_file_rejects_csv_column_mismatch_without_writing(tmp_path) -> None:
    path = tmp_path / "data.csv"
    path.write_text("name,count\nalpha,1\n", encoding="utf-8")

    observation = execute("append_file", tmp_path, {"path": "data.csv", "content": "beta,2,extra\n"}, approved=True)

    assert not observation.ok
    assert "matching column counts" in observation.error
    assert path.read_text(encoding="utf-8") == "name,count\nalpha,1\n"


def test_append_file_appends_valid_toml_block(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[server]\nport = 8000\n", encoding="utf-8")

    observation = execute("append_file", tmp_path, {"path": "config.toml", "content": "[client]\ntimeout = 30"}, approved=True)

    assert observation.ok
    assert path.read_text(encoding="utf-8") == "[server]\nport = 8000\n\n[client]\ntimeout = 30\n"
    assert observation.metadata["append_format"] == "toml"


def test_append_file_rejects_invalid_toml_without_writing(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[server]\nport = 8000\n", encoding="utf-8")

    observation = execute("append_file", tmp_path, {"path": "config.toml", "content": "bad ="}, approved=True)

    assert not observation.ok
    assert "Invalid TOML" in observation.error
    assert path.read_text(encoding="utf-8") == "[server]\nport = 8000\n"


def test_append_file_appends_yaml_with_block_spacing(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("server:\n  port: 8000\n", encoding="utf-8")

    observation = execute("append_file", tmp_path, {"path": "config.yaml", "content": "client:\n  timeout: 30"}, approved=True)

    assert observation.ok
    assert path.read_text(encoding="utf-8") == "server:\n  port: 8000\n\nclient:\n  timeout: 30\n"
    assert observation.metadata["append_format"] == "yaml"


def test_append_file_can_use_raw_format(tmp_path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("first", encoding="utf-8")

    observation = execute("append_file", tmp_path, {"path": "notes.txt", "content": "second", "append_format": "raw"}, approved=True)

    assert observation.ok
    assert path.read_text(encoding="utf-8") == "firstsecond"


def test_append_file_creates_missing_file_after_approval(tmp_path) -> None:
    observation = execute("append_file", tmp_path, {"path": "notes.txt", "content": "hello\n"}, approved=True)

    assert observation.ok
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello\n"
    assert observation.metadata["created"] is True


def test_create_file_fails_if_target_exists(tmp_path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("old\n", encoding="utf-8")

    observation = execute("create_file", tmp_path, {"path": "notes.txt", "content": "new\n"}, approved=True)

    assert not observation.ok
    assert "already exists" in observation.error
    assert path.read_text(encoding="utf-8") == "old\n"


def test_create_file_creates_new_file(tmp_path) -> None:
    observation = execute("create_file", tmp_path, {"path": "notes.txt", "content": "hello\n"}, approved=True)

    assert observation.ok
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello\n"


def test_delete_file_removes_existing_file_after_approval(tmp_path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("delete me\n", encoding="utf-8")

    observation = execute("delete_file", tmp_path, {"path": "notes.txt"}, approved=True)

    assert observation.ok
    assert not path.exists()
    assert observation.metadata["deleted"] is True


def test_delete_file_requires_approval(tmp_path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("delete me\n", encoding="utf-8")

    observation = execute("delete_file", tmp_path, {"path": "notes.txt"})

    assert not observation.ok
    assert observation.metadata["permission"] == PermissionMode.ASK.value
    assert path.exists()


def test_delete_file_missing_ok(tmp_path) -> None:
    observation = execute("delete_file", tmp_path, {"path": "missing.txt", "missing_ok": True}, approved=True)

    assert observation.ok
    assert observation.metadata["deleted"] is False


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


def test_cli_append_file(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("first\n", encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "tools",
            "run",
            "append_file",
            "--workspace",
            str(tmp_path),
            "--path",
            "notes.txt",
            "--content",
            "second\n",
            "--approved",
        ],
    )

    assert result.exit_code == 0
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "first\n\nsecond\n"


def test_cli_delete_file_missing_ok(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "tools",
            "run",
            "delete_file",
            "--workspace",
            str(tmp_path),
            "--path",
            "missing.txt",
            "--missing-ok",
            "--approved",
        ],
    )

    assert result.exit_code == 0
