from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from minicode_agent.security import detect_injection, finding_payloads, trust_level_for_tool
from minicode_agent.tools.types import ToolObservation
from minicode_agent.subagents.types import SubagentRequest, SubagentResult, SubagentRole
from minicode_agent.tools.types import ToolContext
from minicode_agent.trace import TraceStore


class SubagentRunner:
    """Runs a bounded subagent through the normal tool executor."""

    def __init__(
        self,
        workspace: Path,
        trace_store: TraceStore | None = None,
        parent_run_id: str | None = None,
    ) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.trace_store = trace_store
        self.parent_run_id = parent_run_id

    def run(self, request: SubagentRequest) -> SubagentResult:
        registry = create_limited_registry(request.role)
        allowed_tools = [tool.spec.name for tool in registry.list()]
        denied_tools = denied_subagent_tools(request.role)
        self._trace(
            "subagent_started",
            {
                **request.model_dump(),
                "allowed_tools": allowed_tools,
                "denied_tools": denied_tools,
            },
        )
        from minicode_agent.tools.executor import ToolExecutor

        executor = ToolExecutor(registry, trace_store=self.trace_store, run_id=self.parent_run_id)
        context = ToolContext(workspace=self.workspace)
        findings: list[str] = []
        evidence: list[dict[str, Any]] = []
        observations: list[tuple[str, ToolObservation]] = []
        tool_calls = 0
        ok = True

        for tool_name, arguments in planned_actions(request):
            if tool_calls >= request.max_steps:
                break
            approved = request.role == SubagentRole.TESTER and tool_name == "run_tests"
            observation = executor.execute(tool_name, context, arguments, approved=approved)
            tool_calls += 1
            observations.append((tool_name, observation))
            evidence.append(
                {
                    "type": "tool_observation",
                    "tool": tool_name,
                    "ok": observation.ok,
                    "metadata": observation.metadata,
                    "output_chars": len(observation.output),
                    "error": observation.error,
                }
            )
            if observation.ok:
                findings.append(format_finding(tool_name, observation.output))
            else:
                ok = False
                findings.append(f"{tool_name} failed: {observation.error}")

        stopped_reason = "max_steps" if tool_calls >= request.max_steps else "planned_actions_exhausted"
        review = reviewer_review(observations) if request.role == SubagentRole.REVIEWER else {}
        security_review = security_reviewer_review(observations) if request.role == SubagentRole.SECURITY_REVIEWER else {}
        test_review = tester_review(observations) if request.role == SubagentRole.TESTER else {}
        review_blockers = review.get("merge_blockers", [])
        security_blockers = security_review.get("merge_blockers", [])
        test_blockers = test_review.get("merge_blockers", [])
        result = SubagentResult(
            role=request.role,
            task=request.task,
            ok=ok,
            summary=summarize_findings(request.role, findings),
            findings=findings,
            evidence=evidence,
            allowed_tools=allowed_tools,
            denied_tools=denied_tools,
            changed_files=review.get("changed_files", []),
            risks=review.get("risks", []),
            security_findings=security_review.get("security_findings", []),
            test_suggestions=review.get("test_suggestions", []),
            test_results=test_review.get("test_results", []),
            merge_blockers=sorted(set(review_blockers + security_blockers + test_blockers)),
            tool_calls=tool_calls,
            stopped_reason=stopped_reason,
        )
        self._trace("subagent_finished", result.model_dump())
        return result

    def _trace(self, event_type: str, payload: dict) -> None:
        if self.trace_store is None or self.parent_run_id is None:
            return
        self.trace_store.append(self.parent_run_id, event_type, payload)


def create_limited_registry(role: SubagentRole):
    from minicode_agent.tools.registry import ToolRegistry, create_default_registry

    registry = ToolRegistry()
    for tool in create_default_registry(include_subagents=False).list():
        if role.value in tool.spec.subagent_roles:
            registry.register(tool)
    return registry


def denied_subagent_tools(role: SubagentRole) -> list[str]:
    from minicode_agent.tools.registry import create_default_registry

    return [
        tool.spec.name
        for tool in create_default_registry(include_subagents=True).list()
        if role.value not in tool.spec.subagent_roles
    ]


def planned_actions(request: SubagentRequest) -> list[tuple[str, dict]]:
    if request.role == SubagentRole.IMPLEMENTER:
        return []
    if request.role == SubagentRole.TESTER:
        return [("run_tests", {"timeout_seconds": 60})]
    if request.role in {SubagentRole.REVIEWER, SubagentRole.SECURITY_REVIEWER}:
        return [("git_diff", {"stat": False}), ("git_status", {})]
    if request.path and request.pattern:
        return [("read_file", {"path": request.path}), ("search_code", {"pattern": request.pattern})]
    if request.pattern:
        return [("search_code", {"pattern": request.pattern})]
    if request.path:
        return [("read_file", {"path": request.path})]
    return [("list_files", {"max_files": 50}), ("git_status", {})]


def format_finding(tool_name: str, output: str) -> str:
    text = " ".join(output.split())
    if not text:
        text = "(empty output)"
    if len(text) > 500:
        text = text[:500] + " [truncated]"
    return f"{tool_name}: {text}"


def summarize_findings(role: SubagentRole, findings: list[str]) -> str:
    if not findings:
        return f"{role.value} completed without findings."
    return f"{role.value} completed {len(findings)} role tool call(s)."


def reviewer_review(observations: list[tuple[str, ToolObservation]]) -> dict[str, list[str]]:
    outputs = {tool_name: observation.output for tool_name, observation in observations if observation.ok}
    diff = outputs.get("git_diff", "")
    status = outputs.get("git_status", "")
    combined = "\n".join(outputs.values())
    changed_files = sorted(set(diff_changed_files(diff) | status_changed_files(status)))
    risks: list[str] = []
    merge_blockers: list[str] = []
    if re.search(r"\b(TODO|FIXME|pass)\b", diff):
        risks.append("Diff contains placeholder-like content.")
    if "rm -rf" in diff or "del /" in diff:
        risks.append("Diff or status references dangerous deletion commands.")
    if ".env" in combined or "PRIVATE KEY" in combined or "api_key" in combined.lower() or "token" in combined.lower():
        risks.append("Diff may expose sensitive file paths or secret-like content.")
    if re.search(r"deleted file mode|^D\s+", diff + "\n" + status, re.MULTILINE):
        risks.append("Diff or status includes deleted files.")
    if not changed_files:
        risks.append("No changed files were detected from git diff output.")
        merge_blockers.append("reviewer could not identify changed files from git diff")
    if changed_files and not re.search(r"\b(test|pytest|unittest|ruff|mypy)\b", diff, re.IGNORECASE):
        risks.append("Reviewer did not find test evidence in the collected diff/status.")
    test_suggestions = ["Run the relevant test suite before merging."] if changed_files else []
    return {
        "changed_files": changed_files,
        "risks": risks,
        "test_suggestions": test_suggestions,
        "merge_blockers": merge_blockers,
    }


def security_reviewer_review(observations: list[tuple[str, ToolObservation]]) -> dict[str, list]:
    security_findings: list[dict[str, Any]] = []
    merge_blockers: list[str] = []
    for tool_name, observation in observations:
        metadata_findings = observation.metadata.get("security_findings")
        if isinstance(metadata_findings, list):
            security_findings.extend(metadata_findings)
            continue
        trust_level = trust_level_for_tool(tool_name)
        security_findings.extend(
            finding_payloads(
                detect_injection(
                    observation.output,
                    source=tool_name,
                    trust_level=trust_level,
                    evidence={
                        "tool_call_id": observation.tool_call_id,
                        "tool": tool_name,
                        "metadata": observation.metadata,
                    },
                )
            )
        )
    if security_findings:
        merge_blockers.append("untrusted content contains possible prompt injection; review before applying changes")
    return {"security_findings": security_findings, "merge_blockers": merge_blockers}


def tester_review(observations: list[tuple[str, ToolObservation]]) -> dict[str, list]:
    test_results: list[dict[str, Any]] = []
    merge_blockers: list[str] = []
    for tool_name, observation in observations:
        if tool_name != "run_tests":
            continue
        exit_code = observation.metadata.get("exit_code")
        timed_out = bool(observation.metadata.get("timed_out"))
        passed = observation.ok and exit_code == 0 and not timed_out
        test_results.append(
            {
                "tool": tool_name,
                "ok": observation.ok,
                "passed": passed,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "output_chars": len(observation.output),
                "error": observation.error,
                "command": observation.metadata.get("command"),
            }
        )
        if not passed:
            merge_blockers.append("tester did not collect a passing test result")
    if not test_results:
        merge_blockers.append("tester did not run a test command")
    return {"test_results": test_results, "merge_blockers": merge_blockers}


def diff_changed_files(diff: str) -> set[str]:
    paths: set[str] = set()
    for match in re.finditer(r"^diff --git a/(.+?) b/(.+)$", diff, re.MULTILINE):
        paths.add(match.group(2).strip())
    for match in re.finditer(r"^\+\+\+ b/(.+)$", diff, re.MULTILINE):
        paths.add(match.group(1).strip())
    return paths


def status_changed_files(status: str) -> set[str]:
    paths: set[str] = set()
    for line in status.splitlines():
        if len(line) <= 3:
            continue
        if line[2] != " ":
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        paths.add(path.replace("\\", "/"))
    return paths
