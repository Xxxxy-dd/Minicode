import json
import re
from dataclasses import dataclass
from pathlib import Path

from minicode_agent.capabilities import build_capability_profile, capability_reply
from minicode_agent.models import ModelClient, ModelMessage
from minicode_agent.tools.registry import ToolRegistry
from minicode_agent.tools.types import ToolIntent


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
    "工具",
    "skills",
    "skill",
    "命令",
    "说中文",
    "用中文",
    "你有啥工具",
    "有什么工具",
    "what skills",
    "which skills",
    "your skills",
    "slash commands",
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


def direct_chat_reply(
    task: str,
    tool_registry: ToolRegistry | None = None,
    model_name: str | None = None,
    preferred_language: str | None = None,
    recent_user_messages: list[str] | None = None,
    user_preferences: list[str] | None = None,
) -> str:
    return fallback_direct_chat_reply(
        task,
        tool_registry,
        model_name=model_name,
        preferred_language=preferred_language,
        recent_user_messages=recent_user_messages,
        user_preferences=user_preferences,
    )


def model_direct_chat_reply(
    task: str,
    model_client: ModelClient,
    *,
    workspace: Path | None = None,
    tool_registry: ToolRegistry | None = None,
    model_name: str | None = None,
    preferred_language: str | None = None,
    recent_user_messages: list[str] | None = None,
    user_preferences: list[str] | None = None,
) -> str:
    response_language = response_language_from_preferences(user_preferences or [], preferred_language, task)
    response = model_client.complete(
        build_direct_chat_messages(
            task,
            workspace=workspace,
            tool_registry=tool_registry,
            model_name=model_name,
            preferred_language=preferred_language,
            recent_user_messages=recent_user_messages,
            user_preferences=user_preferences,
        )
    )
    return ensure_response_language(
        response.content.strip(),
        response_language,
        model_client,
        user_message=task,
    )


def build_direct_chat_messages(
    task: str,
    *,
    workspace: Path | None = None,
    tool_registry: ToolRegistry | None = None,
    model_name: str | None = None,
    preferred_language: str | None = None,
    recent_user_messages: list[str] | None = None,
    user_preferences: list[str] | None = None,
) -> list[ModelMessage]:
    response_language = response_language_from_preferences(user_preferences or [], preferred_language, task)
    system = (
        "You are MiniCode's conversational agent shell. "
        "Answer the user naturally in response_language unless the user explicitly requests another language in the current message. "
        "You must ground identity, model, tools, skills, commands, and capability answers in the provided runtime_context. "
        "Do not claim abilities that are not present in capability_profile. "
        "Do not emit JSON."
    )
    payload = {
        "user_message": task,
        "runtime_context": {
            "model_name": model_name or "no-model",
            "preferred_language": preferred_language,
            "response_language": response_language,
            "recent_user_messages": recent_user_messages or [],
            "user_preferences": user_preferences or [],
            "capability_profile": build_capability_profile(workspace=workspace, tool_registry=tool_registry),
        },
    }
    return [
        ModelMessage(role="system", content=system),
        ModelMessage(role="user", content=json.dumps(payload, ensure_ascii=False, indent=2)),
    ]


def fallback_direct_chat_reply(
    task: str,
    tool_registry: ToolRegistry | None = None,
    *,
    model_name: str | None = None,
    preferred_language: str | None = None,
    recent_user_messages: list[str] | None = None,
    user_preferences: list[str] | None = None,
) -> str:
    normalized = task.strip()
    reply_in_zh = preferred_language != "en" and (preferred_language == "zh" or contains_cjk(normalized))
    profile = build_capability_profile(tool_registry=tool_registry)
    subject = capability_subject(normalized)
    if subject:
        return capability_reply(profile, subject=subject, chinese=reply_in_zh)
    return capability_reply(profile, subject="overview", chinese=reply_in_zh)


def capability_subject(text: str) -> str | None:
    lowered = text.lower()
    if any(token in lowered for token in ("skills", "skill")) or "技能" in text:
        return "skills"
    if "命令" in text or "command" in lowered:
        return "commands"
    if any(token in lowered for token in ("tool", "capabilit", "help")) or any(token in text for token in ("工具", "能做什么", "会什么", "能帮我什么")):
        return "tools"
    return None


def contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def response_language_from_preferences(preferences: list[str], preferred_language: str | None, task: str) -> str:
    if preferred_language == "zh":
        return "Chinese"
    if preferred_language == "en":
        return "English"
    for preference in preferences:
        lowered = preference.casefold()
        if "chinese" in lowered or "中文" in preference:
            return "Chinese"
        if "english" in lowered or "英文" in preference:
            return "English"
    return "Chinese" if contains_cjk(task) else "English"


def response_matches_language(text: str, response_language: str) -> bool:
    if not text.strip():
        return True
    if response_language == "Chinese":
        return contains_cjk(text)
    return True


def ensure_response_language(
    text: str,
    response_language: str,
    model_client: ModelClient | None = None,
    *,
    user_message: str = "",
) -> str:
    if response_matches_language(text, response_language):
        return text
    if model_client is None:
        return text
    rewrite_messages = [
        ModelMessage(
            role="system",
            content=(
                "Rewrite the assistant response into the requested response_language. "
                "Preserve the exact meaning and any technical identifiers. "
                "Do not add new claims, explanations, markdown fences, or metadata."
            ),
        ),
        ModelMessage(
            role="user",
            content=json.dumps(
                {
                    "response_language": response_language,
                    "user_message": user_message,
                    "assistant_response": text,
                },
                ensure_ascii=False,
                indent=2,
            ),
        ),
    ]
    rewritten = model_client.complete(rewrite_messages).content.strip()
    if response_matches_language(rewritten, response_language):
        return rewritten
    if response_language == "Chinese":
        return "模型返回的回答语言与当前偏好不一致；我已要求模型按偏好重写，但结果仍未符合。请重试或检查模型配置。"
    return rewritten or text
