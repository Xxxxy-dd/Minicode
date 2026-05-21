# MiniCode Agent

MiniCode Agent is a local Coding Agent Runtime inspired by Claude Code, SWE-agent, OpenHands, and lightweight Agent Harness systems.

The first milestone focuses on a clean Python project skeleton and stable module boundaries. Later milestones will implement the query loop, tool runtime, skill routing, memory, context compression, permissions, subagents, and evaluation harness described in the design spec.

## Current Status

Day 2 permission gateway is available:

- CLI entrypoint
- configuration model
- core state models
- tool interface models
- tool registry
- read-only tools: `list_files`, `read_file`, `search_code`, `git_status`, `git_diff`
- permission gateway
- path sandbox for workspace-bound tool arguments
- approval-required handling for medium/high-risk tools
- deny handling for blocked tools
- package layout
- smoke tests, read-only tool tests, and permission tests

## Planned CLI

```bash
minicode run "fix the failing tests"
minicode tools list
minicode tools run read_file --path README.md
minicode eval examples/tasks
minicode trace <run_id>
```

## Design Spec

See `docs/superpowers/specs/2026-05-20-minicode-agent-design.md`.
