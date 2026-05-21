# MiniCode Agent

MiniCode Agent is a local Coding Agent Runtime inspired by Claude Code, SWE-agent, OpenHands, and lightweight Agent Harness systems.

The first milestone focuses on a clean Python project skeleton and stable module boundaries. Later milestones will implement the query loop, tool runtime, skill routing, memory, context compression, permissions, subagents, and evaluation harness described in the design spec.

## Current Status

Day 6 optimized agent loop is available:

- CLI entrypoint
- configuration model
- core state models
- tool interface models
- tool registry
- read-only tools: `list_files`, `read_file`, `search_code`, `git_status`, `git_diff`
- write tools: `write_file`, `edit_file`
- command tools: `run_shell`, `run_tests`
- permission gateway
- path sandbox for workspace-bound tool arguments
- command safety classifier for dangerous shell patterns
- trace store for tool request, permission, and result events
- SQLite trace persistence with JSONL fallback
- compact trace payloads, secret redaction, duration metrics, and JSON trace output
- rule-driven agent loop with phase trace events
- planner abstraction, run_started trace, runtime context, and max-step support
- approval-required handling for medium/high-risk tools
- deny handling for blocked tools
- package layout
- smoke tests, read-only tool tests, permission tests, write tool tests, shell tool tests, trace tests, and agent loop tests

## Planned CLI

```bash
minicode run "fix the failing tests"
minicode tools list
minicode tools run read_file --path README.md
minicode tools run write_file --path notes.txt --content "hello" --approved
minicode tools run write_file --path nested/notes.txt --content "hello" --create-parents --approved
minicode tools run edit_file --path notes.txt --old-text "hello" --new-text "hi" --approved
minicode tools run run_shell --command "cmd /c echo hello" --approved
minicode tools run run_shell --arg cmd --arg /c --arg "echo hello" --approved
minicode tools run run_tests --command "python -m pytest tests" --approved
minicode trace
minicode trace <run_id> --json
minicode eval examples/tasks
```

## Design Spec

See `docs/superpowers/specs/2026-05-20-minicode-agent-design.md`.
