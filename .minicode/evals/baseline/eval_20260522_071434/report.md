# MiniCode Eval Report

- tasks: 1
- config: baseline
- description: No skill routing, memory, compression, or subagents.
- skills: False
- memory: False
- compression: False
- subagents: False
- memory_reflection_mode: off
- passed: 1
- pass_rate: 100.00%

| Task | Category | Expected | Passed | Runtime | Tool Calls | Retries | Compression | Subagents | Memory Written | Memory Rejected | Trace |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| memory_reuse_hint | memory | analysis_only | yes | 0.086s | 2 | 0 | 0 | 0 | 0 | 0 | E:\VS code\Agent\.minicode\eval_workspaces\baseline\eval_20260522_071434\memory_reuse_hint\.minicode\traces\trace.jsonl |

## memory_reuse_hint

- prompt: Inspect the project and remember the local validation command for future tasks.
- category: memory
- tags: memory, procedure, ablation
- difficulty: easy
- expected: analysis_only
- source_workspace: `E:\VS code\Agent\examples\repos\mini_py`
- workspace: `E:\VS code\Agent\.minicode\eval_workspaces\baseline\eval_20260522_071434\memory_reuse_hint`
- run_id: `eval_65a2c7a5`
- agent_ok: True
- config_features: `{"name": "baseline", "description": "No skill routing, memory, compression, or subagents.", "enable_skills": false, "enable_memory": false, "enable_compression": false, "enable_subagents": false, "memory_reflection_mode": "off"}`
