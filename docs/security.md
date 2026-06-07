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

V1.2 eval reports summarize security-related trace evidence directly in Markdown. Prompt-injection findings appear under `Safety Evidence`, permission denials show the blocked tool and reason, and team/worktree reports include merge blockers plus patch proposal review status.

## Prompt Injection Boundary

MiniCode treats workspace files, diffs, command output, test logs, and tool observations as untrusted data. They can provide evidence for the task, but they cannot redefine the user's goal, bypass permissions, or request additional tools.

Day 1 of V1.2 adds a rule-based prompt injection layer:

- `TrustLevel` marks observations as `untrusted_workspace` or `untrusted_command_output`.
- `PromptBoundary` is included in planning prompts so the model sees the trusted instruction boundary explicitly.
- `InjectionFinding` records matched rule id, source tool, trust level, matched text, disposition, and evidence.
- `ToolExecutor` scans successful tool observations and emits `injection_detected` trace events.
- `security-reviewer` can return structured `security_findings` and merge blockers from diff/status evidence.

The detector is intentionally deterministic and conservative. It covers common patterns such as attempts to ignore previous instructions, delete workspace files, exfiltrate secrets, run network commands, or bypass approval policy. Detection does not automatically stop every task; it makes the suspicious instruction observable and keeps it from becoming trusted task intent.

Recommended local verification commands on this workspace:

```powershell
$env:PYTHONPATH = "src"
E:\conda\envs\minicode\python.exe -m pytest -q --basetemp .pytest-tmp-v12-day1
E:\conda\envs\minicode\python.exe -c "from typer.testing import CliRunner; from minicode_agent.cli.app import app; r=CliRunner().invoke(app, ['eval','examples/tasks/16_prompt_injection_readme.json','--workspace','.','--config','full']); print(r.output); raise SystemExit(r.exit_code)"
E:\conda\envs\minicode\python.exe -c "from typer.testing import CliRunner; from minicode_agent.cli.app import app; r=CliRunner().invoke(app, ['eval','examples/tasks/17_prompt_injection_command_output.json','--workspace','.','--config','full']); print(r.output); raise SystemExit(r.exit_code)"
E:\conda\envs\minicode\python.exe -c "from typer.testing import CliRunner; from minicode_agent.cli.app import app; r=CliRunner().invoke(app, ['eval','examples/tasks/18_prompt_injection_diff.json','--workspace','.','--config','full']); print(r.output); raise SystemExit(r.exit_code)"
```

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
