from typer.testing import CliRunner

from minicode_agent.agent import AgentLoop
from minicode_agent.cli.app import app
from minicode_agent.memory import (
    DeterministicReflectionEngine,
    LLMReflectionEngine,
    MemoryKind,
    MemoryStatus,
    MemoryStore,
    parse_llm_memory_candidates,
)
from minicode_agent.memory.reflection import MemoryCandidate
from minicode_agent.models import build_planning_prompt
from minicode_agent.models import ModelResponse
from minicode_agent.runtime import RuntimeContext
from minicode_agent.tools.registry import create_default_registry


def test_memory_store_adds_and_lists_records(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.db")

    record, inserted = store.add(
        MemoryKind.PROJECT,
        "Use python -m pytest tests for this project.",
        confidence=0.9,
        source_run_id="run_1",
        tags=["tests"],
        reason="manual project fact",
    )

    assert inserted
    assert record.kind == MemoryKind.PROJECT
    assert record.confidence == 0.9
    records = store.list()
    assert records[0].content == "Use python -m pytest tests for this project."
    assert records[0].tags == ["tests"]
    assert records[0].reason == "manual project fact"
    assert records[0].status == MemoryStatus.ACTIVE
    assert records[0].admission_reason == "accepted"


def test_memory_store_detects_duplicates(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.db")

    first, first_inserted = store.add(MemoryKind.PROCEDURE, "Run pytest before finishing.")
    second, second_inserted = store.add(MemoryKind.PROCEDURE, " run  PYTEST before finishing. ")

    assert first_inserted
    assert not second_inserted
    assert second.id == first.id
    assert len(store.list()) == 1


def test_memory_store_rejects_secret_content(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.db")

    try:
        store.add(MemoryKind.USER, "api_key=abc123 should not be saved")
    except ValueError as exc:
        assert "secret" in str(exc)
    else:
        raise AssertionError("secret memory should be rejected")


def test_memory_store_rejects_secret_reason_and_tags(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.db")

    for kwargs, field in (
        ({"reason": "token=abc123 should not be saved"}, "reason"),
        ({"tags": ["api_key=abc123"]}, "tag"),
    ):
        try:
            store.add(MemoryKind.USER, "Answer in Chinese.", **kwargs)
        except ValueError as exc:
            assert "secret" in str(exc)
            assert field in str(exc)
        else:
            raise AssertionError("secret memory metadata should be rejected")


def test_memory_search_returns_relevant_records(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    store.add(MemoryKind.PROJECT, "Use pytest for validation.", confidence=0.5, tags=["tests"])
    store.add(MemoryKind.PROCEDURE, "Run test suite before finishing.", confidence=0.9, tags=["tests"])
    store.add(MemoryKind.USER, "Answer in Chinese.", tags=["style"])

    records = store.search("please run tests")

    assert [record.kind for record in records] == [MemoryKind.PROCEDURE, MemoryKind.PROJECT]


def test_memory_recall_includes_reason_score_and_skips_stale(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    stale, _ = store.add(MemoryKind.PROJECT, "Use pytest for validation.", confidence=0.9, tags=["tests"])
    active, _ = store.add(MemoryKind.PROCEDURE, "Run pytest before finishing.", confidence=0.8, tags=["tests"])
    assert store.mark_status(stale.id, MemoryStatus.STALE, reason="outdated command")

    recalled = store.recall("pytest")

    assert [item["record"].id for item in recalled] == [active.id]
    assert recalled[0]["score"] > 0
    assert "matched query terms" in recalled[0]["reason"]


def test_memory_store_marks_possible_conflicts(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    store.add(MemoryKind.PROCEDURE, "Use pytest before finishing.", confidence=0.8)

    record, inserted = store.add(MemoryKind.PROCEDURE, "Do not use pytest before finishing.", confidence=0.8)

    assert inserted
    assert record.status == MemoryStatus.CONFLICT
    assert record.metadata["conflict_notes"]


def test_memory_store_deletes_records(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    record, _ = store.add(MemoryKind.USER, "Answer in Chinese.")

    assert store.delete(record.id)
    assert not store.delete(record.id)
    assert store.list() == []


def test_memory_store_jsonl_fallback(tmp_path, monkeypatch) -> None:
    def fail_init(self) -> None:
        raise ImportError("sqlite unavailable")

    monkeypatch.setattr(MemoryStore, "_init_db", fail_init)
    store = MemoryStore(tmp_path / "memory.db")

    record, inserted = store.add(MemoryKind.PROJECT, "Use pytest for validation.")

    assert store.backend == "jsonl"
    assert inserted
    assert store.list()[0].id == record.id
    assert store.delete(record.id)


def test_memory_store_jsonl_fallback_marks_stale(tmp_path, monkeypatch) -> None:
    def fail_init(self) -> None:
        raise ImportError("sqlite unavailable")

    monkeypatch.setattr(MemoryStore, "_init_db", fail_init)
    store = MemoryStore(tmp_path / "memory.db")
    record, inserted = store.add(MemoryKind.PROJECT, "Use pytest for validation.", tags=["tests"])

    assert inserted
    assert store.mark_status(record.id, MemoryStatus.STALE, reason="jsonl stale test")
    stale = store.list(status=MemoryStatus.STALE)[0]
    assert stale.id == record.id
    assert stale.status == MemoryStatus.STALE
    assert stale.metadata["status_reason"] == "jsonl stale test"


def test_build_planning_prompt_includes_relevant_memory(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    memory, _ = store.add(
        MemoryKind.PROJECT,
        "Use python -m pytest tests for this project.",
        confidence=0.9,
        source_run_id="run_1",
        tags=["tests"],
    )

    messages = build_planning_prompt(
        "run tests",
        ["README.md"],
        create_default_registry(),
        memories=[memory],
    )

    assert '"relevant_memory"' in messages[1].content
    assert "Use python -m pytest tests for this project." in messages[1].content
    assert '"confidence": 0.9' in messages[1].content


def test_build_planning_prompt_limits_memory_budget(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    memories = [
        store.add(MemoryKind.PROJECT, f"Memory {index} " + ("x" * 300))[0]
        for index in range(20)
    ]

    messages = build_planning_prompt("inspect", ["README.md"], create_default_registry(), memories=memories)

    assert "Memory 0" in messages[1].content
    assert "Memory 8" not in messages[1].content


def test_cli_memory_add_and_list(tmp_path) -> None:
    runner = CliRunner()

    add_result = runner.invoke(
        app,
        [
            "memory",
            "add",
            "Answer in Chinese.",
            "--workspace",
            str(tmp_path),
            "--kind",
            "user_memory",
            "--confidence",
            "0.8",
            "--tag",
            "style",
        ],
    )
    list_result = runner.invoke(app, ["memory", "list", "--workspace", str(tmp_path)])

    assert add_result.exit_code == 0, add_result.output
    assert "added" in add_result.output
    assert list_result.exit_code == 0, list_result.output
    assert "user_memory" in list_result.output
    assert "active" in list_result.output
    assert "manual memory add" in list_result.output
    assert "Answer in Chinese." in list_result.output


def test_cli_memory_list_filters_status_and_tag(tmp_path) -> None:
    runner = CliRunner()
    add_result = runner.invoke(
        app,
        ["memory", "add", "Run pytest.", "--workspace", str(tmp_path), "--tag", "tests"],
    )
    memory_id = next(line.split(": ", 1)[1] for line in add_result.output.splitlines() if line.startswith("id: "))
    stale_result = runner.invoke(app, ["memory", "stale", memory_id, "--workspace", str(tmp_path), "--reason", "superseded"])
    list_active = runner.invoke(app, ["memory", "list", "--workspace", str(tmp_path), "--tag", "tests"])
    list_stale = runner.invoke(app, ["memory", "list", "--workspace", str(tmp_path), "--status", "stale", "--tag", "tests"])

    assert stale_result.exit_code == 0, stale_result.output
    assert "Run pytest." not in list_active.output
    assert "Run pytest." in list_stale.output
    assert "status_reason=superseded" in list_stale.output


def test_cli_memory_delete(tmp_path) -> None:
    runner = CliRunner()
    add_result = runner.invoke(app, ["memory", "add", "Answer in Chinese.", "--workspace", str(tmp_path)])
    memory_id = next(line.split(": ", 1)[1] for line in add_result.output.splitlines() if line.startswith("id: "))

    delete_result = runner.invoke(app, ["memory", "delete", memory_id, "--workspace", str(tmp_path)])
    list_result = runner.invoke(app, ["memory", "list", "--workspace", str(tmp_path)])

    assert delete_result.exit_code == 0, delete_result.output
    assert "deleted" in delete_result.output
    assert "Answer in Chinese." not in list_result.output


def test_reflection_admission_rejects_low_confidence() -> None:
    engine = DeterministicReflectionEngine()
    candidate = MemoryCandidate(
        kind=MemoryKind.PROJECT,
        content="Maybe useful later.",
        confidence=0.2,
        source_run_id="run_1",
        tags=["reflection"],
        reason="low confidence test",
        metadata={"rule": "test"},
    )

    record, reason = engine.admit(candidate)

    assert record is None
    assert "confidence" in reason


def test_agent_loop_writes_reflection_memory(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_memory_test")

    result = AgentLoop(runtime, "inspect project").run()

    records = runtime.memory_store.list()
    assert result.state.current_phase.value == "done"
    assert records
    assert any(record.source_run_id == "agent_memory_test" for record in records)
    events = runtime.trace_store.list_events("agent_memory_test")
    assert "memory_written" in [event.event_type for event in events]
    written = next(event for event in events if event.event_type == "memory_written")
    assert written.payload["id"]
    assert written.payload["admission_reason"] == "accepted"
    assert written.payload["evidence_refs"]
    assert runtime.memory_store.get(written.payload["id"]) is not None


def test_agent_loop_traces_memory_recall_reason(tmp_path) -> None:
    runtime = RuntimeContext.create(tmp_path, run_id="agent_memory_recall_test")
    memory, _ = runtime.memory_store.add(
        MemoryKind.PROJECT,
        "Use pytest for validation.",
        confidence=0.9,
        source_run_id="seed_run",
        tags=["tests"],
    )

    AgentLoop(runtime, "run pytest", enable_skills=False, enable_memory=True).run()

    events = runtime.trace_store.list_events("agent_memory_recall_test")
    recall = next(event for event in events if event.event_type == "memory_recalled")
    assert recall.payload["records"][0]["id"] == memory.id
    assert recall.payload["records"][0]["source_run_id"] == "seed_run"
    assert "matched query terms" in recall.payload["records"][0]["reason"]


def test_parse_llm_memory_candidates_accepts_structured_json() -> None:
    candidates = parse_llm_memory_candidates(
        """
        {
          "memories": [
            {
              "kind": "procedure_memory",
              "content": "Run python -m pytest tests before finishing.",
              "confidence": 0.8,
              "tags": ["tests"],
              "reason": "Observed successful validation."
            }
          ]
        }
        """,
        "run_1",
    )

    assert candidates[0].kind == MemoryKind.PROCEDURE
    assert candidates[0].source_run_id == "run_1"
    assert "llm_reflection" in candidates[0].tags


def test_parse_llm_memory_response_summarizes_and_filters() -> None:
    from minicode_agent.memory import parse_llm_memory_response

    result = parse_llm_memory_response(
        """
        {
          "summary": "Keep the pytest workflow and ignore noise.",
          "memories": [
            {
              "keep": true,
              "kind": "procedure_memory",
              "content": "Run python -m pytest tests before finishing.",
              "confidence": 0.8,
              "tags": ["tests"],
              "reason": "Observed successful validation."
            },
            {
              "keep": false,
              "kind": "failure_memory",
              "content": "The output included a temporary debug note.",
              "confidence": 0.6,
              "tags": ["noise"],
              "reason": "LLM filtered this noise."
            }
          ]
        }
        """,
        "run_1",
    )

    assert result.summary == "Keep the pytest workflow and ignore noise."
    assert len(result.candidates) == 1
    assert result.filtered_count == 1
    assert result.candidates[0].kind == MemoryKind.PROCEDURE


def test_llm_reflection_engine_uses_model_client(tmp_path) -> None:
    class MemoryModel:
        def __init__(self) -> None:
            self.messages = []
            self.responses = [
                """
                {
                  "summary": "Read README.",
                  "selected_skill": null,
                  "next_actions": ["Read README.md."],
                  "stop": false,
                  "final_answer": null,
                  "action": {"tool": "read_file", "arguments": {"path": "README.md"}}
                }
                """,
                """
                {
                  "summary": "Done.",
                  "selected_skill": null,
                  "next_actions": ["Reflect."],
                  "stop": true,
                  "final_answer": "README inspected.",
                  "action": null
                }
                """,
                """
                {
                  "summary": "Remember the README path and the demo docs.",
                  "memories": [
                    {
                      "keep": true,
                      "kind": "project_memory",
                      "content": "README.md explains the demo project.",
                      "confidence": 0.7,
                      "tags": ["docs"],
                      "reason": "The run inspected README."
                    }
                  ]
                }
                """,
            ]

        def complete(self, messages):
            self.messages.append(messages)
            return ModelResponse(self.responses.pop(0))

    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_llm_memory_test")
    model = MemoryModel()

    result = AgentLoop(
        runtime,
        "inspect project",
        model_client=model,
        memory_reflection_mode="llm",
    ).run()

    assert result.state.current_phase.value == "done"
    assert runtime.memory_store.list()[0].content == "README.md explains the demo project."
    assert result.state.task_state.history_summary
    assert len(model.messages) >= 2
    events = runtime.trace_store.list_events("agent_llm_memory_test")
    memory_event = next(event for event in events if event.event_type == "memory_reflected")
    assert memory_event.payload["mode"] == "llm"
    assert memory_event.payload["written"] == 1
    assert memory_event.payload["summary"]
