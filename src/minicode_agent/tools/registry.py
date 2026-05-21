from minicode_agent.tools.base import BaseTool, ToolError
from minicode_agent.tools.readonly import GitDiffTool, GitStatusTool, ListFilesTool, ReadFileTool, SearchCodeTool
from minicode_agent.tools.shell import RunShellTool, RunTestsTool
from minicode_agent.tools.write import EditFileTool, WriteFileTool


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


def create_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ListFilesTool())
    registry.register(ReadFileTool())
    registry.register(SearchCodeTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(RunShellTool())
    registry.register(RunTestsTool())
    registry.register(GitStatusTool())
    registry.register(GitDiffTool())
    return registry
