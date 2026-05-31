from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from minicode_agent.harness.types import AssertionResult, HarnessTask
from minicode_agent.trace import TraceEvent


def evaluate_assertions(task: HarnessTask, workspace: Path, trace_events: list[TraceEvent]) -> list[AssertionResult]:
    results: list[AssertionResult] = []
    events_by_type: dict[str, list[TraceEvent]] = {}
    for event in trace_events:
        events_by_type.setdefault(event.event_type, []).append(event)

    for assertion in task.trace_assertions:
        matches = [
            event
            for event in events_by_type.get(assertion.event_type, [])
            if payload_matches(event.payload, assertion.payload_contains)
        ]
        results.append(
            AssertionResult(
                kind="trace",
                target=assertion.event_type,
                passed=len(matches) >= assertion.min_count,
                detail=f"matched {len(matches)} event(s), expected at least {assertion.min_count}",
            )
        )

    requested_tools = [
        str(event.payload.get("tool"))
        for event in events_by_type.get("tool_requested", [])
        if event.payload.get("tool")
    ]
    for assertion in task.forbidden_tools:
        used = assertion.tool in requested_tools
        results.append(
            AssertionResult(
                kind="forbidden_tool",
                target=assertion.tool,
                passed=not used,
                detail="tool was not requested" if not used else "tool was requested",
            )
        )

    changed_paths = changed_files(workspace)
    for assertion in task.file_diff_assertions:
        changed = assertion.path in changed_paths
        results.append(
            AssertionResult(
                kind="file_diff",
                target=assertion.path,
                passed=changed == assertion.should_change,
                detail=f"changed={changed}, expected={assertion.should_change}",
            )
        )

    role_events = events_by_type.get("team_role_completed", [])
    for assertion in task.team_assertions:
        role_matches = [event for event in role_events if event.payload.get("role") == assertion.role]
        has_evidence = any(event.payload.get("evidence_refs") for event in role_matches)
        has_blocker = any(event.payload.get("merge_blockers") for event in role_matches)
        passed = bool(role_matches)
        if assertion.require_evidence:
            passed = passed and has_evidence
        if assertion.require_merge_blocker:
            passed = passed and has_blocker
        results.append(
            AssertionResult(
                kind="team",
                target=assertion.role,
                passed=passed,
                detail=f"roles={len(role_matches)}, evidence={has_evidence}, merge_blocker={has_blocker}",
            )
        )

    return results


def payload_matches(payload: dict[str, Any], expected: dict[str, str]) -> bool:
    for key, value in expected.items():
        current: Any = payload
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
        if str(current) != value:
            return False
    return True


def changed_files(workspace: Path) -> set[str]:
    completed = subprocess.run(
        ["git", "status", "--short"],
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        return set()
    return {line[3:].replace("\\", "/") for line in completed.stdout.splitlines() if len(line) > 3}
