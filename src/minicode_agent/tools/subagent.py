import json
from typing import Any

from pydantic import ValidationError

from minicode_agent.subagents import AgentTeam, SubagentRequest
from minicode_agent.tools.base import BaseTool, ToolError
from minicode_agent.tools.types import PermissionMode, RiskLevel, ToolContext, ToolSpec


class SpawnSubagentTool(BaseTool):
    spec = ToolSpec(
        name="spawn_subagent",
        description="Run a bounded explorer, reviewer, tester, security-reviewer, or implementer role and return structured findings.",
        risk_level=RiskLevel.SAFE,
        permission=PermissionMode.ALLOW,
        counts_as_subagent_call=True,
        input_schema={
            "role": "explorer | reviewer | tester | security-reviewer | implementer",
            "task": "Subtask for the subagent.",
            "max_steps": "Optional max role tool calls, capped at 5.",
            "path": "Optional relative file path for explorer reads.",
            "pattern": "Optional search pattern for explorer search.",
        },
    )

    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        try:
            request = SubagentRequest.model_validate(arguments)
        except ValidationError as exc:
            raise ToolError(str(exc)) from exc
        team = AgentTeam(
            context.resolved_workspace,
            trace_store=context.trace_store,
            parent_run_id=context.run_id,
        ).run(request.task, [request])
        result = team.results[0] if team.results else None
        if result is None:
            raise ToolError("team produced no role results")
        metadata = result.model_dump()
        metadata.update(
            {
                "team_id": team.team_id,
                "team_workspace_plan": team.workspace_plan.model_dump(),
                "role": result.role.value,
                "max_steps": request.max_steps,
                "allowed_tools": result.allowed_tools,
                "denied_tools": result.denied_tools,
            }
        )
        return json.dumps(result.model_dump(), ensure_ascii=False, indent=2), metadata
