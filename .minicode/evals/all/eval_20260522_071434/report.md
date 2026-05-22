# MiniCode Ablation Comparison

| Config | Tasks | Passed | Pass Rate | Avg Runtime | Tool Calls | Retries | Compression | Subagents | Memory Mode |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| baseline | 1 | 1 | 100.00% | 0.086s | 2 | 0 | 0 | 0 | off |
| skill_only | 1 | 1 | 100.00% | 0.064s | 2 | 0 | 0 | 0 | off |
| memory_skill | 1 | 1 | 100.00% | 0.064s | 2 | 0 | 0 | 0 | deterministic |
| memory_llm | 1 | 1 | 100.00% | 0.060s | 2 | 0 | 0 | 0 | llm |
| full | 1 | 1 | 100.00% | 0.065s | 2 | 0 | 0 | 0 | deterministic |
| full_llm_memory | 1 | 1 | 100.00% | 0.065s | 2 | 0 | 0 | 0 | llm |

## Config Reports

- `E:\VS code\Agent\.minicode\evals\baseline\eval_20260522_071434\report.md`
- `E:\VS code\Agent\.minicode\evals\skill_only\eval_20260522_071434\report.md`
- `E:\VS code\Agent\.minicode\evals\memory_skill\eval_20260522_071434\report.md`
- `E:\VS code\Agent\.minicode\evals\memory_llm\eval_20260522_071434\report.md`
- `E:\VS code\Agent\.minicode\evals\full\eval_20260522_071434\report.md`
- `E:\VS code\Agent\.minicode\evals\full_llm_memory\eval_20260522_071434\report.md`

## Experiment Notes

- `skill_only` vs `baseline`: pass_rate +0.00%, tool_calls +0, memory_written 0, subagent_calls 0.
- `memory_skill` vs `baseline`: pass_rate +0.00%, tool_calls +0, memory_written 1, subagent_calls 0.
- `memory_llm` vs `baseline`: pass_rate +0.00%, tool_calls +0, memory_written 1, subagent_calls 0.
- `full` vs `baseline`: pass_rate +0.00%, tool_calls +0, memory_written 1, subagent_calls 0.
- `full_llm_memory` vs `baseline`: pass_rate +0.00%, tool_calls +0, memory_written 1, subagent_calls 0.
- LLM memory configs call the LLM reflection engine only when a model client is available; otherwise they fall back to deterministic reflection and record the fallback reason.