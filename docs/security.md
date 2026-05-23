# MiniCode Agent Security Notes

## Permission Model

Every tool request is evaluated before execution.

- Low-risk read-only operations can be allowed automatically.
- Write tools and shell/test commands require approval by default.
- Dangerous commands and workspace escape attempts are denied.

## Path Sandbox

Path-like arguments are resolved before execution. If a resolved path is outside the configured workspace, the tool call is denied. This protects against `../` traversal and accidental writes to unrelated directories.

## Command Safety

Shell commands pass through a lightweight risk classifier. High-risk patterns such as recursive deletion, disk formatting, shutdown, and similar destructive commands are blocked or require approval depending on policy.

## Trace And Audit

Runs write trace events for requests, permission checks, tool results, compression, subagent activity, and memory writes. Trace payloads are compacted and sensitive environment-like values are redacted before persistence.

## LLM Safety Boundary

LLM rerank and LLM memory reflection are advisory:

- Skill rerank can only reorder already recalled skill candidates.
- Memory reflection can only propose candidate records.
- Final memory admission still applies confidence checks, duplicate detection, source run ids, and secret rejection.
- If the model client is missing or returns invalid output, MiniCode falls back to deterministic behavior.

## Known V1 Limits

- Command safety is conservative pattern matching, not a complete shell sandbox.
- Write approval is modeled at the tool layer; a production remote runner would still need OS/container isolation.
- The built-in skill set is intentionally small.
- Benchmark tasks are local and lightweight; they are useful for regression and ablation, not a replacement for large public SWE benchmarks.
