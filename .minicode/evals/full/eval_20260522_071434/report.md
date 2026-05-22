# MiniCode Eval Report

- tasks: 1
- config: full
- description: Skills, deterministic memory, compression, and reviewer subagents.
- skills: True
- memory: True
- compression: True
- subagents: True
- memory_reflection_mode: deterministic
- passed: 1
- pass_rate: 100.00%

| Task | Category | Expected | Passed | Runtime | Tool Calls | Retries | Compression | Subagents | Memory Written | Memory Rejected | Trace |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| memory_reuse_hint | memory | analysis_only | yes | 0.065s | 2 | 0 | 0 | 0 | 1 | 0 | E:\VS code\Agent\.minicode\eval_workspaces\full\eval_20260522_071434\memory_reuse_hint\.minicode\traces\trace.jsonl |

## memory_reuse_hint

- prompt: Inspect the project and remember the local validation command for future tasks.
- category: memory
- tags: memory, procedure, ablation
- difficulty: easy
- expected: analysis_only
- source_workspace: `E:\VS code\Agent\examples\repos\mini_py`
- workspace: `E:\VS code\Agent\.minicode\eval_workspaces\full\eval_20260522_071434\memory_reuse_hint`
- run_id: `eval_b768bef6`
- agent_ok: True
- config_features: `{"name": "full", "description": "Skills, deterministic memory, compression, and reviewer subagents.", "enable_skills": true, "enable_memory": true, "enable_compression": true, "enable_subagents": true, "memory_reflection_mode": "deterministic"}`
