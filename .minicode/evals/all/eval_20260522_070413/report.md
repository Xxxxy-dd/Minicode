# MiniCode Ablation Comparison

| Config | Tasks | Passed | Pass Rate | Avg Runtime | Tool Calls | Retries | Compression | Subagents | Memory Mode |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| baseline | 1 | 1 | 100.00% | 0.096s | 2 | 0 | 0 | 0 | off |
| skill_only | 1 | 1 | 100.00% | 0.092s | 2 | 0 | 0 | 0 | off |
| memory_skill | 1 | 1 | 100.00% | 0.085s | 2 | 0 | 0 | 0 | deterministic |
| memory_llm | 1 | 1 | 100.00% | 0.085s | 2 | 0 | 0 | 0 | llm |
| full | 1 | 1 | 100.00% | 0.437s | 2 | 0 | 0 | 1 | deterministic |
| full_llm_memory | 1 | 1 | 100.00% | 0.421s | 2 | 0 | 0 | 1 | llm |

## Config Reports

- `E:\VS code\Agent\.minicode\evals\baseline\eval_20260522_070413\report.md`
- `E:\VS code\Agent\.minicode\evals\skill_only\eval_20260522_070413\report.md`
- `E:\VS code\Agent\.minicode\evals\memory_skill\eval_20260522_070413\report.md`
- `E:\VS code\Agent\.minicode\evals\memory_llm\eval_20260522_070413\report.md`
- `E:\VS code\Agent\.minicode\evals\full\eval_20260522_070413\report.md`
- `E:\VS code\Agent\.minicode\evals\full_llm_memory\eval_20260522_070413\report.md`

LLM memory configs are experiment slots in this local deterministic build. They record the requested mode and fall back to deterministic memory candidate generation until the LLM reflection engine is added.