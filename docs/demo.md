# MiniCode Agent Demo Commands

Run these from the project root.

## Smoke Check

```powershell
E:\conda\envs\minicode\python.exe -m pytest tests
E:\conda\envs\minicode\Scripts\minicode.exe --help
```

## Inspect The Project Without A Model

```powershell
E:\conda\envs\minicode\Scripts\minicode.exe run "inspect project" --workspace . --no-model
```

This demonstrates the deterministic rule planner, tool runtime, trace store, and safe read-only flow.

## Route Skills

```powershell
E:\conda\envs\minicode\Scripts\minicode.exe skills route "审查 diff"
E:\conda\envs\minicode\Scripts\minicode.exe skills list
```

This shows explainable skill routing with Chinese and English aliases.

## Inspect Tools

```powershell
E:\conda\envs\minicode\Scripts\minicode.exe tools list
E:\conda\envs\minicode\Scripts\minicode.exe tools run inspect_repo --workspace .
```

This shows the declared tool runtime and the structured repository summary used by planner/debug flows.

## Write Preview And Approval

```powershell
E:\conda\envs\minicode\Scripts\minicode.exe tools run write_file --workspace . --path scratch.txt --content "hello"
E:\conda\envs\minicode\Scripts\minicode.exe tools run write_file --workspace . --path scratch.txt --content "hello" --approved
E:\conda\envs\minicode\Scripts\minicode.exe trace --workspace .
```

The first command shows a diff preview and refuses to write without approval. The approved command executes after the preview/approval events are recorded in trace.

## Memory

```powershell
E:\conda\envs\minicode\Scripts\minicode.exe memory add "Use python -m pytest tests before finishing." --kind project_memory --confidence 0.9
E:\conda\envs\minicode\Scripts\minicode.exe memory list --query pytest
E:\conda\envs\minicode\Scripts\minicode.exe memory list --kind failure_memory
E:\conda\envs\minicode\Scripts\minicode.exe memory stale <memory-id> --reason "superseded by current test command"
```

This demonstrates local durable memory, explainable recall metadata, kind/status/tag filtering, and manual stale marking.

## Run The Harness

```powershell
E:\conda\envs\minicode\Scripts\minicode.exe eval examples\tasks\11_memory_reuse_hint.json --workspace . --config all
```

The report is written under `.minicode/evals/` with Markdown, `results.json`, and `summary.csv`.

## LLM-Enhanced Run

```powershell
$env:MINICODE_MODEL="gpt-4.1-mini"
$env:MINICODE_MODEL_API_KEY="<api-key>"
E:\conda\envs\minicode\Scripts\minicode.exe run "inspect project" --workspace . --llm-rerank --memory-reflection-mode llm
```

This enables model planning plus auxiliary LLM rerank and LLM memory reflection. If the model is unavailable, the deterministic paths remain the fallback for rerank and memory reflection.
