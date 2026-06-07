# MiniCode Benchmark Examples

This directory contains lightweight benchmark assets for the MiniCode harness.

## Layout

- `tasks/`: JSON benchmark tasks loaded by `minicode eval`.
- `repos/mini_py/`: a tiny Python project used by most tasks.
- `repos/mini_py_buggy/`: a failing variant used by repair tasks.
- `skills/code-style/`: a minimal external skill loaded through `MINICODE_SKILL_PATHS`.

## Run

```bash
minicode eval examples/tasks
```

To try the external skill example:

```bash
MINICODE_SKILL_PATHS=examples/skills minicode skills route "cleanup this module" --workspace .
```

Each task points at a source workspace and provides a prompt. Harness copies the
workspace into `.minicode/eval_workspaces/<config>/<eval_id>/<task_id>` before
running the agent, so tasks do not mutate each other. Most tasks include a
success command so the harness can judge pass or fail automatically.

## Task Set

| Task | Category | Auto-judged | Expected |
| --- | --- | --- | --- |
| `fix_pytest_failure` | debugging | yes | fail |
| `add_small_feature` | feature | yes | pass |
| `fix_boundary_condition` | bugfix | yes | pass |
| `refactor_duplicate_code` | refactor | yes | pass |
| `update_docs` | docs | yes | pass |
| `fix_type_error` | typing | yes | pass |
| `fix_path_handling` | bugfix | yes | pass |
| `add_missing_tests` | testing | yes | pass |
| `simple_code_review` | review | no | analysis_only |
| `dangerous_command_block` | safety | no | analysis_only |
| `memory_reuse_hint` | memory | no | analysis_only |
| `compression_long_context` | context | no | analysis_only |
| `workspace_skill_route` | skills | no | analysis_only |
| `cli_release_polish` | cli | no | analysis_only |
| `agent_team_reviewer` | team | no | analysis_only |
| `prompt_injection_readme` | safety | no | analysis_only |
| `prompt_injection_command_output` | safety | no | analysis_only |
| `prompt_injection_diff` | safety | no | analysis_only |
| `worktree_clean_isolation` | team | no | analysis_only |
| `worktree_dirty_blocker` | team | no | analysis_only |
| `failure_memory_recall` | memory | no | analysis_only |
| `context_evidence_compression` | context | no | analysis_only |

Day 15 focuses on creating the task set. Day 16 uses this set for ablation
experiments across baseline, skill, memory, compression, and subagent configs.

V1.2 adds safety, team/worktree, memory, and context evidence demos. The generated Markdown report includes prompt-injection findings, team role evidence, worktree patch proposal status, memory recall refs, and context compression refs.
