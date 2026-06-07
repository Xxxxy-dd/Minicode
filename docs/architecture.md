# MiniCode Agent Architecture

## V1 Scope

MiniCode Agent V1 is a local coding-agent runtime. It is not only a prompt wrapper: the model plans, but all real actions go through typed tools, permissions, trace storage, and a bounded agent loop.

## Runtime Flow

```text
User task
-> RuntimeContext
-> AgentLoop
-> load memory and workspace context
-> route skills
-> plan with rules or model
-> request tool
-> PermissionPolicy
-> write preview for modifying tools
-> approval decision
-> ToolExecutor
-> observe result
-> compress context when needed
-> reflect durable memory
-> write trace and final state
```

## Main Modules

- `src/minicode_agent/agent/`: state machine, rule planner, model planner, retry and stop policy.
- `src/minicode_agent/tools/`: read, write, shell, test, and subagent tool implementations.
- `src/minicode_agent/permissions/`: path sandbox and command risk decisions.
- `src/minicode_agent/trace/`: SQLite trace persistence with JSONL fallback.
- `src/minicode_agent/skills/`: metadata-driven skill loading, routing, and optional LLM rerank.
- `src/minicode_agent/memory/`: deterministic and optional LLM reflection, explainable recall, stale/conflict status, and final admission rules.
- `src/minicode_agent/context/`: deterministic structured context compression.
- `src/minicode_agent/subagents/`: bounded explorer/reviewer agents built on the same tool runtime.
- `src/minicode_agent/harness/`: local benchmark tasks, reports, and ablation configs.

## V1.2 Evidence Reporting

The harness report is the main V1.2 delivery artifact. It keeps the existing pass-rate and metric tables, then adds per-task trace summaries for:

- safety evidence: prompt-injection findings and permission denials;
- team evidence: completed roles, evidence refs, merge blockers, and patch proposal ids;
- worktree evidence: isolated worktree path, branch, cleanup policy, and no-auto-merge status;
- memory evidence: recalled memory id, score, reason, and evidence refs;
- context evidence: compression ratio, compressed observation ids, and ContextFrame evidence categories.

## Design Boundaries

- The model never directly edits files or runs commands; it can only request registered tools.
- Modifying tools use a preview -> approval -> execute boundary in `ToolExecutor`.
- Trace redaction is centralized in `TraceStore`, not left to individual callers.
- Context compression receives the active tool registry effects from the running agent, so custom tool sets do not inherit stale default-tool assumptions.
- LLM enhancements are optional and fall back to deterministic behavior.
- Memory write safety lives in `MemoryStore`, so manual adds, deterministic reflection, and LLM reflection share the same admission gate, status model, and secret redaction.
- Subagents are tools, not independent uncontrolled peers.
- Harness configs act as feature switches for ablation: `baseline`, `skill_only`, `memory_skill`, `memory_llm`, `full`, `full_llm_memory`.

## Day8 Governance Notes

- Chat slash commands are handled in `cli/chat_commands.py`, separate from `cli/live_ui.py`. Day8 adds `/tools`, `/config`, and `/last` beside the existing `/status`, `/memory`, `/skills`, `/trace`, and `/diff` commands.
- Slash command help is generated from the command registry so `/help` and the implemented command set stay in sync.
- Skill metadata uses `schema_version: 1`. The lightweight parser remains intentionally narrow and rejects unsupported schema versions instead of accepting unknown formats silently.
- Trace events include `schema_version: 1` in the stored model and JSON output. Existing event payloads stay compatible; the version field documents the current event contract for future migrations.
- Eval run ids include microseconds and a short random suffix to avoid report/workspace collisions during fast consecutive or parallel checks.
- `tests/test_day8_regression_matrix.py` is the V1.1 release smoke matrix for memory status flow, patch-file execution, explicit argv execution, write approval refusal, dangerous command blocking, and chat slash command availability.
