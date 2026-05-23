# MiniCode Agent

MiniCode Agent is a local Coding Agent Runtime inspired by Claude Code, SWE-agent, OpenHands, Hermes-style agent systems, and lightweight Agent Harness designs.

V1 implements an end-to-end coding-agent loop with tool calling, permissions, trace replay, model planning, skill routing, memory, context compression, subagents, and benchmark evaluation.

## Current Status

V1 is feature-complete after the Day 17 polishing pass:

- CLI entrypoint
- Claude-like interactive `minicode chat` shell with bottom command bar and preview mode
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
- model client interface
- OpenAI-compatible chat completions adapter
- stable planning prompt builder
- structured model response parser
- optional model-driven planner for `minicode run`
- model-driven multi-step query loop
- real tool outputs injected into later model turns
- model stop policy with final answers
- configurable retry limit for failed tool calls
- LLM skill rerank for top skill candidates
- skill metadata and markdown loader
- built-in skills: `debugging`, `test-writing`, `code-review`
- `minicode skills list` and `minicode skills show`
- `minicode skills route` for route debugging
- active skill content injected into model planning prompts
- deterministic metadata-based skill router
- top-k skill injection with traceable route reasons
- memory records with project, user, procedure, and failure kinds
- local memory persistence with SQLite and JSONL fallback
- memory admission rules for confidence, source run ids, duplicate detection, admission reasons, and secret rejection
- `minicode memory add` and `minicode memory list`
- `minicode memory delete` for correcting stale or mistaken records
- relevant memory injected into model planning prompts
- deterministic run-end reflection candidates written to memory
- deterministic `TaskStateCompressor` for long tool observations
- structured `history_summary` on `TaskState`
- `context_compressed` trace events with compression ratio and fallback marker
- compression metrics on agent runs
- compressed observations fed into later model turns
- long single outputs, recent observation bursts, and total history growth trigger compression
- `spawn_subagent` tool for bounded read-only subagents
- Explorer subagent with limited read/search/status tools
- Reviewer subagent with limited diff/read/search/status tools
- subagent max-step limits and structured results
- subagent results include allowed and denied tool sets for auditability
- reviewer subagent reports changed files, risks, and test suggestions
- `subagent_started` and `subagent_finished` trace events
- JSON benchmark task loading from a file or directory
- `minicode eval` runs AgentLoop, success commands, metrics collection, and report generation
- eval metrics include pass/fail, runtime, tool calls, retries, compression events, subagent calls, and trace path
- Markdown eval reports are written under `.minicode/evals/`
- `examples/tasks/` contains 12 benchmark tasks
- `examples/repos/mini_py/` and `examples/repos/mini_py_buggy/` provide sample Python projects
- at least 5 benchmark tasks include automatic success commands
- eval tasks run in isolated copied workspaces under `.minicode/eval_workspaces/`
- eval supports config labels for Day 16 ablation reports
- benchmark tasks include expected outcome, category, tags, and difficulty metadata
- ablation presets: `baseline`, `skill_only`, `memory_skill`, `memory_llm`, `full`, `full_llm_memory`
- eval configs control AgentLoop skill routing, memory, compression, and subagent availability
- `minicode eval ... --config all` runs every ablation preset and writes a comparison report
- eval reports include feature flags and memory reflection mode for reproducibility
- LLM skill rerank for top skill candidates
- LLM memory reflection engine with deterministic admission fallback
- LLM memory summary/filter before deterministic memory admission
- memory quality metrics: candidates, written, rejected, and duplicates
- eval reports also emit `results.json` and `summary.csv`
- `minicode eval --list-configs` shows built-in ablation presets
- `minicode eval ... --config-file custom.json` supports custom JSON eval configs
- Chinese and English skill aliases
- mock model tests for model planning
- package layout
- smoke tests, read-only tool tests, permission tests, write tool tests, shell tool tests, trace tests, agent loop tests, model planning tests, and OpenAI-compatible adapter tests

## CLI Quick Start

```bash
minicode run "fix the failing tests"
minicode run "inspect project" --model gpt-4.1-mini
minicode run "inspect project" --no-model
minicode chat --no-model --preview
minicode chat "inspect project" --no-model
minicode run "inspect project" --model gpt-4.1-mini --llm-rerank --memory-reflection-mode llm
minicode run "review current diff"
minicode skills list
minicode skills show debugging
minicode skills route "审查 diff"
minicode memory add "Use python -m pytest tests for validation" --kind project_memory --confidence 0.9
minicode memory list
minicode memory list --query pytest
minicode memory delete <memory_id>
minicode tools list
minicode tools run spawn_subagent --role explorer --task "inspect project"
minicode tools run read_file --path README.md
minicode tools run write_file --path notes.txt --content "hello" --approved
minicode tools run write_file --path nested/notes.txt --content "hello" --create-parents --approved
minicode tools run edit_file --path notes.txt --old-text "hello" --new-text "hi" --approved
minicode tools run run_shell --command "cmd /c echo hello" --approved
minicode tools run run_shell --arg cmd --arg /c --arg "echo hello" --approved
minicode tools run run_tests --command "python -m pytest tests" --approved
minicode trace
minicode trace <run_id> --json
minicode eval examples/tasks --config baseline
minicode eval examples/tasks --config all
minicode eval examples/tasks --list-configs
minicode eval examples/tasks --config-file custom_ablation.json
```

Model-backed runs use OpenAI-compatible chat completions. Prefer MiniCode-specific
environment variables:

```bash
MINICODE_MODEL=gpt-4.1-mini
MINICODE_MODEL_API_KEY=...
MINICODE_MODEL_BASE_URL=https://api.openai.com/v1
```

`OPENAI_MODEL` and `OPENAI_API_KEY` are also accepted as compatibility fallbacks.

Model-backed runs use the multi-step query loop. Rule-planner runs remain a
single safe action for deterministic local demos. `max_agent_steps` bounds model
turns and tool calls; `max_failed_tool_attempts` bounds repeated failed tool
calls before the run fails. Skill routing uses deterministic metadata and
keyword recall with English and Chinese aliases, with optional LLM reranking for
the top candidates. Route candidates and reasons are stored on `AgentState` and
in trace events. Memory v1 uses deterministic local reflection and admission
rules first; optional LLM summary/filter mode can propose cleaner durable
memories before the same deterministic admission rules decide what gets stored.
Context compression v1 deterministically compresses long observations into
structured task state before the next model turn, preserving goals, files,
failures, and next actions. It triggers on long single tool outputs, recent
observation bursts, or total history growth.
Subagents are implemented with MiniCode's own tool runtime and permission
gateway, not an external agent framework, so they reuse the same sandbox, trace,
and approval boundaries as normal tools. Harness v1 provides the lightweight
evaluation runner used by Day 15 benchmark tasks and Day 16 ablation experiments.
Benchmark v1 adds a 12-task local task set covering pytest repair, small feature
work, boundary fixes, refactoring, docs, type hints, path handling, tests, review,
safety checks, memory reuse, and context-compression pressure. Eval tasks are
copied into isolated workspaces before running,
and reports are grouped by config labels for Day 16 ablation experiments. Ablation
configs can disable skills, memory, compression, and subagents so reports compare
pass rate, runtime, tool calls, retries, compression events, subagent calls, and
memory quality metrics. The `memory_llm` and `full_llm_memory` configs use the
model-backed memory reflection engine when a model client is available, then
reuse the deterministic admission rules for confidence, duplicate detection, and
secret rejection. Without a model client they record the fallback reason and use
deterministic reflection. Reports are written as Markdown plus `results.json`
and `summary.csv` for later charts or resume metrics. LLM reranking now scores
the top skill candidates with an auxiliary model when enabled, and memory
reflection can ask the model to summarize and filter durable memories before the
same deterministic admission rules decide what gets stored.

## Project Docs

- [Architecture](docs/architecture.md)
- [Security](docs/security.md)
- [Demo Commands](docs/demo.md)
- [Core Concepts](docs/核心概念说明.md)
- [Interview Q&A](docs/面试问答.md)

## Design Spec

See `docs/superpowers/specs/2026-05-20-minicode-agent-design.md`.
