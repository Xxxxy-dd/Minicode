import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from minicode_agent.skills.registry import SkillDefinition, SkillRegistry
from minicode_agent.models.client import ModelMessage


class SkillRerankModelClient(Protocol):
    def complete(self, messages: list[ModelMessage]):
        """Return an object with a string content attribute."""


@dataclass(frozen=True)
class SkillRouteWeights:
    name: int = 4
    aliases: int = 4
    tags: int = 3
    applies_to: int = 3
    examples: int = 2
    description: int = 1


@dataclass(frozen=True)
class SkillRouteCandidate:
    name: str
    score: int
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SkillRouteResult:
    selected: list[str]
    candidates: list[SkillRouteCandidate]
    rerank_used: bool = False
    rerank_fallback: bool = False
    rerank_reason: str | None = None
    rerank_skipped_reason: str | None = None
    no_match_reason: str | None = None

    @property
    def reasons(self) -> dict[str, list[str]]:
        return {candidate.name: candidate.reasons for candidate in self.candidates if candidate.name in self.selected}

    @property
    def unselected_reasons(self) -> dict[str, list[str]]:
        return {candidate.name: candidate.reasons for candidate in self.candidates if candidate.name not in self.selected}

    @property
    def debug_summary(self) -> str:
        if self.candidates:
            return f"{len(self.candidates)} candidate(s), selected: {', '.join(self.selected) or '(none)'}"
        return self.no_match_reason or "No skill metadata matched the task text."


@dataclass(frozen=True)
class SkillRerankDecision:
    selected: list[str]
    reason: str | None
    fallback_used: bool = False


class SkillRouter:
    """Deterministic metadata-based skill router."""

    def __init__(
        self,
        registry: SkillRegistry | None = None,
        model_client: SkillRerankModelClient | None = None,
        enable_llm_rerank: bool = False,
        top_k: int = 2,
        weights: SkillRouteWeights | None = None,
        min_score_gap: int = 5,
        rerank_top_n: int = 4,
    ) -> None:
        self.registry = registry or SkillRegistry()
        self.model_client = model_client
        self.enable_llm_rerank = enable_llm_rerank
        self.top_k = top_k
        self.weights = weights or SkillRouteWeights()
        self.min_score_gap = min_score_gap
        self.rerank_top_n = rerank_top_n

    def route(self, task: str) -> SkillRouteResult:
        tokens = tokenize(task)
        direct_skills = direct_skill_aliases(task)
        candidates = [score_skill(skill, tokens, task, self.weights, direct_skills) for skill in self.registry.list()]
        candidates = [candidate for candidate in candidates if candidate.score > 0]
        candidates.sort(key=lambda candidate: (-candidate.score, candidate.name))
        selected = select_top_candidates(candidates, self.top_k, self.min_score_gap)
        rerank_used = False
        rerank_fallback = False
        rerank_reason: str | None = None
        rerank_skipped_reason: str | None = None
        if self.enable_llm_rerank and self.model_client and candidates:
            decision = self._rerank(task, candidates)
            rerank_used = True
            rerank_fallback = decision.fallback_used
            rerank_reason = decision.reason
            if decision.selected:
                selected = decision.selected[: self.top_k]
        elif self.enable_llm_rerank and candidates:
            rerank_skipped_reason = "LLM rerank requested but no model client is configured."
        elif self.enable_llm_rerank:
            rerank_skipped_reason = "LLM rerank requested but there are no skill candidates to rerank."
        return SkillRouteResult(
            selected=selected,
            candidates=candidates,
            rerank_used=rerank_used,
            rerank_fallback=rerank_fallback,
            rerank_reason=rerank_reason,
            rerank_skipped_reason=rerank_skipped_reason,
            no_match_reason=None if candidates else "No skill metadata matched the task text.",
        )

    def _rerank(self, task: str, candidates: list[SkillRouteCandidate]) -> SkillRerankDecision:
        top_candidates = candidates[: self.rerank_top_n]
        messages = [
            ModelMessage(
                role="system",
                content=(
                    "You rerank MiniCode skill candidates. "
                    "Return only JSON with fields selected_skills and reason. "
                    "selected_skills must be an ordered list of candidate skill names. "
                    "Only choose from the provided candidates."
                ),
            ),
            ModelMessage(
                role="user",
                content=json.dumps(
                    {
                        "task": task,
                        "candidates": [
                            {
                                "name": candidate.name,
                                "score": candidate.score,
                                "reasons": candidate.reasons,
                            }
                            for candidate in top_candidates
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            ),
        ]
        try:
            response = self.model_client.complete(messages)
            decision = parse_skill_rerank_response(response.content, [candidate.name for candidate in top_candidates])
        except Exception as exc:
            return SkillRerankDecision(
                selected=[candidate.name for candidate in top_candidates[: self.top_k]],
                reason=str(exc),
                fallback_used=True,
            )

        selected = [name for name in decision.selected if name in {candidate.name for candidate in candidates}]
        if not selected:
            return SkillRerankDecision(
                selected=[candidate.name for candidate in top_candidates[: self.top_k]],
                reason="LLM rerank returned no valid candidate order.",
                fallback_used=True,
            )
        return SkillRerankDecision(selected=selected, reason=decision.reason, fallback_used=False)


def score_skill(
    skill: SkillDefinition,
    task_tokens: set[str],
    task: str,
    weights: SkillRouteWeights,
    direct_skills: set[str] | None = None,
) -> SkillRouteCandidate:
    score = 0
    reasons: list[str] = []
    direct_skills = direct_skills or set()
    if skill.name in direct_skills:
        score += weights.aliases * 3
        reasons.append("direct alias matched task text")
    fields = {
        "name": [skill.name],
        "aliases": skill.metadata.aliases,
        "tags": skill.metadata.tags,
        "applies_to": skill.metadata.applies_to,
        "examples": skill.metadata.examples,
        "description": [skill.metadata.description],
    }
    for field_name, values in fields.items():
        for value in values:
            matched = sorted(tokenize(value) & task_tokens)
            phrase_match = value.lower() in task.lower()
            if matched or phrase_match:
                field_weight = getattr(weights, field_name)
                amount = field_weight * max(1, len(matched))
                if phrase_match:
                    amount += field_weight
                score += amount
                reasons.append(f"{field_name} matched {', '.join(matched) or value}")
    return SkillRouteCandidate(name=skill.name, score=score, reasons=reasons)


def select_top_candidates(candidates: list[SkillRouteCandidate], top_k: int, min_score_gap: int) -> list[str]:
    if not candidates:
        return []
    selected = [candidates[0].name]
    for candidate in candidates[1:top_k]:
        if candidates[0].score - candidate.score <= min_score_gap:
            selected.append(candidate.name)
    return selected


def tokenize(text: str) -> set[str]:
    stop_words = {
        "and",
        "for",
        "the",
        "this",
        "that",
        "with",
        "project",
        "code",
        "task",
        "skill",
        "use",
        "when",
    }
    words = {
        word
        for word in re.findall(r"[a-zA-Z0-9]+", text.lower())
        if len(word) >= 3 and word not in stop_words
    }
    aliases = {
        "pytest": {"test", "tests", "failing", "failure"},
        "diff": {"review", "audit"},
        "traceback": {"bug", "error", "failure"},
    }
    expanded = set(words)
    for word in words:
        expanded.update(aliases.get(word, set()))
    return expanded


def direct_skill_aliases(text: str) -> set[str]:
    mapping = {
        "修复": {"debugging"},
        "报错": {"debugging"},
        "错误": {"debugging"},
        "失败": {"debugging"},
        "测试": {"debugging", "test-writing"},
        "单测": {"test-writing"},
        "覆盖": {"test-writing"},
        "审查": {"code-review"},
        "评审": {"code-review"},
        "检查diff": {"code-review"},
        "代码审查": {"code-review"},
    }
    matched: set[str] = set()
    normalized = text.replace(" ", "").lower()
    for phrase, skills in mapping.items():
        if phrase in normalized:
            matched.update(skills)
    return matched


@dataclass(frozen=True)
class ParsedSkillRerankResponse:
    selected: list[str]
    reason: str | None


def parse_skill_rerank_response(content: str, candidate_names: list[str]) -> ParsedSkillRerankResponse:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM skill rerank response must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("LLM skill rerank response must be a JSON object.")

    selected = payload.get("selected_skills", [])
    if not isinstance(selected, list):
        raise ValueError("LLM skill rerank response field 'selected_skills' must be a list.")
    if not all(isinstance(item, str) and item.strip() for item in selected):
        raise ValueError("LLM skill rerank response field 'selected_skills' must contain only strings.")
    filtered = [name for name in selected if name in candidate_names]
    reason = payload.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("LLM skill rerank response field 'reason' must be a string or null.")
    return ParsedSkillRerankResponse(selected=filtered, reason=reason)
