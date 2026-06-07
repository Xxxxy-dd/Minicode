from pathlib import Path

from minicode_agent.harness import HarnessRunner
from minicode_agent.harness.runner import render_report


EXAMPLES_ROOT = Path("examples")
TASKS_DIR = EXAMPLES_ROOT / "tasks"


def test_benchmark_task_set_has_ten_tasks() -> None:
    tasks = HarnessRunner(Path.cwd()).load_tasks(TASKS_DIR)

    assert len(tasks) >= 10
    assert len({task.id for task in tasks}) == len(tasks)


def test_benchmark_tasks_have_enough_success_commands() -> None:
    tasks = HarnessRunner(Path.cwd()).load_tasks(TASKS_DIR)
    auto_judged = [task for task in tasks if task.success]

    assert len(auto_judged) >= 5


def test_v1_1_tasks_cover_day6_categories() -> None:
    tasks = HarnessRunner(Path.cwd()).load_tasks(TASKS_DIR)
    by_id = {task.id: task for task in tasks}
    expected = {
        "memory_reuse_hint": "memory",
        "compression_long_context": "context",
        "dangerous_command_block": "safety",
        "simple_code_review": "review",
        "workspace_skill_route": "skills",
        "cli_release_polish": "cli",
        "agent_team_reviewer": "team",
    }

    for task_id, category in expected.items():
        assert by_id[task_id].category == category

    assert by_id["agent_team_reviewer"].team_assertions


def test_v1_2_tasks_cover_team_worktree_and_prompt_injection() -> None:
    tasks = HarnessRunner(Path.cwd()).load_tasks(TASKS_DIR)
    by_id = {task.id: task for task in tasks}
    expected = {
        "prompt_injection_readme": "safety",
        "prompt_injection_command_output": "safety",
        "prompt_injection_diff": "safety",
        "worktree_clean_isolation": "team",
        "worktree_dirty_blocker": "team",
        "failure_memory_recall": "memory",
        "context_evidence_compression": "context",
    }

    for task_id, category in expected.items():
        assert by_id[task_id].category == category

    assert by_id["worktree_clean_isolation"].team_assertions
    assert by_id["worktree_dirty_blocker"].team_assertions
    assert by_id["failure_memory_recall"].trace_assertions
    assert by_id["context_evidence_compression"].trace_assertions


def test_benchmark_tasks_have_metadata() -> None:
    tasks = HarnessRunner(Path.cwd()).load_tasks(TASKS_DIR)

    assert all(task.category != "general" for task in tasks)
    assert all(task.tags for task in tasks)
    assert all(task.difficulty for task in tasks)
    assert {task.expected.value for task in tasks} >= {"pass", "fail", "analysis_only"}


def test_benchmark_task_workspaces_exist() -> None:
    tasks = HarnessRunner(Path.cwd()).load_tasks(TASKS_DIR)

    assert all(task.workspace.exists() for task in tasks)


def test_benchmark_has_buggy_repo_task() -> None:
    tasks = HarnessRunner(Path.cwd()).load_tasks(TASKS_DIR)
    fix_task = next(task for task in tasks if task.id == "fix_pytest_failure")

    assert fix_task.workspace.name == "mini_py_buggy"
    assert fix_task.expected.value == "fail"


def test_benchmark_report_displays_pass_rate() -> None:
    tasks = HarnessRunner(Path.cwd()).load_tasks(TASKS_DIR)
    results = [
        HarnessRunner(Path.cwd()).run_task(tasks[0]),
    ]

    report = render_report(results)

    assert "pass_rate" in report
    assert tasks[0].id in report


def test_demo_commands_stay_in_sync() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    demo = Path("docs/demo.md").read_text(encoding="utf-8")
    delivery = Path("docs/V1.2交付报告.md").read_text(encoding="utf-8")
    examples = Path("examples/README.md").read_text(encoding="utf-8")
    required_tasks = [
        "prompt_injection_readme",
        "prompt_injection_diff",
        "worktree_clean_isolation",
        "worktree_dirty_blocker",
        "failure_memory_recall",
        "context_evidence_compression",
    ]

    for task_id in required_tasks:
        assert task_id in demo
        assert task_id in examples

    assert "Safety Evidence" in demo
    assert "worktree patch proposal" in demo
    assert "docs/V1.2交付报告.md" in readme
    assert "pass_rate: 100.00%" in readme
    assert "V1.1 发布检查" not in readme
    assert "Worktree Isolation" in readme
    assert "简历描述" in readme
    assert "docs/V1.2交付报告.md" in demo
    assert "22/22 passed" in delivery
    assert "eval_20260607_093707_451286_f8be7e/report.md" in delivery
    assert "Prompt Cache 目前是通过 PromptSegment / ContextFrame" in delivery
