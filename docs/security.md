# MiniCode Agent Security Notes

## Permission Model

Every tool request is evaluated before execution.

- Low-risk read-only operations can be allowed automatically.
- Write tools first generate a diff preview, then require approval before execution.
- Shell/test commands require approval by default.
- Dangerous commands and workspace escape attempts are denied.
- Sensitive paths such as `.env`, `.ssh`, private keys, and key/certificate files are blocked.

## Path Sandbox

Path-like arguments are resolved before execution. If a resolved path is outside the configured workspace, the tool call is denied. This protects against `../` traversal and accidental writes to unrelated directories.

## Command Safety

Shell commands pass through a lightweight risk classifier. High-risk patterns such as recursive deletion, disk formatting, shutdown, and similar destructive commands are blocked or require approval depending on policy.

The classifier covers common Unix commands and Windows/PowerShell variants such as `Remove-Item -Recurse`, `rd /s`, `reg delete`, `Stop-Computer`, and piped download execution.

## Write Preview

Write-class tools use a preview -> approval -> execute flow:

- `write_file`, `append_file`, `create_file`, `edit_file`, and `delete_file` simulate the resulting text and show a unified diff or create/delete summary.
- `apply_patch` validates patch paths and records touched files plus the key patch hunks.
- Rejected writes keep files unchanged and still emit trace events explaining the decision.
- Approved writes execute only after the preview and approval decision are recorded.

## Trace And Audit

Runs write trace events for requests, permission checks, write previews, approval decisions, tool results, compression, subagent activity, memory recall, memory rejection, and memory writes. Trace payloads are sanitized in `TraceStore` before persistence, so direct trace callers, tool executors, and memory paths share the same redaction boundary.

## Memory Safety

Memory records carry `status`, `reason`, `admission_reason`, tags, metadata, and source run id. Active records are recalled with a traceable reason and score; stale records are skipped by default. Memory content, reasons, tags, metadata, and trace payloads share the same secret redaction policy, and candidate records that look like credentials are rejected before storage.

## LLM Safety Boundary

LLM rerank and LLM memory reflection are advisory:

- Skill rerank can only reorder already recalled skill candidates.
- Memory reflection can only propose candidate records.
- Final memory admission still applies confidence checks, duplicate detection, stale/conflict status, source run ids, and secret rejection.
- If the model client is missing or returns invalid output, MiniCode falls back to deterministic behavior.

## Known V1 Limits

- Command safety is conservative pattern matching, not a complete shell sandbox.
- Write approval is modeled at the tool layer; a production remote runner would still need OS/container isolation.
- The built-in skill set is intentionally small.
- Benchmark tasks are local and lightweight; they are useful for regression and ablation, not a replacement for large public SWE benchmarks.
