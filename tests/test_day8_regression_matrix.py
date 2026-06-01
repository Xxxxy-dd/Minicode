import sys

from typer.testing import CliRunner

from minicode_agent.cli.app import app


def test_day8_memory_cli_status_matrix(tmp_path) -> None:
    runner = CliRunner()

    add_result = runner.invoke(
        app,
        [
            "memory",
            "add",
            "Use focused tests before full regression.",
            "--workspace",
            str(tmp_path),
            "--tag",
            "day8",
        ],
    )
    memory_id = next(line.split(": ", 1)[1] for line in add_result.output.splitlines() if line.startswith("id: "))
    stale_result = runner.invoke(app, ["memory", "stale", memory_id, "--workspace", str(tmp_path), "--reason", "covered"])
    active_result = runner.invoke(app, ["memory", "list", "--workspace", str(tmp_path), "--tag", "day8"])
    stale_list = runner.invoke(app, ["memory", "list", "--workspace", str(tmp_path), "--status", "stale", "--tag", "day8"])

    assert add_result.exit_code == 0, add_result.output
    assert stale_result.exit_code == 0, stale_result.output
    assert "Use focused tests" not in active_result.output
    assert "Use focused tests" in stale_list.output
    assert "status_reason=covered" in stale_list.output


def test_day8_tool_cli_patch_file_and_argv_matrix(tmp_path) -> None:
    notes = tmp_path / "notes.txt"
    patch_file = tmp_path / "change.diff"
    notes.write_text("old\n", encoding="utf-8")
    patch_file.write_text("--- a/notes.txt\n+++ b/notes.txt\n@@ -1 +1 @@\n-old\n+new\n", encoding="utf-8")

    patch_result = CliRunner().invoke(
        app,
        [
            "tools",
            "run",
            "apply_patch",
            "--workspace",
            str(tmp_path),
            "--patch-file",
            str(patch_file),
            "--approved",
        ],
    )
    argv_result = CliRunner().invoke(
        app,
        [
            "tools",
            "run",
            "run_shell",
            "--workspace",
            str(tmp_path),
            "--arg",
            sys.executable,
            "--arg",
            "-c",
            "--arg",
            "print('day8 argv')",
            "--approved",
        ],
    )

    assert patch_result.exit_code == 0, patch_result.output
    assert notes.read_text(encoding="utf-8") == "new\n"
    assert argv_result.exit_code == 0, argv_result.output
    assert "day8 argv" in argv_result.output


def test_day8_tool_cli_rejects_unapproved_write_and_dangerous_argv(tmp_path) -> None:
    write_result = CliRunner().invoke(
        app,
        [
            "tools",
            "run",
            "write_file",
            "--workspace",
            str(tmp_path),
            "--path",
            "scratch.txt",
            "--content",
            "hello",
        ],
    )
    dangerous_result = CliRunner().invoke(
        app,
        [
            "tools",
            "run",
            "run_shell",
            "--workspace",
            str(tmp_path),
            "--arg",
            "git",
            "--arg",
            "push",
            "--approved",
        ],
    )

    assert write_result.exit_code == 1
    assert "Preview:" in write_result.output
    assert not (tmp_path / "scratch.txt").exists()
    assert dangerous_result.exit_code == 1
    assert "blocked" in dangerous_result.output


def test_day8_chat_slash_command_matrix(tmp_path) -> None:
    for command, expected in {
        "/tools": "write_file risk=medium permission=ask",
        "/config": "memory_reflection_mode: deterministic",
        "/last": "(no completed turn yet)",
    }.items():
        result = CliRunner().invoke(app, ["chat", command, "--workspace", str(tmp_path), "--no-model", "--preview"])

        assert result.exit_code == 0, result.output
        assert expected in result.output
