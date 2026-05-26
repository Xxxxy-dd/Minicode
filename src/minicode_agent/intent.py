import re
from dataclasses import dataclass

from minicode_agent.tools.registry import ToolRegistry
from minicode_agent.tools.types import PermissionMode, ToolIntent


@dataclass(frozen=True)
class IntentGuardRule:
    tokens: tuple[str, ...]
    allowed_intents: tuple[ToolIntent, ...]
    reason: str

    def matches(self, text: str) -> bool:
        normalized = text.lower()
        return any(token in normalized for token in self.tokens)


INTENT_GUARD_RULES = (
    IntentGuardRule(
        tokens=("追加", "append", "continue", "继续写", "继续"),
        allowed_intents=(ToolIntent.FILE_APPEND,),
        reason="append intent should use append_file",
    ),
    IntentGuardRule(
        tokens=("覆盖", "overwrite", "replace", "重写", "改写"),
        allowed_intents=(ToolIntent.FILE_OVERWRITE, ToolIntent.FILE_EDIT),
        reason="overwrite intent should use write_file or edit_file",
    ),
    IntentGuardRule(
        tokens=("删除", "delete", "remove", "移除"),
        allowed_intents=(ToolIntent.FILE_DELETE,),
        reason="delete intent should use delete_file",
    ),
    IntentGuardRule(
        tokens=("新建", "创建", "create", "new file"),
        allowed_intents=(ToolIntent.FILE_CREATE,),
        reason="create intent should use create_file or write_file",
    ),
)


TOOL_INTENT_PHRASES = (
    "read file",
    "read the file",
    "read readme",
    "write file",
    "edit file",
    "modify file",
    "update file",
    "run command",
    "run shell",
    "run test",
    "run tests",
    "search code",
    "search file",
    "search for",
    "grep for",
    "find in code",
    "inspect file",
    "inspect project",
    "inspect workspace",
    "list files",
    "list project files",
    "call tool",
    "use tool",
    "execute command",
    "open file",
    "读取文件",
    "阅读文件",
    "读取 readme",
    "阅读 readme",
    "写入文件",
    "编辑文件",
    "修改文件",
    "运行命令",
    "执行命令",
    "运行测试",
    "搜索代码",
    "搜索文件",
    "搜索项目",
    "查找代码",
    "查找文件",
    "查看文件",
    "检查项目",
    "检查工作区",
    "列出文件",
    "列出项目文件",
    "调用工具",
    "使用工具",
)


DIRECT_CHAT_PATTERNS = (
    "你是谁",
    "你是什么",
    "你有什么工具",
    "你能做什么",
    "你会什么",
    "你能帮我什么",
    "你好",
    "您好",
    "说中文",
    "用中文",
    "你有啥工具",
    "有什么工具",
    "tool list",
    "what tools do you have",
    "what are your tools",
    "what can you do",
    "who are you",
    "capabilities",
    "capability",
)


def is_tool_intent_text(value: str) -> bool:
    normalized = value.strip().lower()
    if any(phrase in normalized for phrase in TOOL_INTENT_PHRASES):
        return True
    return bool(re.search(r"\b(read|write|edit|modify|update|inspect|open)\s+[\w./\\-]+\.\w+\b", normalized))


def tool_intent_mismatch_reason(goal: str, tool_intents: set[ToolIntent]) -> str | None:
    if not tool_intents:
        return None
    for rule in INTENT_GUARD_RULES:
        if rule.matches(goal) and not tool_intents.intersection(rule.allowed_intents):
            return rule.reason
    return None


def is_direct_chat_query(task: str) -> bool:
    normalized = task.strip().lower()
    if not normalized:
        return False
    return any(pattern in normalized for pattern in DIRECT_CHAT_PATTERNS)


def direct_chat_reply(task: str, tool_registry: ToolRegistry | None = None) -> str:
    normalized = task.strip()
    lowered = normalized.lower()
    if any(phrase in normalized for phrase in ("你是谁", "你是什么", "who are you")):
        return "我是 MiniCode，一个本地编码代理，可以帮你看代码、改代码、跑测试、查问题和整理项目。"
    if any(phrase in normalized for phrase in ("你有什么工具", "你能做什么", "what can you do", "help")):
        return _capability_reply(tool_registry)
    if "tool" in lowered or "工具" in normalized:
        return _capability_reply(tool_registry)
    return "我是 MiniCode，可以帮你处理代码、测试、审查和项目整理。"


def _capability_reply(tool_registry: ToolRegistry | None) -> str:
    if tool_registry is None:
        return "我可以读写文件、搜索代码、运行命令、跑测试、查看 git 状态和差异，也可以按需调用子代理。"

    tools = tool_registry.list()
    safe_tools = [tool.spec.name for tool in tools if tool.spec.permission == PermissionMode.ALLOW]
    approval_tools = [tool.spec.name for tool in tools if tool.spec.permission == PermissionMode.ASK]
    parts = [
        "我可以基于当前工具注册表工作：",
        f"无需审批的工具包括 {', '.join(safe_tools) or '无'}；",
        f"需要审批的工具包括 {', '.join(approval_tools) or '无'}。",
    ]
    return "".join(parts)
