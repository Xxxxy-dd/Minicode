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

## Design Boundaries

- The model never directly edits files or runs commands; it can only request registered tools.
- Modifying tools use a preview -> approval -> execute boundary in `ToolExecutor`.
- Trace redaction is centralized in `TraceStore`, not left to individual callers.
- Context compression receives the active tool registry effects from the running agent, so custom tool sets do not inherit stale default-tool assumptions.
- LLM enhancements are optional and fall back to deterministic behavior.
- Memory write safety lives in `MemoryStore`, so manual adds, deterministic reflection, and LLM reflection share the same admission gate, status model, and secret redaction.
- Subagents are tools, not independent uncontrolled peers.
- Harness configs act as feature switches for ablation: `baseline`, `skill_only`, `memory_skill`, `memory_llm`, `full`, `full_llm_memory`.
