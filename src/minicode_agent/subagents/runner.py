from __future__ import annotations

from pathlib import Path
import re

from minicode_agent.subagents.types import SubagentRequest, SubagentResult, SubagentRole
from minicode_agent.tools.readonly import GitDiffTool, GitStatusTool, ListFilesTool, ReadFileTool, SearchCodeTool
from minicode_agent.tools.types import ToolContext
from minicode_agent.trace import TraceStore


ROLE_TOOLS = {
    SubagentRole.EXPLORER: {"list_files", "read_file", "search_code", "git_status"},
    SubagentRole.REVIEWER: {"git_diff", "read_file", "search_code", "git_status"},
}
ALL_SUBAGENT_TOOL_NAMES = {"git_diff", "git_status", "list_files", "read_file", "search_code", "spawn_subagent"}


class SubagentRunner:
    """Runs a bounded, read-only subagent through the normal tool executor."""

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
        allowed_tools = sorted(ROLE_TOOLS[request.role])
        denied_tools = sorted(ALL_SUBAGENT_TOOL_NAMES - set(allowed_tools))
        self._trace(
            "subagent_started",
            {
                **request.model_dump(),
                "allowed_tools": allowed_tools,
                "denied_tools": denied_tools,
            },
        )
        from minicode_agent.tools.executor import ToolExecutor

        registry = create_limited_registry(ROLE_TOOLS[request.role])
        executor = ToolExecutor(registry, trace_store=self.trace_store, run_id=self.parent_run_id)
        context = ToolContext(workspace=self.workspace)
        findings: list[str] = []
        tool_calls = 0
        ok = True

        for tool_name, arguments in planned_actions(request):
            if tool_calls >= request.max_steps:
                break
            observation = executor.execute(tool_name, context, arguments)
            tool_calls += 1
            if observation.ok:
                findings.append(format_finding(tool_name, observation.output))
            else:
                ok = False
                findings.append(f"{tool_name} failed: {observation.error}")

        stopped_reason = "max_steps" if tool_calls >= request.max_steps else "planned_actions_exhausted"
        review = reviewer_review(findings) if request.role == SubagentRole.REVIEWER else {}
        result = SubagentResult(
            role=request.role,
            task=request.task,
            ok=ok,
            summary=summarize_findings(request.role, findings),
            findings=findings,
            allowed_tools=allowed_tools,
            denied_tools=denied_tools,
            changed_files=review.get("changed_files", []),
            risks=review.get("risks", []),
            test_suggestions=review.get("test_suggestions", []),
            tool_calls=tool_calls,
            stopped_reason=stopped_reason,
        )
        self._trace("subagent_finished", result.model_dump())
        return result

    def _trace(self, event_type: str, payload: dict) -> None:
        if self.trace_store is None or self.parent_run_id is None:
            return
        self.trace_store.append(self.parent_run_id, event_type, payload)


def create_limited_registry(allowed_tools: set[str]):
    from minicode_agent.tools.registry import ToolRegistry

    available = {
        "list_files": ListFilesTool(),
        "read_file": ReadFileTool(),
        "search_code": SearchCodeTool(),
        "git_status": GitStatusTool(),
        "git_diff": GitDiffTool(),
    }
    registry = ToolRegistry()
    for name in sorted(allowed_tools):
        registry.register(available[name])
    return registry


def planned_actions(request: SubagentRequest) -> list[tuple[str, dict]]:
    if request.role == SubagentRole.REVIEWER:
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
    return f"{role.value} completed {len(findings)} read-only tool call(s)."


def reviewer_review(findings: list[str]) -> dict[str, list[str]]:
    text = "\n".join(findings)
    changed_files = sorted(set(re.findall(r"\b(?:a|b)/([^\s]+)", text)))
    risks: list[str] = []
    if "TODO" in text or "pass" in text:
        risks.append("Diff contains placeholder-like content.")
    if not changed_files:
        risks.append("No changed files were detected from git diff output.")
    test_suggestions = ["Run the relevant test suite before merging."] if changed_files else []
    return {
        "changed_files": changed_files,
        "risks": risks,
        "test_suggestions": test_suggestions,
    }
