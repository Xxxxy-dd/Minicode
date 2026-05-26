from minicode_agent.tools.base import BaseTool, ToolError
from minicode_agent.tools.patch import ApplyPatchTool
from minicode_agent.tools.quality import RunFormatterTool, RunLinterTool
from minicode_agent.tools.readonly import GitDiffTool, GitStatusTool, ListFilesTool, ReadFileTool, SearchCodeTool
from minicode_agent.tools.repo import InspectRepoTool
from minicode_agent.tools.shell import RunShellTool, RunTestsTool
from minicode_agent.tools.subagent import SpawnSubagentTool
from minicode_agent.tools.write import AppendFileTool, CreateFileTool, DeleteFileTool, EditFileTool, WriteFileTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        name = tool.spec.name
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> BaseTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolError(f"Unknown tool: {name}") from exc

    def list(self) -> list[BaseTool]:
        return [self._tools[name] for name in sorted(self._tools)]


def create_default_registry(include_subagents: bool = True) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ListFilesTool())
    registry.register(ReadFileTool())
    registry.register(SearchCodeTool())
    registry.register(WriteFileTool())
    registry.register(AppendFileTool())
    registry.register(CreateFileTool())
    registry.register(DeleteFileTool())
    registry.register(EditFileTool())
    registry.register(RunShellTool())
    registry.register(RunTestsTool())
    registry.register(GitStatusTool())
    registry.register(GitDiffTool())
    registry.register(InspectRepoTool())
    registry.register(ApplyPatchTool())
    registry.register(RunFormatterTool())
    registry.register(RunLinterTool())
    if include_subagents:
        registry.register(SpawnSubagentTool())
    return registry
