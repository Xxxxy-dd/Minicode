# MiniCode Eval Report

- tasks: 1
- config: skill_only
- description: Skill routing only.
- skills: True
- memory: False
- compression: False
- subagents: False
- memory_reflection_mode: off
- passed: 1
- pass_rate: 100.00%

| Task | Category | Expected | Passed | Runtime | Tool Calls | Retries | Compression | Subagents | Trace |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| simple_code_review | review | analysis_only | yes | 0.092s | 2 | 0 | 0 | 0 | E:\VS code\Agent\.minicode\eval_workspaces\skill_only\eval_20260522_070413\simple_code_review\.minicode\traces\trace.jsonl |

## simple_code_review

- prompt: Review current diff and report risks, changed files, and test suggestions.
- category: review
- tags: subagent, diff
- difficulty: easy
- expected: analysis_only
- source_workspace: `E:\VS code\Agent\examples\repos\mini_py`
- workspace: `E:\VS code\Agent\.minicode\eval_workspaces\skill_only\eval_20260522_070413\simple_code_review`
- run_id: `eval_bf30a2d4`
- agent_ok: True
- config_features: `{"name": "skill_only", "description": "Skill routing only.", "enable_skills": true, "enable_memory": false, "enable_compression": false, "enable_subagents": false, "memory_reflection_mode": "off"}`
